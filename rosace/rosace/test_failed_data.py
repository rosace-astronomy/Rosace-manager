"""
Tests for failed_data.py (purge of the observations of one night whose
acquisition failed), on a small database of its own and files in tmp_path.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from rosace.failed_data import find_failed, purge_failed, report

T0 = datetime(2026, 10, 29, 19, 0, tzinfo=timezone.utc)
NIGHT = "2026-10-29"


def _db(tmp_path):
    path = tmp_path / "rosace.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at TEXT, closed_at TEXT, status TEXT,
                               session_dir TEXT, hardware_profile TEXT, simulated INTEGER);
        CREATE TABLE observations (id TEXT PRIMARY KEY, session_id TEXT, created_at TEXT, ended_at TEXT,
                                   sequencer_status TEXT, yaml_file TEXT);
        CREATE TABLE pointing_errors (id INTEGER PRIMARY KEY, observation_id TEXT);
        CREATE TABLE spectrum_flux (id INTEGER PRIMARY KEY, observation_id TEXT);
        CREATE TABLE bess_validation (observation_id TEXT PRIMARY KEY);
    """)
    db.commit()
    return path, db


def _session(db, sid, start, end, session_dir, status="closed"):
    db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
               (sid, start.isoformat(), end.isoformat() if end else None, status, str(session_dir),
                "hardware_indi_72ED-LAN", 0))
    db.commit()


def _file(directory, name, at, content="x"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content)
    os.utime(path, (at.timestamp(), at.timestamp()))
    return path


def _observation(db, directory, oid, sid, start, minutes, status, names=(), pointing_error=False):
    """An observation row and its observation file naming `names`."""
    stem = f"{start:%Y%m%dT%H%M%S}_Star_{oid}"
    end = start + timedelta(minutes=minutes)
    db.execute("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?)",
               (oid, sid, start.isoformat(), end.isoformat(), status, f"{stem}.yaml"))
    if pointing_error:
        db.execute("INSERT INTO pointing_errors (observation_id) VALUES (?)", (oid,))
    db.commit()
    _file(directory, f"{stem}.yaml", end,
          yaml.safe_dump({"id": oid, "sequencer_data": {"images": list(names)}}))
    return stem


def _counts(path):
    db = sqlite3.connect(path)
    return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("sessions", "observations", "pointing_errors", "spectrum_flux", "bess_validation")}


@pytest.fixture
def night(tmp_path):
    """One session: a success (o1), then a failed attempt (o2) during which
    the post-obs thread writes o1's files, then a reduction failure (o3)."""
    path, db = _db(tmp_path)
    d = tmp_path / f"{NIGHT}_Site"
    _session(db, "s1", T0, T0 + timedelta(hours=2), d)
    o1 = _observation(db, d, "o1", "s1", T0, 20, "done",
                      names=["STAR_o1-1.fits", "GUIDING_o1.fits", "CALIB_shared-1.fits"], pointing_error=True)
    db.execute("INSERT INTO spectrum_flux (observation_id) VALUES ('o1')")
    db.execute("INSERT INTO bess_validation VALUES ('o1')")
    db.commit()
    t2 = T0 + timedelta(minutes=20)
    o2 = _observation(db, d, "o2", "s1", t2, 5, "error",
                      names=["GUIDING_o2_named.fits", "CALIB_shared-1.fits"], pointing_error=True)
    t3 = T0 + timedelta(minutes=25)
    o3 = _observation(db, d, "o3", "s1", t3, 10, "done", names=["STAR_o3-1.fits"])
    files = {
        "o1_star": _file(d, "STAR_o1-1.fits", T0 + timedelta(minutes=10)),
        "o1_guide": _file(d, "GUIDING_o1.fits", T0 + timedelta(minutes=5)),
        "o1_raw": _file(d, "GUIDING_RAW_o1.fits", T0 + timedelta(minutes=5)),
        "calib": _file(d, "CALIB_shared-1.fits", t2 + timedelta(minutes=1)),     # shot by o2, reused by o1? named by both
        "o1_validation": _file(d, f"{o1}_o1_validation.json", t2 + timedelta(minutes=2)),
        "fripon": _file(d, "FRRA12_20261029T191000_UT.json", t2 + timedelta(minutes=2)),
        "o2_guide": _file(d, "GUIDING_Star_10s_20261029_192130.fits", t2 + timedelta(minutes=1)),
        "o2_raw": _file(d, "GUIDING_RAW_Star_10s_20261029_192130.fits", t2 + timedelta(minutes=1)),
        "o2_named": _file(d, "GUIDING_o2_named.fits", t2 + timedelta(minutes=1)),
        "o2_srl": _file(d, f"{o2}.obs.srl", t2 + timedelta(minutes=3)),
        "o2_guide_log": _file(d, "20261029T192000_Star_o2_PHD2_GuideLog.txt", t2 + timedelta(minutes=3)),
        "o3_star": _file(d, "STAR_o3-1.fits", t3 + timedelta(minutes=5)),
        "log": _file(d, "20261029T190000_s1_session.log", t2 + timedelta(minutes=2)),
        "suggestions": _file(d, "exposure_suggestions.txt", t2 + timedelta(minutes=2)),
        "o2_yaml": d / f"{o2}.yaml",
        "o3_yaml": d / f"{o3}.yaml",
    }
    sub = d / "Star_o1"
    _file(sub, "inside.fits", t2 + timedelta(minutes=1))
    return path, db, d, files


