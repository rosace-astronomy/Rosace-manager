"""Tests for time_budget.py: the night's time budget, built at CLOSE_SESSION."""

import csv
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from rosace import database as db_mod
from rosace.database import db_init
from rosace.time_budget import build_night_budget, compute_budget

T0 = datetime(2026, 11, 24, 18, 0, 0, tzinfo=timezone.utc)


def _seg(start_s, end_s, category, step="", obs="", outcome=None):
    return {
        "started_at": (T0 + timedelta(seconds=start_s)).isoformat(),
        "ended_at": (T0 + timedelta(seconds=end_s)).isoformat(),
        "category": category, "step": step, "observation_id": obs, "outcome": outcome,
    }


def _night():
    """One observation: 60 s init, 10 s scheduler, 100 s pointing over two
    steps, 1000 s science, 30 s calibrations, 20 s close."""
    return [
        _seg(0, 60, "init"),
        _seg(60, 70, "scheduler"),
        _seg(70, 72, "orchestrator", obs="A"),
        _seg(72, 132, "pointing", "slew_72ed", "A"),
        _seg(132, 172, "pointing", "pointing_72ed", "A"),
        _seg(172, 1172, "science", "star_spectra_acquisition", "A"),
        _seg(1172, 1202, "calibrations", "dark_acquisition", "A"),
        _seg(1202, 1222, "close"),
    ]


def test_nothing_leaks_and_mixed_categories_are_split():
    budget = compute_budget(_night(), {"A": {"science_exposure_s": 900.0, "slew_s": 20.0,
                                              "guiding_settle_s": 30.0}})
    s = budget.seconds
    assert budget.total == pytest.approx(1222.0)
    assert s["science_exposure"] == pytest.approx(900.0)
    assert s["science_readout"] == pytest.approx(100.0)
    assert s["slew"] == pytest.approx(20.0)
    assert s["guiding_settle"] == pytest.approx(30.0)
    assert s["fine_pointing"] == pytest.approx(50.0)
    assert "pointing" not in s and "science" not in s
    assert budget.successful_observations == 1 and budget.failed_observations == 0
    assert budget.capped == []


def test_missing_measurements_leave_everything_in_the_rest():
    s = compute_budget(_night(), {}).seconds
    assert s["fine_pointing"] == pytest.approx(100.0)
    assert s["science_readout"] == pytest.approx(1000.0)
    assert "slew" not in s and "science_exposure" not in s


def test_measurements_longer_than_the_step_are_capped():
    budget = compute_budget(_night(), {"A": {"science_exposure_s": 5000.0, "slew_s": 80.0,
                                              "guiding_settle_s": 80.0}})
    s = budget.seconds
    assert s["science_exposure"] == pytest.approx(1000.0)
    assert s["slew"] + s["guiding_settle"] == pytest.approx(100.0)
    assert budget.total == pytest.approx(1222.0)
    assert budget.capped == ["A"]


def test_failed_acquisition_counts_as_failed_attempt():
    segments = [
        _seg(0, 10, "scheduler"),
        _seg(10, 70, "pointing", "slew_72ed", "B", "failed"),
        _seg(70, 75, "orchestrator"),
    ]
    budget = compute_budget(segments, {"B": {"slew_s": 20.0}})
    assert budget.seconds["failed_attempt"] == pytest.approx(60.0)
    assert "slew" not in budget.seconds
    assert budget.failed_observations == 1 and budget.successful_observations == 0


def test_gap_between_two_sessions_is_counted():
    segments = [_seg(0, 100, "init"), _seg(100, 200, "close"),
                _seg(1200, 1300, "init"), _seg(1300, 1400, "close")]
    budget = compute_budget(segments, {})
    assert budget.seconds["between_sessions"] == pytest.approx(1000.0)
    assert budget.total == pytest.approx(1400.0)


async def test_build_night_budget_writes_table_csv_and_pdf(tmp_path):
    await db_init()
    night_dir = tmp_path / "2026-11-24_TestSite"
    night_dir.mkdir()
    sid = uuid.uuid4().hex[:8]
    with sqlite3.connect(db_mod.DB_PATH) as db:
        db.execute("INSERT INTO sessions (id, started_at, status, session_dir) VALUES (?, ?, 'running', ?)",
                   (sid, T0.isoformat(), str(night_dir)))
        db.execute("INSERT INTO observations (id, session_id, target_name, created_at, "
                   "science_exposure_s, slew_s, guiding_settle_s) VALUES (?, ?, 'Vega', ?, 900, 20, 30)",
                   (f"A{sid}", sid, T0.isoformat()))
        for seg in _night():
            if seg["observation_id"]:
                seg["observation_id"] = f"A{sid}"
            db.execute("INSERT INTO time_segments (session_id, started_at, ended_at, category, step, "
                       "observation_id, outcome) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (sid, seg["started_at"], seg["ended_at"], seg["category"], seg["step"] or None,
                        seg["observation_id"] or None, seg["outcome"]))

    budget = await build_night_budget(night_dir)

    assert budget is not None
    with sqlite3.connect(db_mod.DB_PATH) as db:
        rows = dict(db.execute("SELECT category, seconds FROM night_time_budget WHERE night = '2026-11-24'"))
    assert rows["science_exposure"] == pytest.approx(900.0)
    assert sum(rows.values()) == pytest.approx(1222.0)
    with open(night_dir / "2026-11-24_time_budget.csv", encoding="utf-8") as f:
        assert {r["category"] for r in csv.DictReader(f)} == set(rows)
    assert (night_dir / "2026-11-24_time_budget.pdf").stat().st_size > 0

    # Computed again at the next close: rows replaced, not added.
    await build_night_budget(night_dir)
    with sqlite3.connect(db_mod.DB_PATH) as db:
        count = db.execute("SELECT COUNT(*) FROM night_time_budget WHERE night = '2026-11-24'").fetchone()[0]
    assert count == len(rows)


async def test_build_night_budget_without_time_line_returns_none(tmp_path):
    await db_init()
    assert await build_night_budget(tmp_path / "2026-01-01_Nowhere") is None
