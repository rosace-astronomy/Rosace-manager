# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Purge of the observations of one night whose acquisition failed
(sequencer_status = 'error' in rosace.db) — under clouds Rosace tries again
every minute all night, and each attempt leaves rows and guide images behind.
Every other observation is kept, files and rows: a success, one whose
reduction failed (its raw frames may be reduced later), one cut by a crash.

The night is the date of its session directories (<date>_<site>, the
astronomical night); every session of that night is processed. For each:
- rows, in one transaction: spectrum_flux, pointing_errors and
  bess_validation rows of its failed observations, then those observations;
  the session row too when it keeps no observation;
- files, at the top of its session directory only (subdirectories hold the
  reduction of kept observations): a failed observation's YAML and the files
  named after it (its YAML stem, or its id), every file its YAML names, and
  the GUIDING_*/WIDEFIELD_* files modified during its attempt — the guide
  images of a pointing that failed before recording them; only the
  SEQUENCER writes those, one observation at a time. A time window alone is
  not enough: the post-obs thread writes a kept observation's files
  (validation page, FRIPON fit) during later attempts. Never deleted: a file
  a kept observation's YAML names (with the GUIDING_RAW_ twin of its guide
  images), a file named after a kept observation, the session's own files
  (session log, Demetra ZIP — named with the session id) and
  exposure_suggestions.txt;
- a session that keeps no observation goes entirely, files selected as by
  --purge-simulated (simulated_data._session_files()); a directory left empty
  is removed.

purge_failed(night, apply=False) only reports what would go; apply=True
deletes. Refused while a session runs. Used by
`python -m rosace.main --purge-failed <date> [--apply]`.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .config import DB_PATH
from .simulated_data import FILE_MARGIN_SECONDS, RUNNING_MAX_AGE_HOURS, _session_files

SESSION_WIDE_FILES = ("exposure_suggestions.txt",)
ATTEMPT_PREFIXES = ("GUIDING_", "WIDEFIELD_")   # written by the SEQUENCER only


@dataclass
class SessionPurge:
    session_id: str
    started_at: str
    session_dir: str | None
    failed: list[str] = field(default_factory=list)   # observation ids
    kept: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)
    size_bytes: int = 0   # measured when found, so a report after deletion still shows it

    @property
    def whole_session(self) -> bool:
        return self.kept == 0


def _timestamp(iso: str | None) -> float | None:
    return datetime.fromisoformat(iso).timestamp() if iso else None


def _names_id(name: str, oid: str) -> bool:
    """Whether a file name holds an observation id as a whole word (an id of
    digits only must not match inside a timestamp)."""
    return re.search(rf"(?<![0-9A-Za-z]){re.escape(oid)}(?![0-9A-Za-z])", name) is not None


def _names_in(yaml_path: Path) -> set[str]:
    """The base name of every string an observation file holds — the files it
    names, whatever casier they are in."""
    names = set()

    def walk(value):
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str) and value:
            names.add(Path(value).name)

    walk(yaml.safe_load(yaml_path.read_text()))
    return names


def _attempt_files(directory: Path, failed: list[tuple], kept: list[tuple]) -> list[Path]:
    """The files of the failed attempts (see the module docstring). failed and
    kept: (id, yaml stem, yaml path, window start, window end) per observation."""
    protected = set()
    for _, stem, yaml_path, _, _ in kept:
        if yaml_path is None:
            continue   # recorded before its observation file was created (crash)
        if not yaml_path.is_file():
            raise RuntimeError(f"observation file of a kept observation not found: {yaml_path}")
        protected |= _names_in(yaml_path)
    protected |= {n.replace("GUIDING_", "GUIDING_RAW_", 1) for n in protected if n.startswith("GUIDING_")}
    kept_marks = [mark for oid, stem, *_ in kept for mark in (oid, stem)]

    named = set()
    for _, _, yaml_path, _, _ in failed:
        if yaml_path is not None and yaml_path.is_file():
            try:
                named |= _names_in(yaml_path)
            except yaml.YAMLError:
                pass   # its own name and window still find its files
    files = []
    for f in sorted(directory.iterdir()):
        if not f.is_file() or f.name in protected or f.name in SESSION_WIDE_FILES:
            continue
        if any(mark in f.name for mark in kept_marks):
            continue
        mtime = f.stat().st_mtime
        if (f.name in named
                or any(_names_id(f.name, oid) or f.name.startswith(stem) for oid, stem, *_ in failed)
                or (f.name.startswith(ATTEMPT_PREFIXES)
                    and any(start <= mtime <= end for *_, start, end in failed))):
            files.append(f)
    return files


