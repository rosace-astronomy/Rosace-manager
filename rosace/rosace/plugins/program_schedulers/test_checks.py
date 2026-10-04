"""
Tests for the planner contract (program_schedulers/__init__.py), the shared
feasibility checks (checks.py) and the history helper they offer
(database.db_get_successes_for_target).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from pydantic import ValidationError

from rosace.database import (
    db_count_trailing_failures, db_get_program_time_s, db_get_successes_for_target, db_init,
    db_insert_observation, db_insert_session, db_set_stage_status, db_stamp_stage,
)
from rosace.models import ExposureConfig, Observation, OrchestratorStage, SessionContext, Target
from rosace.plugins.program_schedulers import Candidate, TimeWindow, load_planner
from rosace.plugins.program_schedulers import checks
from rosace.plugins.program_schedulers.manual_list.manual_list import ManualListScheduler
from rosace.plugins.schedulers import SchedulerFatalError
from rosace.targets import Star

NOW = datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc)


def _star(magnitude_v=2.0, ra=10.0, dec=40.0) -> Star:
    return Star(id=1, name="Test star", ra=ra, dec=dec, magnitude_v=magnitude_v)


@pytest.fixture(autouse=True)
def _installation(monkeypatch):
    """Fixed installation settings, independent of the active config."""
    monkeypatch.setattr(checks, "MIN_ALTITUDE_DEG", 10)
    monkeypatch.setattr(checks, "MIN_MERIDIAN_DISTANCE_DEG", 0)
    monkeypatch.setattr(checks, "MERIDIAN_TOLERANCE_DEG", None)
    monkeypatch.setattr(checks, "OVERHEAD_SECONDS", 300)
    monkeypatch.setattr(checks, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
    monkeypatch.setattr(checks, "MAX_EXPOSURE_TIME_SECONDS", 300)
    monkeypatch.setattr(checks, "MIN_EXPOSURE_COUNT", 5)
    monkeypatch.setattr(checks, "HORIZON_FILE", None)
    monkeypatch.setattr(checks, "LOOKAHEAD_MINUTES", 60)


def _altitude_over_time(monkeypatch, altitude_at):
    """Replaces the sky computation by altitude_at(minutes after NOW)."""
    monkeypatch.setattr(
        checks, "altitude_deg",
        lambda ra, dec, lat, lon, at: altitude_at((at - NOW).total_seconds() / 60),
    )


class TestModels:

    def test_valid_candidate(self):
        c = Candidate(queried_name="Vega", program="Be_stars", priority=3, reason="test")
        assert c.exposure.exposure_time is None
        assert c.window is None

    @pytest.mark.parametrize("field, value", [
        ("priority", -1), ("priority", 5), ("queried_name", ""), ("reason", ""), ("program", ""),
    ])
    def test_invalid_candidate(self, field, value):
        kwargs = {"queried_name": "Vega", "program": "Be_stars", "priority": 3, "reason": "test"}
        kwargs[field] = value
        with pytest.raises(ValidationError):
            Candidate(**kwargs)


class TestLoadPlanner:

    def test_known_planner(self):
        assert isinstance(load_planner("manual_list"), ManualListScheduler)

    def test_unknown_planner(self):
        with pytest.raises(ValueError, match="not found"):
            load_planner("no_such_planner")

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="Invalid planner name"):
            load_planner("../manual_list")


class TestEstimateDuration:

    def test_explicit_exposure_time(self):
        # 60 s per sub-exposure, at least 5 sub-exposures, + 5 min overhead.
        assert checks.estimate_duration_s(_star(), ExposureConfig(exposure_time=60)) == 600

    def test_from_magnitude(self):
        # mag 2.5 -> 1 s * 10 = 10 s per sub-exposure, 5 of them, + 300 s.
        assert checks.estimate_duration_s(_star(magnitude_v=2.5), ExposureConfig()) == pytest.approx(350)

    def test_long_exposure_split(self):
        # 780 s > 300 s max -> 5 x 300 s (min count 5 above ceil(780/300) = 3).
        assert checks.estimate_duration_s(_star(), ExposureConfig(exposure_time=780)) == 5 * 300 + 300

    def test_unknown(self):
        assert checks.estimate_duration_s(_star(magnitude_v=None), ExposureConfig()) is None


class TestCheckFeasible:

    def test_high_star_is_feasible(self, monkeypatch):
        _altitude_over_time(monkeypatch, lambda minutes: 45.0)
        assert checks.check_feasible(_star(), ExposureConfig(exposure_time=60), NOW) is None

    def test_star_setting_during_observation(self, monkeypatch):
        # 12° at the start, 2° lost every 5 minutes: below 10° at +10 min.
        _altitude_over_time(monkeypatch, lambda minutes: 12.0 - 0.4 * minutes)
        reason = checks.check_feasible(_star(), ExposureConfig(exposure_time=60), NOW)
        assert reason is not None and "below minimum altitude" in reason and "+10 min" in reason

    def test_planner_minimum_adds_to_floor(self, monkeypatch):
        _altitude_over_time(monkeypatch, lambda minutes: 20.0)
        exposure = ExposureConfig(exposure_time=60)
        assert checks.check_feasible(_star(), exposure, NOW) is None
        assert "below minimum altitude" in checks.check_feasible(_star(), exposure, NOW, min_altitude_deg=30)

    def test_planner_minimum_never_lowers_floor(self, monkeypatch):
        _altitude_over_time(monkeypatch, lambda minutes: 8.0)
        reason = checks.check_feasible(_star(), ExposureConfig(exposure_time=60), NOW, min_altitude_deg=5)
        assert reason is not None and "below minimum altitude" in reason

    def test_below_local_horizon(self, monkeypatch, tmp_path):
        horizon = tmp_path / "horizon.txt"
        horizon.write_text("0 40\n90 40\n180 5\n270 5\n")
        monkeypatch.setattr(checks, "HORIZON_FILE", horizon)
        _altitude_over_time(monkeypatch, lambda minutes: 30.0)
        monkeypatch.setattr(checks, "azimuth_deg", lambda ra, dec, lat, lon, at: 45.0)
        reason = checks.check_feasible(_star(), ExposureConfig(exposure_time=60), NOW)
        assert reason is not None and "below local horizon" in reason

    def test_above_local_horizon(self, monkeypatch, tmp_path):
        horizon = tmp_path / "horizon.txt"
        horizon.write_text("0 40\n90 40\n180 5\n270 5\n")
        monkeypatch.setattr(checks, "HORIZON_FILE", horizon)
        _altitude_over_time(monkeypatch, lambda minutes: 30.0)
        monkeypatch.setattr(checks, "azimuth_deg", lambda ra, dec, lat, lon, at: 220.0)
        assert checks.check_feasible(_star(), ExposureConfig(exposure_time=60), NOW) is None

    def test_missing_horizon_file_is_a_scheduler_fatal_error(self, monkeypatch, tmp_path):
        # horizon.py raises its own HorizonFileError; checks.py turns it into the
        # scheduler's fatal error, which disables the planner (or stops the session).
        monkeypatch.setattr(checks, "HORIZON_FILE", tmp_path / "no_such_horizon.txt")
        _altitude_over_time(monkeypatch, lambda minutes: 30.0)
        with pytest.raises(SchedulerFatalError, match="Horizon file not found"):
            checks.check_feasible(_star(), ExposureConfig(exposure_time=60), NOW)

    def test_meridian_rule(self, monkeypatch):
        _altitude_over_time(monkeypatch, lambda minutes: 45.0)
        monkeypatch.setattr(checks, "hour_angle_deg", lambda ra, lon, at: 1.2)
        exposure = ExposureConfig(exposure_time=60)
        assert checks.check_feasible(_star(), exposure, NOW) is None   # rule off by default
        monkeypatch.setattr(checks, "MIN_MERIDIAN_DISTANCE_DEG", 3)
        assert "too close to the meridian" in checks.check_feasible(_star(), exposure, NOW)

    def _hour_angle_over_time(self, monkeypatch, ha_at_start):
        """Hour angle advancing 15 deg per hour from ha_at_start at NOW, folded into [-180, 180)."""
        monkeypatch.setattr(
            checks, "hour_angle_deg",
            lambda ra, lon, at: (ha_at_start + 15 * (at - NOW).total_seconds() / 3600 + 180) % 360 - 180,
        )

    def test_meridian_tolerance(self, monkeypatch):
        _altitude_over_time(monkeypatch, lambda minutes: 45.0)
        exposure = ExposureConfig(exposure_time=60)   # 5 x 60 s + 300 s overhead = 10 min = 2.5 deg of HA
        self._hour_angle_over_time(monkeypatch, -1.0)  # east, ends 1.5 deg past the meridian
        assert checks.check_feasible(_star(), exposure, NOW) is None   # rule off by default
        monkeypatch.setattr(checks, "MERIDIAN_TOLERANCE_DEG", 15)
        assert checks.check_feasible(_star(), exposure, NOW) is None   # within the tolerance

        # A 2-hour observation starting 1 deg east ends 29 deg past the meridian.
        reason = checks.check_feasible(_star(), exposure, NOW, duration_s=2 * 3600)
        assert reason is not None and "past the meridian" in reason and "tolerance 15" in reason

    def test_meridian_tolerance_target_already_west(self, monkeypatch):
        _altitude_over_time(monkeypatch, lambda minutes: 45.0)
        monkeypatch.setattr(checks, "MERIDIAN_TOLERANCE_DEG", 15)
        self._hour_angle_over_time(monkeypatch, 5.0)   # west: tracked away from the meridian
        assert checks.check_feasible(_star(), ExposureConfig(exposure_time=60), NOW, duration_s=2 * 3600) is None

    def test_meridian_tolerance_ignores_antimeridian(self, monkeypatch):
        _altitude_over_time(monkeypatch, lambda minutes: 45.0)
        monkeypatch.setattr(checks, "MERIDIAN_TOLERANCE_DEG", 15)
        self._hour_angle_over_time(monkeypatch, 175.0 - 360)   # -185 folds to +175: west
        assert checks.check_feasible(_star(), ExposureConfig(exposure_time=60), NOW, duration_s=3600) is None
        self._hour_angle_over_time(monkeypatch, -178.0)   # crosses -180 -> +180: not the meridian
        assert checks.check_feasible(_star(), ExposureConfig(exposure_time=60), NOW, duration_s=3600) is None

    def test_imposed_duration_checks_whole_series(self, monkeypatch):
        # Fine for one 10-minute observation, too low before the end of a 2-hour series.
        _altitude_over_time(monkeypatch, lambda minutes: 30.0 - 0.2 * minutes)
        exposure = ExposureConfig(exposure_time=60)
        assert checks.check_feasible(_star(), exposure, NOW) is None
        reason = checks.check_feasible(_star(), exposure, NOW, duration_s=2 * 3600)
        assert reason is not None and "below minimum altitude" in reason

    def test_start_later(self, monkeypatch):
        # High now, below the floor one hour later: a booked observation launched
        # then is refused.
        _altitude_over_time(monkeypatch, lambda minutes: 45.0 if minutes < 50 else 5.0)
        exposure = ExposureConfig(exposure_time=60)
        assert checks.check_feasible(_star(), exposure, NOW) is None
        reason = checks.check_feasible(_star(), exposure, NOW, start=NOW + timedelta(hours=1))
        assert reason is not None and "below minimum altitude" in reason

    def test_unknown_duration(self):
        reason = checks.check_feasible(_star(magnitude_v=None), ExposureConfig(), NOW)
        assert reason is not None and "duration unknown" in reason


class TestSuccessesForTarget:

    @pytest_asyncio.fixture(autouse=True)
    async def init_db(self):
        await db_init()

    async def _observation(self, session_id, program, target, minutes_ago, success):
        obs = Observation(
            id=str(uuid.uuid4())[:8],
            created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
            session_id=session_id,
            scheduler_data={"target": Target(name=target, ra=1.0, dec=1.0),
                            "queried_name": target, "program": program, "priority": 3},
        )
        await db_insert_observation(obs, session_id)
        if success:
            await db_stamp_stage(obs.id, OrchestratorStage.SEQUENCER)
        return obs

    async def test_counts_only_successes(self):
        session = SessionContext(site_name="Test")
        await db_insert_session(session)
        program, target = f"prog_{uuid.uuid4().hex[:8]}", f"star_{uuid.uuid4().hex[:8]}"

        assert await db_get_successes_for_target(program, target) == (0, None)

        first = await self._observation(session.session_id, program, target, 30, success=True)
        await self._observation(session.session_id, program, target, 10, success=False)
        count, last = await db_get_successes_for_target(program, target)
        assert count == 1
        assert last == first.created_at

        other = await self._observation(session.session_id, f"{program}_other", target, 5, success=True)
        assert other is not None
        assert (await db_get_successes_for_target(program, target))[0] == 1


class TestFailureBackoff:

    @pytest_asyncio.fixture(autouse=True)
    async def init_db(self):
        await db_init()

    async def test_recent_failure_refused_then_accepted(self, monkeypatch):
        session = SessionContext(site_name="Test")
        await db_insert_session(session)
        program, target = f"prog_{uuid.uuid4().hex[:8]}", f"star_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        monkeypatch.setattr(checks, "FAILURE_BACKOFF_HOURS", 1)

        assert await checks.check_not_recently_failed(program, target, now) is None

        obs = Observation(
            id=str(uuid.uuid4())[:8], created_at=now - timedelta(minutes=20), session_id=session.session_id,
            scheduler_data={"target": Target(name=target, ra=1.0, dec=1.0), "queried_name": target,
                            "program": program, "priority": 3},
        )
        await db_insert_observation(obs, session.session_id)
        await db_set_stage_status(obs.id, OrchestratorStage.SEQUENCER, "error")

        reason = await checks.check_not_recently_failed(program, target, now)
        assert reason is not None and "not retried before 1 h" in reason
        later = now + timedelta(hours=1, minutes=1)
        assert await checks.check_not_recently_failed(program, target, later) is None


def _at(minutes: float) -> datetime:
    return NOW + timedelta(minutes=minutes)


class TestWindow:

    def test_launch_time_is_overhead_before_start(self):
        assert checks.launch_time(TimeWindow(start_after=_at(30))) == _at(25)
        assert checks.launch_time(TimeWindow(end_before=_at(30))) is None
        assert checks.launch_time(None) is None

    @pytest.mark.parametrize("window, status", [
        (None, "open"),
        (TimeWindow(start_after=_at(-10)), "open"),
        (TimeWindow(start_after=_at(4)), "open"),                     # launch 1 min ago
        (TimeWindow(end_before=_at(20)), "open"),                     # 10-min observation fits
        (TimeWindow(start_after=_at(30)), "booked"),                  # launch in 25 min
        (TimeWindow(start_after=_at(30), end_before=_at(40)), "booked"),
        (TimeWindow(start_after=_at(90)), "refused"),                 # launch beyond the lookahead
        (TimeWindow(end_before=_at(5)), "refused"),                   # would end too late
        (TimeWindow(start_after=_at(30), end_before=_at(32)), "refused"),
        (TimeWindow(end_before=_at(-1)), "refused"),                  # closed
    ])
    def test_status(self, window, status):
        got, reason = checks.check_window(window, NOW, duration_s=600)
        assert got == status
        assert (reason is not None) == (status == "refused")

    def test_backoff_not_applied_at_level_1(self):
        assert checks.backoff_applies(2) and checks.backoff_applies(3)
        assert not checks.backoff_applies(1)


class TestHistoryHelpers:

    @pytest_asyncio.fixture(autouse=True)
    async def init_db(self):
        await db_init()

    async def _observation(self, session_id, program, target, minutes_ago, success):
        obs = Observation(
            id=str(uuid.uuid4())[:8],
            created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
            session_id=session_id,
            scheduler_data={"target": Target(name=target, ra=1.0, dec=1.0),
                            "queried_name": target, "program": program, "priority": 3},
        )
        await db_insert_observation(obs, session_id)
        if success:
            await db_stamp_stage(obs.id, OrchestratorStage.SEQUENCER)
        else:
            await db_set_stage_status(obs.id, OrchestratorStage.SEQUENCER, "error")

    async def test_trailing_failures(self):
        session = SessionContext(site_name="Test")
        await db_insert_session(session)
        program, target = f"prog_{uuid.uuid4().hex[:8]}", f"star_{uuid.uuid4().hex[:8]}"
        assert await db_count_trailing_failures(program, target) == 0
        await self._observation(session.session_id, program, target, 40, success=False)
        await self._observation(session.session_id, program, target, 30, success=True)
        await self._observation(session.session_id, program, target, 20, success=False)
        await self._observation(session.session_id, program, target, 10, success=False)
        assert await db_count_trailing_failures(program, target) == 2
        await self._observation(session.session_id, program, target, 5, success=True)
        assert await db_count_trailing_failures(program, target) == 0

    async def test_program_time(self):
        session = SessionContext(site_name="Test")
        await db_insert_session(session)
        program = f"prog_{uuid.uuid4().hex[:8]}"
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        await self._observation(session.session_id, program, "A", 10, success=True)
        await self._observation(session.session_id, program, "B", 20, success=False)
        used = await db_get_program_time_s(since)
        assert used[program] == pytest.approx(30 * 60, abs=10)
        later = await db_get_program_time_s(datetime.now(timezone.utc))
        assert program not in later
