"""
Tests for the blank program planner (BlankProgramScheduler) — also the
pattern to follow for a new planner's tests: a SchedulingContext built by
hand, the sky computation replaced by fixed altitudes, and the history
written into rosace.db as real observations.
"""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from rosace.database import db_init, db_insert_observation, db_insert_session, db_stamp_stage
from rosace.models import Observation, OrchestratorStage, SessionContext, Target
from rosace.plugins.program_schedulers import PRIORITY_ORDINARY, Candidate, SchedulingContext, Site
from rosace.plugins.program_schedulers import checks
from rosace.plugins.program_schedulers.blank_program.blank_program import BlankProgramScheduler
from rosace.targets import ProgramCreate, StarCreate, program_add_star, program_insert, star_insert, targets_db_init
from rosace.astro_math import MoonState


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


def _ctx(program, **params) -> SchedulingContext:
    return SchedulingContext(
        now=datetime.now(timezone.utc),
        site=Site(latitude_deg=45.0, longitude_deg=5.0, elevation_m=950),
        program=program,
        params=params,
    )


async def _program(*star_names):
    """Program with its stars; names sorted so program_get_stars' order is known."""
    program = f"prog_{_uid()}"
    pid = await program_insert(ProgramCreate(name=program))
    for i, name in enumerate(star_names):
        sid = await star_insert(StarCreate(name=name, ra=10.0 * (i + 1), dec=20.0, magnitude_v=3.0))
        await program_add_star(pid, sid)
    return program


async def _observed(program, star, success):
    session = SessionContext(site_name="Test")
    await db_insert_session(session)
    obs = Observation(
        id=_uid(), created_at=datetime.now(timezone.utc), session_id=session.session_id,
        scheduler_data={"target": Target(name=star, ra=1.0, dec=1.0), "queried_name": star,
                        "program": program, "priority": 3},
    )
    await db_insert_observation(obs, session.session_id)
    if success:
        await db_stamp_stage(obs.id, OrchestratorStage.SEQUENCER)


async def test_proposes_a_valid_candidate():
    star = f"A_{_uid()}"
    program = await _program(star)

    candidate = await BlankProgramScheduler().propose(_ctx(program))

    assert isinstance(candidate, Candidate)
    assert candidate.queried_name == star
    assert candidate.program == program
    assert candidate.priority == PRIORITY_ORDINARY
    assert candidate.reason


async def test_successful_star_skipped_failed_star_kept():
    uid = _uid()
    a, b = f"A_{uid}", f"B_{uid}"
    program = await _program(a, b)
    planner = BlankProgramScheduler()

    await _observed(program, a, success=False)
    assert (await planner.propose(_ctx(program))).queried_name == a     # a failure doesn't count

    await _observed(program, a, success=True)
    assert (await planner.propose(_ctx(program))).queried_name == b

    await _observed(program, b, success=True)
    assert await planner.propose(_ctx(program)) is None


async def test_infeasible_star_skipped(monkeypatch):
    uid = _uid()
    a, b = f"A_{uid}", f"B_{uid}"
    program = await _program(a, b)               # a at ra 10, b at ra 20
    monkeypatch.setattr(checks, "altitude_deg", lambda ra, dec, lat, lon, at: 5.0 if ra == 10.0 else 45.0)

    assert (await BlankProgramScheduler().propose(_ctx(program))).queried_name == b


async def test_own_minimum_altitude(monkeypatch):
    program = await _program(f"A_{_uid()}")
    monkeypatch.setattr(checks, "altitude_deg", lambda ra, dec, lat, lon, at: 25.0)

    assert await BlankProgramScheduler().propose(_ctx(program, min_altitude_deg=30)) is None


async def test_unknown_program():
    assert await BlankProgramScheduler().propose(_ctx(f"nowhere_{_uid()}")) is None


async def test_own_moon_distance(monkeypatch):
    program = await _program(f"A_{_uid()}")
    monkeypatch.setattr(checks, "MIN_MOON_DISTANCE_DEG", 0)
    monkeypatch.setattr(checks, "moon_state", lambda ra, dec, lat, lon, at: MoonState(30.0, 1.0, 20.0))

    assert await BlankProgramScheduler().propose(_ctx(program, min_moon_distance_deg=20)) is not None
    assert await BlankProgramScheduler().propose(_ctx(program, min_moon_distance_deg=45)) is None
