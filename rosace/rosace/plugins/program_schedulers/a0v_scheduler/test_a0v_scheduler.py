"""
Tests for the A0V planner (A0vSchedulerScheduler). Same pattern as
test_random_pick.py: sky computation replaced by fixed altitudes, history
written into rosace.db as real observations — here around a fixed night
instant, so that no test depends on the time of day it runs at.
"""

import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest
import pytest_asyncio

from rosace import database
from rosace.database import db_init, db_insert_observation, db_insert_session, db_set_stage_status, db_stamp_stage
from rosace.models import Observation, OrchestratorStage, SessionContext, Target
from rosace.plugins.program_schedulers import PRIORITY_HIGH, Candidate, SchedulingContext, Site, load_planner
from rosace.plugins.program_schedulers import checks
from rosace.plugins.program_schedulers.a0v_scheduler import a0v_scheduler
from rosace.plugins.program_schedulers.a0v_scheduler.a0v_scheduler import A0vSchedulerScheduler
from rosace.targets import ProgramCreate, StarCreate, program_add_star, program_insert, star_insert, targets_db_init

NOW = datetime(2026, 1, 15, 22, 0, tzinfo=timezone.utc)   # well inside one astronomical night


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture(autouse=True)
async def init_db():
    await db_init()
    await targets_db_init()


@pytest.fixture(autouse=True)
def _installation(monkeypatch):
    monkeypatch.setattr(checks, "altitude_deg", lambda ra, dec, lat, lon, at: 80.0)
    monkeypatch.setattr(checks, "MIN_ALTITUDE_DEG", 10)
    monkeypatch.setattr(checks, "MIN_MERIDIAN_DISTANCE_DEG", 0)
    monkeypatch.setattr(checks, "MERIDIAN_TOLERANCE_DEG", None)
    monkeypatch.setattr(checks, "HORIZON_FILE", None)
    monkeypatch.setattr(checks, "FAILURE_BACKOFF_HOURS", 1)
    monkeypatch.setattr(checks, "OVERHEAD_SECONDS", 300)
    # The night's first observation: other test files share rosace.db.
    async def first_observation(now=None):
        return NOW - timedelta(minutes=10)
    monkeypatch.setattr(a0v_scheduler, "db_get_first_observation_time", first_observation)


def _altitudes(monkeypatch, by_ra: dict[float, float]):
    monkeypatch.setattr(checks, "altitude_deg", lambda ra, dec, lat, lon, at: by_ra[ra])


def _ctx(program, now=NOW, **params) -> SchedulingContext:
    return SchedulingContext(now=now, site=Site(latitude_deg=45.0, longitude_deg=5.0, elevation_m=950),
                             program=program, params=params)


async def _program(*star_names):
    """Program with its stars at RA 10, 20, ..., 60 s exposure."""
    program = f"prog_{_uid()}"
    pid = await program_insert(ProgramCreate(name=program))
    for i, name in enumerate(star_names):
        sid = await star_insert(StarCreate(name=name, ra=10.0 * (i + 1), dec=20.0, magnitude_v=4.0))
        await program_add_star(pid, sid, 60.0)
    return program


async def _observed(program, star, success, at):
    """An observation of `star` started at `at` (and, for a failure, ended then)."""
    session = SessionContext(site_name="Test")
    await db_insert_session(session)
    obs = Observation(
        id=_uid(), created_at=at, session_id=session.session_id,
        scheduler_data={"target": Target(name=star, ra=1.0, dec=1.0), "queried_name": star,
                        "program": program, "priority": 2},
    )
    await db_insert_observation(obs, session.session_id)
    if success:
        await db_stamp_stage(obs.id, OrchestratorStage.SEQUENCER)
    else:
        await db_set_stage_status(obs.id, OrchestratorStage.SEQUENCER, "error")
        async with aiosqlite.connect(database.DB_PATH) as db:
            await db.execute("UPDATE observations SET ended_at = ? WHERE id = ?", (at.isoformat(), obs.id))
            await db.commit()


def test_resolved_by_name():
    assert isinstance(load_planner("a0v_scheduler"), A0vSchedulerScheduler)


