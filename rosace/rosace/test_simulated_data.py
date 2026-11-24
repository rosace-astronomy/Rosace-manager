"""
Tests for simulated_data.py (purge of the sessions run with simulated
equipment), on a small database of its own and files in tmp_path.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

from rosace.simulated_data import FILE_MARGIN_SECONDS, find_simulated_sessions, purge_simulated, report

T0 = datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc)


def _db(tmp_path):
    path = tmp_path / "rosace.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at TEXT, closed_at TEXT, status TEXT,
                               session_dir TEXT, hardware_profile TEXT, simulated INTEGER);
        CREATE TABLE observations (id TEXT PRIMARY KEY, session_id TEXT);
        CREATE TABLE pointing_errors (id INTEGER PRIMARY KEY, observation_id TEXT);
        CREATE TABLE spectrum_flux (id INTEGER PRIMARY KEY, observation_id TEXT);
    """)
    db.commit()
    return path, db


def _session(db, sid, start, end, session_dir, simulated, status="closed", observations=()):
    db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
               (sid, start.isoformat(), end.isoformat() if end else None, status, str(session_dir),
                "hardware_indi_simu" if simulated else "hardware_indi_72ED-LAN", simulated))
    for oid in observations:
        db.execute("INSERT INTO observations VALUES (?, ?)", (oid, sid))
        db.execute("INSERT INTO pointing_errors (observation_id) VALUES (?)", (oid,))
        db.execute("INSERT INTO spectrum_flux (observation_id) VALUES (?)", (oid,))
        db.execute("INSERT INTO spectrum_flux (observation_id) VALUES (?)", (oid,))
    db.commit()


def _file(directory, name, at):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("x")
    os.utime(path, (at.timestamp(), at.timestamp()))
    return path


def _counts(path):
    db = sqlite3.connect(path)
    return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("sessions", "observations", "pointing_errors", "spectrum_flux")}


def test_only_simulated_sessions_are_selected(tmp_path):
    path, db = _db(tmp_path)
    night = tmp_path / "night"
    _session(db, "sim1", T0, T0 + timedelta(minutes=10), night, 1, observations=["o1", "o2"])
    _session(db, "real1", T0 + timedelta(hours=5), T0 + timedelta(hours=6), night, 0, observations=["o3"])
    _session(db, "old1", T0 - timedelta(days=30), T0 - timedelta(days=29), night, None, observations=["o4"])

    purges = find_simulated_sessions(path)

    assert [p.session_id for p in purges] == ["sim1"]
    assert purges[0].rows == {"spectrum_flux": 4, "pointing_errors": 2, "observations": 2, "sessions": 1}


def test_running_session_spared_only_while_recent(tmp_path):
    path, db = _db(tmp_path)
    night = tmp_path / "night"
    _session(db, "stale", T0, None, night, 1, status="running", observations=["o1"])   # server stopped without CLOSE
    _session(db, "current", T0 + timedelta(hours=30), None, night, 1, status="running")

    # The stale one is selected even though it would become the most recent session once others are gone.
    assert [p.session_id for p in find_simulated_sessions(path, now=T0 + timedelta(hours=31))] == ["stale"]
    assert find_simulated_sessions(path, now=T0 + timedelta(hours=2)) == []
    assert [p.session_id for p in find_simulated_sessions(path, now=T0 + timedelta(hours=60))] == ["stale", "current"]


def test_files_of_the_session_window_and_by_name(tmp_path):
    path, db = _db(tmp_path)
    night = tmp_path / "night"
    _session(db, "sim1", T0, T0 + timedelta(minutes=10), night, 1)
    before = _file(night, "STAR_before.fits", T0 - timedelta(minutes=1))
    during = _file(night, "STAR_during.fits", T0 + timedelta(minutes=5))
    log = _file(night, "20261003T150000_sim1_session.log", T0 + timedelta(seconds=10 * 60 + FILE_MARGIN_SECONDS - 1))
    zip_named = _file(night, "night_sim1_demetra.zip", T0 + timedelta(hours=3))   # by name, whatever its time
    after = _file(night, "STAR_after.fits", T0 + timedelta(minutes=30))

    files = set(find_simulated_sessions(path)[0].files)

    assert files == {during, log, zip_named}
    assert before not in files and after not in files