def test_only_the_failed_acquisition_goes(night):
    path, _, d, files = night

    purges = find_failed(NIGHT, path)

    assert len(purges) == 1
    p = purges[0]
    assert p.failed == ["o2"] and p.kept == 2 and not p.whole_session
    assert p.rows == {"spectrum_flux": 0, "pointing_errors": 1, "bess_validation": 0, "observations": 1, "sessions": 0}
    assert set(p.files) == {files[k] for k in ("o2_guide", "o2_raw", "o2_named", "o2_srl", "o2_guide_log", "o2_yaml")}


def test_apply_keeps_every_kept_observation_complete(night):
    path, _, d, files = night

    purge_failed(NIGHT, apply=True, db_path=path)

    assert _counts(path) == {"sessions": 1, "observations": 2, "pointing_errors": 1, "spectrum_flux": 1,
                             "bess_validation": 1}
    gone = {"o2_guide", "o2_raw", "o2_named", "o2_srl", "o2_guide_log", "o2_yaml"}
    for key, f in files.items():
        assert f.exists() == (key not in gone), key
    assert (d / "Star_o1" / "inside.fits").exists()
    assert find_failed(NIGHT, path) == []


def test_dry_run_touches_nothing(night):
    path, _, _, files = night

    purges = purge_failed(NIGHT, apply=False, db_path=path)

    assert purges and all(f.exists() for f in files.values())
    assert _counts(path)["observations"] == 3
    assert "Total: 1 observations, 0 sessions, 1 pointing errors" in report(purges)


def test_session_without_kept_observation_goes_entirely(tmp_path):
    path, db = _db(tmp_path)
    d = tmp_path / f"{NIGHT}_Site"
    _session(db, "s1", T0, T0 + timedelta(hours=1), d)
    _observation(db, d, "o1", "s1", T0 + timedelta(minutes=5), 2, "error", pointing_error=True)
    _file(d, "BIAS_x-1.fits", T0 + timedelta(minutes=1))
    _file(d, "20261029T190000_s1_session.log", T0 + timedelta(hours=1, minutes=1))

    purges = purge_failed(NIGHT, apply=True, db_path=path)

    assert purges[0].whole_session and len(purges[0].files) == 3
    assert _counts(path) == {"sessions": 0, "observations": 0, "pointing_errors": 0, "spectrum_flux": 0,
                             "bess_validation": 0}
    assert not d.exists()


def test_other_nights_and_failure_free_sessions_untouched(tmp_path):
    path, db = _db(tmp_path)
    other = tmp_path / "2026-10-27_Site"
    _session(db, "s0", T0 - timedelta(days=2), T0 - timedelta(days=2, hours=-1), other)
    _observation(db, other, "o0", "s0", T0 - timedelta(days=2), 2, "error")
    clean = tmp_path / f"{NIGHT}_Site"
    _session(db, "s1", T0, T0 + timedelta(hours=1), clean)
    _observation(db, clean, "o1", "s1", T0, 10, "done")

    assert find_failed(NIGHT, path) == []
    assert [p.session_id for p in find_failed("2026-10-27", path)] == ["s0"]


def test_refused_while_a_session_runs(night):
    path, db, d, _ = night
    _session(db, "s2", T0 + timedelta(hours=3), None, d, status="running")

    with pytest.raises(RuntimeError, match="a session is running"):
        find_failed(NIGHT, path, now=T0 + timedelta(hours=4))
    # A "running" row older than a night was left by a crash: not a running session.
    assert find_failed(NIGHT, path, now=T0 + timedelta(hours=40))


def test_malformed_night_rejected(night):
    path, *_ = night
    with pytest.raises(ValueError):
        find_failed("29/10/2026", path)
