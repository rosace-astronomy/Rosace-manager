"""
Tests for the random pick planner (RandomPickScheduler). Same pattern as
test_blank_program.py: sky computation replaced by fixed altitudes, history
written into rosace.db as real observations, random.shuffle replaced by a
known order.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from rosace.database import db_init, db_insert_observation, db_insert_session, db_set_stage_status, db_stamp_stage
from rosace.models import Observation, OrchestratorStage, SessionContext, Target
from rosace.plugins.program_schedulers import PRIORITY_FILLER, PRIORITY_ORDINARY, Candidate, SchedulingContext, Site
from rosace.plugins.program_schedulers import checks
from rosace.plugins.program_schedulers.random_pick import random_pick
from rosace.plugins.program_schedulers.random_pick.random_pick import RandomPickScheduler
from rosace.targets import ProgramCreate, StarCreate, program_add_star, program_insert, star_insert, targets_db_init


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture(autouse=True)
async def init_db():
    await db_init()
    await targets_db_init()


@pytest.fixture(autouse=True)
def _installation(monkeypatch):
    monkeypatch.setattr(checks, "altitude_deg", lambda ra, dec, lat, lon, at: 45.0)
    monkeypatch.setattr(checks, "MIN_ALTITUDE_DEG", 10)
    monkeypatch.setattr(checks, "MIN_MERIDIAN_DISTANCE_DEG", 0)
    monkeypatch.setattr(checks, "MERIDIAN_TOLERANCE_DEG", None)
    monkeypatch.setattr(checks, "HORIZON_FILE", None)
    monkeypatch.setattr(checks, "FAILURE_BACKOFF_HOURS", 1)
    # Known order instead of a random one: the list is kept as is.
    monkeypatch.setattr(random_pick.random, "shuffle", lambda items: None)


def _ctx(program, **params) -> SchedulingContext:
    return SchedulingContext(
        now=datetime.now(timezone.utc),
        site=Site(latitude_deg=45.0, longitude_deg=5.0, elevation_m=950),
        program=program,
        params=params,
    )


async def _program(*star_names, exposure_time=None):
    """Program with its stars at RA 10, 20, ...; names sorted so program_get_stars' order is known."""
    program = f"prog_{_uid()}"
    pid = await program_insert(ProgramCreate(name=program))
    for i, name in enumerate(star_names):
        sid = await star_insert(StarCreate(name=name, ra=10.0 * (i + 1), dec=20.0, magnitude_v=3.0))
        await program_add_star(pid, sid, exposure_time)
    return program


async def _observed(program, star, success, days_ago=0):
    session = SessionContext(site_name="Test")
    await db_insert_session(session)
    obs = Observation(
        id=_uid(), created_at=datetime.now(timezone.utc) - timedelta(days=days_ago), session_id=session.session_id,
        scheduler_data={"target": Target(name=star, ra=1.0, dec=1.0), "queried_name": star,
                        "program": program, "priority": 3},
    )
    await db_insert_observation(obs, session.session_id)
    if success:
        await db_stamp_stage(obs.id, OrchestratorStage.SEQUENCER)
    else:
        await db_set_stage_status(obs.id, OrchestratorStage.SEQUENCER, "error")


async def test_proposes_a_valid_candidate_with_its_exposure():
    star = f"A_{_uid()}"
    program = await _program(star, exposure_time=1800.0)

    candidate = await RandomPickScheduler().propose(_ctx(program))

    assert isinstance(candidate, Candidate)
    assert (candidate.queried_name, candidate.program) == (star, program)
    assert candidate.priority == PRIORITY_ORDINARY
    assert candidate.exposure.exposure_time == 1800.0
    assert candidate.reason


async def test_priority_is_a_parameter():
    program = await _program(f"A_{_uid()}")
    assert (await RandomPickScheduler().propose(_ctx(program, priority=4))).priority == PRIORITY_FILLER


async def test_follows_the_shuffled_order(monkeypatch):
    uid = _uid()
    a, b = f"A_{uid}", f"B_{uid}"
    program = await _program(a, b)
    monkeypatch.setattr(random_pick.random, "shuffle", lambda items: items.reverse())

    assert (await RandomPickScheduler().propose(_ctx(program))).queried_name == b


async def test_once_per_night_and_failure_backoff():
    uid = _uid()
    a, b = f"A_{uid}", f"B_{uid}"
    program = await _program(a, b)
    planner = RandomPickScheduler()

    await _observed(program, a, success=True)
    assert (await planner.propose(_ctx(program))).queried_name == b

    await _observed(program, b, success=False)
    assert await planner.propose(_ctx(program)) is None   # a done, b failed minutes ago


async def test_min_interval_days():
    uid = _uid()
    a, b = f"A_{uid}", f"B_{uid}"
    program = await _program(a, b)
    planner = RandomPickScheduler()
    await _observed(program, a, success=True, days_ago=3)
    await _observed(program, b, success=True, days_ago=6)

    assert (await planner.propose(_ctx(program))).queried_name == a   # no interval: tonight only
    candidate = await planner.propose(_ctx(program, min_interval_days=5))
    assert candidate.queried_name == b and "last 5 d" in candidate.reason
    assert await planner.propose(_ctx(program, min_interval_days=7)) is None


async def test_infeasible_target_skipped(monkeypatch):
    uid = _uid()
    a, b = f"A_{uid}", f"B_{uid}"
    program = await _program(a, b)               # a at RA 10, b at RA 20
    monkeypatch.setattr(checks, "altitude_deg", lambda ra, dec, lat, lon, at: 5.0 if ra == 10.0 else 45.0)

    assert (await RandomPickScheduler().propose(_ctx(program))).queried_name == b
    assert await RandomPickScheduler().propose(_ctx(program, min_altitude_deg=50)) is None


async def test_unknown_program():
    assert await RandomPickScheduler().propose(_ctx(f"nowhere_{_uid()}")) is None
