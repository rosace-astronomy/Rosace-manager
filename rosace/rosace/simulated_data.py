# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Purge of the data produced with simulated equipment (INDI simulators, the
internal backend): sessions recorded with simulated = 1 in rosace.db, with
everything they left behind.

For each such session (except one still marked running and started less than
RUNNING_MAX_AGE_HOURS ago — it may really be in progress; an older "running"
row was left by a server stopped without CLOSE_SESSION, no session lasts
that long):
- files, in its session directory: those whose name contains the session id
  (session log, Demetra ZIP), plus those modified while it ran (started_at to
  closed_at + FILE_MARGIN_SECONDS; without closed_at, until the next session
  starts). Only one session runs at a time, so whatever was written in that
  directory meanwhile is its own (FITS, observation YAML, .obs.srl, dark
  medians...). A directory left empty is removed;
- rows, in one transaction: spectrum_flux and pointing_errors of its
  observations, its observations, the session itself.

purge_simulated(apply=False) only reports what would go; apply=True deletes.
Used by `python -m rosace.main --purge-simulated [--apply]`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DB_PATH

FILE_MARGIN_SECONDS = 120   # the session log is written right before closed_at is stamped
RUNNING_MAX_AGE_HOURS = 24  # a "running" session older than this is stale (no night lasts that long)


@dataclass
class SessionPurge:
    session_id: str
    started_at: str
    session_dir: str | None
    rows: dict[str, int] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)
    size_bytes: int = 0   # measured when found, so a report after deletion still shows it


def _session_files(session_dir: str | None, session_id: str, start: float, end: float) -> list[Path]:
    if not session_dir or not Path(session_dir).is_dir():
        return []
    files = []
    for f in sorted(Path(session_dir).iterdir()):
        if f.is_file() and (session_id in f.name or start <= f.stat().st_mtime <= end):
            files.append(f)
    return files


def find_simulated_sessions(db_path: Path | None = None, now: datetime | None = None) -> list[SessionPurge]:
    """The simulated sessions, with their rows and files — except a session
    still marked running and started less than RUNNING_MAX_AGE_HOURS ago
    (it may really be in progress). `now` defaults to the current time."""
    now = now or datetime.now(timezone.utc)
    db = sqlite3.connect(f"file:{db_path or DB_PATH}?mode=ro", uri=True)
    try:
        sessions = db.execute(
            "SELECT id, started_at, closed_at, status, session_dir, simulated FROM sessions ORDER BY started_at"
        ).fetchall()
        purges = []
        for i, (sid, started, closed, status, session_dir, simulated) in enumerate(sessions):
            if simulated != 1:
                continue
            if status == "running" and now - datetime.fromisoformat(started) < timedelta(hours=RUNNING_MAX_AGE_HOURS):
                continue
            start = datetime.fromisoformat(started).timestamp()
            if closed:
                end = datetime.fromisoformat(closed).timestamp() + FILE_MARGIN_SECONDS
            elif i + 1 < len(sessions):
                end = datetime.fromisoformat(sessions[i + 1][1]).timestamp()
            else:
                end = float("inf")
            purge = SessionPurge(sid, started, session_dir)
            obs = "(SELECT id FROM observations WHERE session_id = ?)"
            purge.rows = {
                "spectrum_flux": db.execute(f"SELECT COUNT(*) FROM spectrum_flux WHERE observation_id IN {obs}", (sid,)).fetchone()[0],
                "pointing_errors": db.execute(f"SELECT COUNT(*) FROM pointing_errors WHERE observation_id IN {obs}", (sid,)).fetchone()[0],
                "observations": db.execute("SELECT COUNT(*) FROM observations WHERE session_id = ?", (sid,)).fetchone()[0],
                "sessions": 1,
            }
            purge.files = _session_files(session_dir, sid, start, end)
            purge.size_bytes = sum(f.stat().st_size for f in purge.files)
            purges.append(purge)
        return purges
    finally:
        db.close()


def _delete_rows(db_path: Path, session_ids: list[str]) -> None:
    db = sqlite3.connect(db_path)
    try:
        with db:   # one transaction
            # Time budget and document tables: absent from a database the server never
            # initialised since they were added.
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            for sid in session_ids:
                row = db.execute("SELECT session_dir FROM sessions WHERE id = ?", (sid,)).fetchone()
                obs = "(SELECT id FROM observations WHERE session_id = ?)"
                db.execute(f"DELETE FROM spectrum_flux WHERE observation_id IN {obs}", (sid,))
                db.execute(f"DELETE FROM pointing_errors WHERE observation_id IN {obs}", (sid,))
                if "observation_documents" in tables:
                    db.execute(f"DELETE FROM observation_documents WHERE observation_id IN {obs}", (sid,))
                db.execute("DELETE FROM observations WHERE session_id = ?", (sid,))
                db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
                if "time_segments" in tables:
                    db.execute("DELETE FROM time_segments WHERE session_id = ?", (sid,))
                # The night's budget goes with its last session.
                if "night_time_budget" in tables and row and row[0]:
                    left = db.execute("SELECT COUNT(*) FROM sessions WHERE session_dir = ?", (row[0],)).fetchone()[0]
                    if not left:
                        db.execute("DELETE FROM night_time_budget WHERE night = ?", (Path(row[0]).name[:10],))
    finally:
        db.close()


def purge_simulated(apply: bool = False, db_path: Path | None = None) -> list[SessionPurge]:
    """Finds the simulated sessions and, with apply=True, deletes their rows
    (one transaction) then their files, and removes the directories left
    empty. Returns what was (or would be) purged."""
    db_path = db_path or DB_PATH
    purges = find_simulated_sessions(db_path)
    if not apply or not purges:
        return purges
    _delete_rows(db_path, [p.session_id for p in purges])
    directories = set()
    for purge in purges:
        for f in purge.files:
            if f.exists():
                f.unlink()
        if purge.session_dir:
            directories.add(Path(purge.session_dir))
    for directory in directories:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    return purges


def report(purges: list[SessionPurge]) -> str:
    """Human-readable summary: one line per session, then the totals."""
    lines = []
    totals = {"spectrum_flux": 0, "pointing_errors": 0, "observations": 0, "sessions": 0}
    files = size = 0
    for p in purges:
        for table, count in p.rows.items():
            totals[table] += count
        files += len(p.files)
        size += p.size_bytes
        lines.append(f"  {p.session_id}  {p.started_at[:16]}  {Path(p.session_dir).name if p.session_dir else '-'}"
                     f"  observations {p.rows['observations']}, files {len(p.files)} ({p.size_bytes / 1e6:.0f} MB)")
    lines.append(f"Total: {totals['sessions']} sessions, {totals['observations']} observations, "
                 f"{totals['pointing_errors']} pointing errors, {totals['spectrum_flux']} spectrum flux rows, "
                 f"{files} files ({size / 1e6:.0f} MB)")
    return "\n".join(lines)
