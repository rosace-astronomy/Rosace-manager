"""
Tests for the selector (SelectorPlugin): planner calls (threads, timeout,
errors), candidate verification, ordering, and the comparison with
scheduler_rosace_2026 on the same observation list.

Programs live in a temporary programs/ directory (pipeline_loader.PROGRAMS_DIR
patched). Planners other than manual_list are test doubles, put directly in
the selector's planner cache.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from rosace.conftest import assert_scheduler_contract
from rosace.database import db_init, db_insert_observation, db_insert_session
from rosace.models import Observation, SessionContext, Target
from rosace.plugins import pipeline_loader
from rosace.plugins.program_schedulers import Candidate, TimeWindow
from rosace.plugins.program_schedulers import checks
from rosace.plugins.program_schedulers.manual_list import manual_list as manual_list_mod
from rosace.plugins.schedulers import SchedulerFatalError
from rosace.plugins.schedulers.scheduler_rosace_2026 import scheduler as old_scheduler_mod
from rosace.plugins.schedulers.scheduler_rosace_2026.scheduler import SchedulerPlugin as OldSchedulerPlugin
from rosace.plugins.schedulers.selector import scheduler as selector_mod
from rosace.plugins.schedulers.selector.scheduler import SelectorPlugin
from rosace.targets import ProgramCreate, StarCreate, program_add_star, program_insert, star_insert, targets_db_init

HEADER = "Priority, Program, Star, Start_after, End_before, Series, comment\n"


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
    monkeypatch.setattr(checks, "HORIZON_FILE", None)
    monkeypatch.setattr(checks, "OVERHEAD_SECONDS", 300)
    monkeypatch.setattr(checks, "MAX_EXPOSURE_TIME_SECONDS", 300)
    monkeypatch.setattr(checks, "MIN_EXPOSURE_COUNT", 5)
    monkeypatch.setattr(manual_list_mod, "_previously_reported", set())


@pytest.fixture
def programs_dir(tmp_path, monkeypatch):
    """A temporary programs/ directory holding manual_list.yaml, reading an
    initially empty observation list (tmp_path/ObservationList.txt)."""
    directory = tmp_path / "programs"
    directory.mkdir()
    (tmp_path / "ObservationList.txt").write_text(HEADER)
    (directory / "manual_list.yaml").write_text(
        "program: manual_list\n"
        "scheduler:\n"
        "  name: manual_list\n"
        "  params:\n"
        f"    observation_list_path: {tmp_path / 'ObservationList.txt'}\n"
    )
    monkeypatch.setattr(pipeline_loader, "PROGRAMS_DIR", directory)
    return directory


def _observation_list(programs_dir, body: str):
    (programs_dir.parent / "ObservationList.txt").write_text(HEADER + body)


def _declare_program(programs_dir, program: str, planner: str | None = None):
    """Writes programs/<program>.yaml, with a `scheduler` key if planner is given."""
    content = f"program: {program}\nsequencer: []\nreduction: []\nsharing: blank_share\n"
    if planner:
        content += f"scheduler:\n  name: {planner}\n"
    (programs_dir / f"{program}.yaml").write_text(content)


async def _program_with_stars(programs_dir, *star_names):
    program = f"prog_{_uid()}"
    pid = await program_insert(ProgramCreate(name=program))
    for i, name in enumerate(star_names):
        sid = await star_insert(StarCreate(name=name, ra=10.0 * (i + 1), dec=20.0, magnitude_v=3.0))
        await program_add_star(pid, sid, exposure_time=60.0)
    _declare_program(programs_dir, program)
    return program


async def _session() -> SessionContext:
    session = SessionContext(site_name="Test")
    await db_insert_session(session)
    return session


def _obs(session_id: str) -> Observation:
    return Observation(id=_uid(), created_at=datetime.now(timezone.utc), session_id=session_id)


async def _record(obs: Observation, session_id: str):
    """Stores a selected observation in rosace.db, as main.py does."""
    await db_insert_observation(obs, session_id)


class FixedPlanner:
    def __init__(self, candidate):
        self.candidate, self.calls = candidate, 0

    async def propose(self, ctx):
        self.calls += 1
        return self.candidate


class RaisingPlanner:
    def __init__(self, exc):
        self.exc, self.calls = exc, 0

    async def propose(self, ctx):
        self.calls += 1
        raise self.exc


class BlockingPlanner:
    """Blocks its thread (time.sleep, not await) — must not freeze the selector."""
    def __init__(self, seconds):
        self.seconds, self.calls = seconds, 0

    async def propose(self, ctx):
        self.calls += 1
        time.sleep(self.seconds)
        return None


def _add_planner(selector, programs_dir, program, planner):
    _declare_program(programs_dir, program, planner="test_double")
    selector._planners[program] = planner


class TestContract:

    async def test_manual_list_target_selected(self, programs_dir):
        star = f"A_{_uid()}"
        program = await _program_with_stars(programs_dir, star)
        _observation_list(programs_dir, f"3, {program}, {star}, none, none, none, x\n")
        session = await _session()

        obs = await SelectorPlugin().run(_obs(session.session_id))

        assert_scheduler_contract(obs)
        data = obs.scheduler_data
        assert data["target"].name == star and data["queried_name"] == star
        assert data["program"] == program and data["priority"] == 3
        assert data["proposed_by"] == "manual_list"
        assert data["estimated_duration_s"] == pytest.approx(5 * 60 + 300)
        assert data["reason"] == "ObservationList line 2 (priority 3)"

    async def test_nothing_to_observe(self, programs_dir):
        session = await _session()
        obs = await SelectorPlugin().run(_obs(session.session_id))
        assert obs.scheduler_data["target"] is None

    async def test_manual_list_required(self, programs_dir):
        (programs_dir / "manual_list.yaml").unlink()
        session = await _session()
        with pytest.raises(SchedulerFatalError, match="manual list is required"):
            await SelectorPlugin().run(_obs(session.session_id))


class TestOrdering:

    async def test_lower_level_wins(self, programs_dir):
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program = await _program_with_stars(programs_dir, a, b)
        _observation_list(programs_dir, f"3, {program}, {a}, none, none, none, x\n")
        selector = SelectorPlugin()
        _add_planner(selector, programs_dir, "other", FixedPlanner(
            Candidate(queried_name=b, program=program, priority=2, reason="test")))
        session = await _session()

        obs = await selector.run(_obs(session.session_id))
        assert obs.scheduler_data["queried_name"] == b
        assert obs.scheduler_data["proposed_by"] == "other"

    async def test_manual_list_first_at_equal_level(self, programs_dir):
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program = await _program_with_stars(programs_dir, a, b)
        _observation_list(programs_dir, f"3, {program}, {a}, none, none, none, x\n")
        selector = SelectorPlugin()
        _add_planner(selector, programs_dir, "aaa_first_by_name", FixedPlanner(
            Candidate(queried_name=b, program=program, priority=3, reason="test")))
        session = await _session()

        obs = await selector.run(_obs(session.session_id))
        assert obs.scheduler_data["proposed_by"] == "manual_list"

    async def test_series_program_of_previous_observation_keeps_the_hand(self, programs_dir):
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program_x = await _program_with_stars(programs_dir, a)
        program_y = await _program_with_stars(programs_dir, b)
        selector = SelectorPlugin()
        _add_planner(selector, programs_dir, "planner_x", FixedPlanner(
            Candidate(queried_name=a, program=program_x, priority=1, reason="series x")))
        _add_planner(selector, programs_dir, "planner_y", FixedPlanner(
            Candidate(queried_name=b, program=program_y, priority=1, reason="series y")))
        session = await _session()

        # The previous observation belongs to program_y -> its series continues,
        # although planner_x comes first by name.
        previous = _obs(session.session_id)
        previous.scheduler_data = {"target": Target(name=b, ra=20.0, dec=20.0), "queried_name": b,
                                   "program": program_y, "priority": 1}
        await _record(previous, session.session_id)

        obs = await selector.run(_obs(session.session_id))
        assert obs.scheduler_data["program"] == program_y


class TestPlannerFailures:

    async def test_exception_skips_program_for_this_round(self, programs_dir, caplog):
        star = f"A_{_uid()}"
        program = await _program_with_stars(programs_dir, star)
        _observation_list(programs_dir, f"3, {program}, {star}, none, none, none, x\n")
        selector = SelectorPlugin()
        broken = RaisingPlanner(RuntimeError("boom"))
        _add_planner(selector, programs_dir, "broken", broken)
        session = await _session()

        with caplog.at_level(logging.WARNING, logger="spectro.plugin.scheduler"):
            obs = await selector.run(_obs(session.session_id))
            await selector.run(_obs(session.session_id))

        assert obs.scheduler_data["proposed_by"] == "manual_list"
        assert broken.calls == 2                       # asked again next round
        assert any("planner error" in r.getMessage() for r in caplog.records)

    async def test_fatal_error_disables_program_until_session_ends(self, programs_dir):
        selector = SelectorPlugin()
        broken = RaisingPlanner(SchedulerFatalError("bad config"))
        _add_planner(selector, programs_dir, "broken", broken)
        session = await _session()

        await selector.run(_obs(session.session_id))
        await selector.run(_obs(session.session_id))
        assert broken.calls == 1

        new_session = await _session()
        await selector.run(_obs(new_session.session_id))
        assert broken.calls == 2

    async def test_fatal_error_from_manual_list_stops_session(self, programs_dir):
        selector = SelectorPlugin()
        selector._planners["manual_list"] = RaisingPlanner(SchedulerFatalError("broken"))
        session = await _session()
        with pytest.raises(SchedulerFatalError, match="broken"):
            await selector.run(_obs(session.session_id))

    async def test_blocking_planner_times_out_without_freezing(self, programs_dir, monkeypatch, caplog):
        monkeypatch.setattr(selector_mod, "PLANNER_TIMEOUT_SECONDS", 0.2)
        selector = SelectorPlugin()
        slow = BlockingPlanner(1.0)
        _add_planner(selector, programs_dir, "slow", slow)
        session = await _session()

        start = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="spectro.plugin.scheduler"):
            await selector.run(_obs(session.session_id))
            first_round = time.monotonic() - start
            await selector.run(_obs(session.session_id))     # its thread is still sleeping
        assert first_round < 0.8
        assert slow.calls == 1
        assert any("no answer within" in r.getMessage() for r in caplog.records)
        assert any("still running" in r.getMessage() for r in caplog.records)

        await asyncio.sleep(1.0)                              # the thread ends
        monkeypatch.setattr(selector_mod, "PLANNER_TIMEOUT_SECONDS", 5)
        slow.seconds = 0
        await selector.run(_obs(session.session_id))
        assert slow.calls == 2


class TestVerification:

    @pytest.mark.parametrize("problem", ["unknown star", "unknown program", "window", "too low"])
    async def test_bad_candidate_rejected(self, programs_dir, monkeypatch, caplog, problem):
        star = f"A_{_uid()}"
        program = await _program_with_stars(programs_dir, star)
        candidate = {
            "unknown star": Candidate(queried_name=f"Nowhere_{_uid()}", program=program, priority=2, reason="t"),
            "unknown program": Candidate(queried_name=star, program=f"none_{_uid()}", priority=2, reason="t"),
            "window": Candidate(queried_name=star, program=program, priority=2, reason="t",
                                window=TimeWindow(end_before=datetime.now(timezone.utc))),
            "too low": Candidate(queried_name=star, program=program, priority=2, reason="t"),
        }[problem]
        if problem == "too low":
            monkeypatch.setattr(checks, "altitude_deg", lambda ra, dec, lat, lon, at: 5.0)
        selector = SelectorPlugin()
        _add_planner(selector, programs_dir, "faulty", FixedPlanner(candidate))
        session = await _session()

        with caplog.at_level(logging.WARNING, logger="spectro.plugin.scheduler"):
            obs = await selector.run(_obs(session.session_id))

        assert obs.scheduler_data["target"] is None
        assert any("rejected" in r.getMessage() for r in caplog.records)


class TestSameChoicesAsScheduler2026:

    async def test_same_sequence_of_targets(self, programs_dir, monkeypatch):
        """Same observation list and same history -> the selector with
        manual_list picks the same targets as scheduler_rosace_2026, in the
        same order, including a star listed twice and a star too low."""
        uid = _uid()
        a, b, c, low = f"A_{uid}", f"B_{uid}", f"C_{uid}", f"Low_{uid}"
        program = await _program_with_stars(programs_dir, a, b, c, low)   # low at ra 40
        _observation_list(programs_dir,
                          f"2, {program}, {a}, none, none, none, x\n"
                          f"2, {program}, {low}, none, none, none, x\n"
                          f"3, {program}, {b}, none, none, none, x\n"
                          f"4, {program}, {a}, none, none, none, x\n"
                          f"3, {program}, {c}, none, none, none, x\n")

        def altitude(ra):
            return 5.0 if ra == 40.0 else 45.0
        monkeypatch.setattr(checks, "altitude_deg", lambda ra, dec, lat, lon, at: altitude(ra))
        monkeypatch.setattr(old_scheduler_mod, "_target_altitude_deg", lambda ra, dec: altitude(ra))
        monkeypatch.setattr(old_scheduler_mod, "HORIZON_FILE", None)
        monkeypatch.setattr(old_scheduler_mod, "MIN_MERIDIAN_DISTANCE_DEG", 0)
        monkeypatch.setattr(old_scheduler_mod, "MIN_TARGET_ALTITUDE_DEG", 10)
        monkeypatch.setattr(old_scheduler_mod, "OBSERVATION_LIST_PATH",
                            programs_dir.parent / "ObservationList.txt")

        session = await _session()
        selector, old = SelectorPlugin(), OldSchedulerPlugin()
        chosen = []
        for _ in range(6):
            new_obs = await selector.run(_obs(session.session_id))
            old_obs = await old.run(_obs(session.session_id))
            new_target, old_target = new_obs.scheduler_data["target"], old_obs.scheduler_data["target"]
            assert (new_target and new_target.name) == (old_target and old_target.name)
            if new_target is None:
                break
            chosen.append(new_target.name)
            await _record(new_obs, session.session_id)

        assert chosen == [a, b, c, a]