def find_failed(night: str, db_path: Path | None = None, now: datetime | None = None) -> list[SessionPurge]:
    """The sessions of `night` (YYYY-MM-DD) with failed acquisitions or no kept
    observation, with the rows and files that would go. Raises RuntimeError
    while a session runs (marked running, started less than
    RUNNING_MAX_AGE_HOURS ago)."""
    datetime.strptime(night, "%Y-%m-%d")   # ValueError on a malformed date
    now = now or datetime.now(timezone.utc)
    db = sqlite3.connect(f"file:{db_path or DB_PATH}?mode=ro", uri=True)
    try:
        since = (now - timedelta(hours=RUNNING_MAX_AGE_HOURS)).isoformat()
        if db.execute("SELECT 1 FROM sessions WHERE status = 'running' AND started_at > ?", (since,)).fetchone():
            raise RuntimeError("a session is running -- run this again once it has closed")
        sessions = db.execute(
            "SELECT id, started_at, closed_at, session_dir FROM sessions ORDER BY started_at"
        ).fetchall()
        purges = []
        for i, (sid, started, closed, session_dir) in enumerate(sessions):
            if not session_dir or not Path(session_dir).name.startswith(f"{night}_"):
                continue
            next_start = _timestamp(sessions[i + 1][1]) if i + 1 < len(sessions) else None
            session_end = (_timestamp(closed) + FILE_MARGIN_SECONDS) if closed else (next_start or float("inf"))
            observations = db.execute(
                "SELECT id, created_at, ended_at, sequencer_status, yaml_file FROM observations "
                "WHERE session_id = ? ORDER BY created_at", (sid,)
            ).fetchall()
            failed, kept = [], []
            for j, (oid, created, ended, status, yaml_file) in enumerate(observations):
                following = _timestamp(observations[j + 1][1]) if j + 1 < len(observations) else None
                end = _timestamp(ended) or following or session_end
                yaml_path = Path(session_dir) / Path(yaml_file).name if yaml_file else None
                entry = (oid, Path(yaml_file).stem if yaml_file else oid, yaml_path, _timestamp(created), end)
                (failed if status == "error" else kept).append(entry)
            if kept and not failed:
                continue
            purge = SessionPurge(sid, started, session_dir, failed=[f[0] for f in failed], kept=len(kept))
            ids = purge.failed
            marks = ",".join("?" * len(ids))
            purge.rows = {
                table: db.execute(f"SELECT COUNT(*) FROM {table} WHERE observation_id IN ({marks})", ids).fetchone()[0]
                for table in ("spectrum_flux", "pointing_errors", "bess_validation")
            }
            purge.rows["observations"] = len(ids)
            purge.rows["sessions"] = 1 if purge.whole_session else 0
            if purge.whole_session:
                purge.files = _session_files(session_dir, sid, _timestamp(started), session_end)
            elif Path(session_dir).is_dir():
                purge.files = _attempt_files(Path(session_dir), failed, kept)
            purge.size_bytes = sum(f.stat().st_size for f in purge.files)
            purges.append(purge)
        return purges
    finally:
        db.close()


def _delete_rows(db_path: Path, purges: list[SessionPurge]) -> None:
    db = sqlite3.connect(db_path)
    try:
        with db:   # one transaction
            for purge in purges:
                ids = [(oid,) for oid in purge.failed]
                for table in ("spectrum_flux", "pointing_errors", "bess_validation"):
                    db.executemany(f"DELETE FROM {table} WHERE observation_id = ?", ids)
                db.executemany("DELETE FROM observations WHERE id = ?", ids)
                if purge.whole_session:
                    db.execute("DELETE FROM sessions WHERE id = ?", (purge.session_id,))
    finally:
        db.close()


def purge_failed(night: str, apply: bool = False, db_path: Path | None = None) -> list[SessionPurge]:
    """Finds what the failed acquisitions of `night` left and, with
    apply=True, deletes the rows (one transaction) then the files, and removes
    a session directory left empty. Returns what was (or would be) purged."""
    db_path = db_path or DB_PATH
    purges = find_failed(night, db_path)
    if not apply or not purges:
        return purges
    _delete_rows(db_path, purges)
    for purge in purges:
        for f in purge.files:
            if f.exists():
                f.unlink()
        directory = Path(purge.session_dir) if purge.session_dir else None
        if directory and directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    return purges


def report(purges: list[SessionPurge]) -> str:
    """Human-readable summary: one line per session, then the totals."""
    lines = []
    totals = {"observations": 0, "pointing_errors": 0, "spectrum_flux": 0, "bess_validation": 0, "sessions": 0}
    files = size = 0
    for p in purges:
        for table, count in p.rows.items():
            totals[table] += count
        files += len(p.files)
        size += p.size_bytes
        what = ("whole session (no kept observation)" if p.whole_session
                else f"{len(p.failed)} failed observations, {p.kept} kept")
        lines.append(f"  {p.session_id}  {p.started_at[:16]}  {Path(p.session_dir).name if p.session_dir else '-'}"
                     f"  {what}, files {len(p.files)} ({p.size_bytes / 1e6:.0f} MB)")
    lines.append(f"Total: {totals['observations']} observations, {totals['sessions']} sessions, "
                 f"{totals['pointing_errors']} pointing errors, {totals['spectrum_flux']} spectrum flux rows, "
                 f"{totals['bess_validation']} validation rows, {files} files ({size / 1e6:.0f} MB)")
    return "\n".join(lines)