def test_session_without_closed_at_ends_at_the_next_session(tmp_path):
    path, db = _db(tmp_path)
    night = tmp_path / "night"
    _session(db, "sim1", T0, None, night, 1)            # crashed, never closed
    _session(db, "real1", T0 + timedelta(hours=1), T0 + timedelta(hours=2), night, 0)
    mine = _file(night, "a.fits", T0 + timedelta(minutes=50))
    theirs = _file(night, "b.fits", T0 + timedelta(minutes=70))

    files = find_simulated_sessions(path)[0].files

    assert mine in files and theirs not in files


def test_dry_run_touches_nothing(tmp_path):
    path, db = _db(tmp_path)
    night = tmp_path / "night"
    _session(db, "sim1", T0, T0 + timedelta(minutes=10), night, 1, observations=["o1"])
    f = _file(night, "a.fits", T0 + timedelta(minutes=1))

    purges = purge_simulated(apply=False, db_path=path)

    assert len(purges) == 1 and f.exists()
    assert _counts(path)["observations"] == 1


def test_apply_deletes_rows_files_and_empty_directory(tmp_path):
    path, db = _db(tmp_path)
    sim_night, mixed_night = tmp_path / "sim_night", tmp_path / "mixed_night"
    _session(db, "sim1", T0, T0 + timedelta(minutes=10), sim_night, 1, observations=["o1"])
    _session(db, "sim2", T0 + timedelta(hours=1), T0 + timedelta(hours=1, minutes=10), mixed_night, 1, observations=["o2"])
    _session(db, "real1", T0 + timedelta(hours=5), T0 + timedelta(hours=6), mixed_night, 0, observations=["o3"])
    _file(sim_night, "a.fits", T0 + timedelta(minutes=1))
    _file(mixed_night, "b.fits", T0 + timedelta(hours=1, minutes=1))
    real_file = _file(mixed_night, "c.fits", T0 + timedelta(hours=5, minutes=30))

    purges = purge_simulated(apply=True, db_path=path)

    assert _counts(path) == {"sessions": 1, "observations": 1, "pointing_errors": 1, "spectrum_flux": 2}
    assert not sim_night.exists()                       # emptied, removed
    assert mixed_night.is_dir() and real_file.exists() and not (mixed_night / "b.fits").exists()
    assert "Total: 2 sessions, 2 observations" in report(purges)
    assert purge_simulated(apply=False, db_path=path) == []


def test_apply_deletes_the_time_line_and_the_budget_of_an_emptied_night(tmp_path):
    path, db = _db(tmp_path)
    db.executescript("""
        CREATE TABLE time_segments (id INTEGER PRIMARY KEY, session_id TEXT);
        CREATE TABLE night_time_budget (night TEXT, category TEXT);
    """)
    sim_night = tmp_path / "2026-10-03_Site"
    mixed_night = tmp_path / "2026-10-04_Site"
    _session(db, "sim1", T0, T0 + timedelta(minutes=10), sim_night, 1)
    _session(db, "sim2", T0 + timedelta(days=1), T0 + timedelta(days=1, minutes=10), mixed_night, 1)
    _session(db, "real1", T0 + timedelta(days=1, hours=1), T0 + timedelta(days=1, hours=2), mixed_night, 0)
    for sid in ("sim1", "sim2", "real1"):
        db.execute("INSERT INTO time_segments (session_id) VALUES (?)", (sid,))
    for night in ("2026-10-03", "2026-10-04"):
        db.execute("INSERT INTO night_time_budget VALUES (?, 'init')", (night,))
    db.commit()

    purge_simulated(apply=True, db_path=path)

    check = sqlite3.connect(path)
    assert [r[0] for r in check.execute("SELECT session_id FROM time_segments")] == ["real1"]
    assert [r[0] for r in check.execute("SELECT night FROM night_time_budget")] == ["2026-10-04"]