async def test_first_of_the_night_with_its_window():
    star = f"A_{_uid()}"
    program = await _program(star)

    candidate = await A0vSchedulerScheduler().propose(_ctx(program))

    assert isinstance(candidate, Candidate)
    assert (candidate.queried_name, candidate.program, candidate.priority) == (star, program, PRIORITY_HIGH)
    assert candidate.exposure.exposure_time == 60.0
    assert candidate.window.end_before == NOW - timedelta(minutes=10) + timedelta(minutes=90)
    assert candidate.window.start_after is None
    assert "first reference star of the night" in candidate.reason


async def test_window_dropped_once_it_can_no_longer_be_met():
    program = await _program(f"A_{_uid()}")
    late = NOW + timedelta(minutes=85)   # 5 min left, the observation lasts 10

    candidate = await A0vSchedulerScheduler().propose(_ctx(program, now=late))

    assert candidate.priority == PRIORITY_HIGH and candidate.window is None


async def test_one_every_interval():
    uid = _uid()
    a, b = f"A_{uid}", f"B_{uid}"
    program = await _program(a, b)
    await _observed(program, a, success=True, at=NOW - timedelta(hours=3))
    planner = A0vSchedulerScheduler()

    assert await planner.propose(_ctx(program)) is None
    later = await planner.propose(_ctx(program, now=NOW + timedelta(hours=1, minutes=5)))
    assert later.queried_name == b and later.window is None
    assert "last reference star 4.1 h ago" in later.reason
    assert await planner.propose(_ctx(program, interval_hours=2)) is not None


async def test_pause_after_a_failure():
    uid = _uid()
    a, b = f"A_{uid}", f"B_{uid}"
    program = await _program(a, b)
    await _observed(program, a, success=False, at=NOW - timedelta(minutes=20))
    planner = A0vSchedulerScheduler()

    assert await planner.propose(_ctx(program)) is None                       # 20 min < 30
    after = await planner.propose(_ctx(program, now=NOW + timedelta(minutes=15)))
    assert after.queried_name == b                                            # a still in its 1 h backoff
    assert await planner.propose(_ctx(program, retry_minutes=10)) is not None


async def test_airmass_limit(monkeypatch):
    uid = _uid()
    a, b = f"A_{uid}", f"B_{uid}"
    program = await _program(a, b)
    _altitudes(monkeypatch, {10.0: 40.0, 20.0: 30.0})        # X 1.56 and 2.0

    assert await A0vSchedulerScheduler().propose(_ctx(program)) is None
    assert (await A0vSchedulerScheduler().propose(_ctx(program, max_airmass=1.6))).queried_name == a


async def test_rotation_within_the_margin(monkeypatch):
    uid = _uid()
    a, b, c, d = f"A_{uid}", f"B_{uid}", f"C_{uid}", f"D_{uid}"
    program = await _program(a, b, c, d)
    # a X 1.00 (best), b X 1.04, c X 1.06, d X 1.31 (outside the 0.1 margin)
    _altitudes(monkeypatch, {10.0: 89.0, 20.0: 74.0, 30.0: 70.0, 40.0: 50.0})
    yesterday = NOW - timedelta(days=1)
    await _observed(program, a, success=True, at=yesterday - timedelta(days=3))
    await _observed(program, b, success=True, at=yesterday - timedelta(days=9))
    planner = A0vSchedulerScheduler()

    first = await planner.propose(_ctx(program))
    assert first.queried_name == c and "never observed" in first.reason   # never observed first

    await _observed(program, c, success=True, at=NOW - timedelta(hours=5))
    assert (await planner.propose(_ctx(program))).queried_name == b       # then the least recent
    # d, never observed but too low compared with the best, is never chosen.


async def test_highest_breaks_the_tie(monkeypatch):
    uid = _uid()
    a, b = f"A_{uid}", f"B_{uid}"
    program = await _program(a, b)
    _altitudes(monkeypatch, {10.0: 70.0, 20.0: 75.0})

    assert (await A0vSchedulerScheduler().propose(_ctx(program))).queried_name == b


async def test_infeasible_star_skipped(monkeypatch):
    uid = _uid()
    a, b = f"A_{uid}", f"B_{uid}"
    program = await _program(a, b)
    _altitudes(monkeypatch, {10.0: 5.0, 20.0: 60.0})

    assert (await A0vSchedulerScheduler().propose(_ctx(program))).queried_name == b
    assert await A0vSchedulerScheduler().propose(_ctx(program, min_altitude_deg=65)) is None


async def test_unknown_program():
    assert await A0vSchedulerScheduler().propose(_ctx(f"nowhere_{_uid()}")) is None
