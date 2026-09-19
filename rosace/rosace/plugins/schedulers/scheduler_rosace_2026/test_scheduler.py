"""
Tests for the SCHEDULER plugin (SchedulerPlugin).
Run: pytest plugins/test_scheduler.py -v
"""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from rosace.database import db_init, db_insert_session, db_insert_observation
from rosace.models import Observation, SessionContext
from rosace.plugins.schedulers.scheduler_rosace_2026 import scheduler as scheduler_mod
from rosace.plugins.schedulers.scheduler_rosace_2026.scheduler import SchedulerPlugin
from rosace.plugins.schedulers import SchedulerFatalError
from rosace.conftest import assert_scheduler_contract
from rosace.targets import (
    targets_db_init,
    StarCreate, ProgramCreate,
    star_insert, star_add_alias, program_insert, program_add_star,
)


def _write_observation_list(tmp_path, content: str):
    path = tmp_path / "ObservationList.txt"
    path.write_text(content)
    return path


def _write_horizon(tmp_path, content: str):
    path = tmp_path / "horizon.txt"
    path.write_text(content)
    return path


# Obstruction of 40° over the whole northern-to-eastern quadrant (az 0-90),
# opening to 5° from the south to the west (az 180-270), linear in between.
HORIZON_NORTH_EAST_HIGH = "0 40\n90 40\n180 5\n270 5\n"


class TestTargetAltitudeDeg:
    """Tests _target_altitude_deg() itself, not SchedulerPlugin. The north
    celestial pole (dec=90°) sits at an altitude constantly equal to the
    site's latitude, regardless of the time — the only simple case that
    doesn't depend on the test's run time."""

    def test_north_celestial_pole_altitude_equals_site_latitude(self):
        from rosace.config import SITE_LATITUDE_DEG

        altitude = scheduler_mod._target_altitude_deg(ra_deg=0.0, dec_deg=90.0)

        # astro_math.altitude_deg() is a geocentric approximation (no oblateness
        # modeled), so NCP altitude == latitude exactly, not just approximately.
        assert altitude == pytest.approx(SITE_LATITUDE_DEG, abs=1e-9)


class TestSchedulerPlugin:

    @pytest_asyncio.fixture(autouse=True)
    async def init_db(self):
        await db_init()
        await targets_db_init()

    @pytest.fixture(autouse=True)
    def _always_observable(self, monkeypatch):
        """Every test in this class gets a target well above MIN_TARGET_ALTITUDE_DEG
        by default — real sky altitude depends on the current date/time, which
        would make unrelated tests flaky. Tests for the altitude check itself
        override this with their own monkeypatch."""
        monkeypatch.setattr(scheduler_mod, "_target_altitude_deg", lambda ra, dec: 45.0)

    @pytest.fixture(autouse=True)
    def _no_horizon_by_default(self, monkeypatch):
        """No local horizon mask unless a test opts in — whatever horizon_file
        the active config happens to declare must never leak into these tests."""
        monkeypatch.setattr(scheduler_mod, "HORIZON_FILE", None)

    async def _make_session(self) -> SessionContext:
        session = SessionContext(site_name="Test")
        await db_insert_session(session)
        return session

    def _make_obs(self, session_id: str) -> Observation:
        return Observation(
            id=str(uuid.uuid4())[:8],
            created_at=datetime.now(timezone.utc),
            session_id=session_id,
        )

    async def test_selects_single_valid_target(self, tmp_path, monkeypatch):
        sid = await star_insert(StarCreate(name="alf Lyr", ra=279.23, dec=38.78, magnitude_v=0.03))
        pid = await program_insert(ProgramCreate(name="Be_stars"))
        await program_add_star(pid, sid, exposure_time=45.0)
        await star_add_alias(sid, "VegaSingleTargetTest")

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, Be_stars, VegaSingleTargetTest, Test\n"
        ))

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["target"].name == "alf Lyr"  # canonical name
        assert obs.scheduler_data["queried_name"] == "VegaSingleTargetTest"  # name as written in the file
        assert obs.scheduler_data["program"] == "Be_stars"
        assert obs.scheduler_data["exposure"].exposure_time == pytest.approx(45.0)
        assert_scheduler_contract(obs)

    async def test_returns_none_when_file_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n"
        ))

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["target"] is None

    async def test_unknown_program_line_is_skipped(self, tmp_path, monkeypatch):
        await star_insert(StarCreate(name="SomeStar", ra=10.0, dec=10.0))
        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, NoSuchProgram, SomeStar, x\n"
        ))

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["target"] is None

    async def test_unknown_star_line_is_skipped(self, tmp_path, monkeypatch):
        await program_insert(ProgramCreate(name="SomeProgram"))
        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, SomeProgram, NoSuchStar, x\n"
        ))

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["target"] is None

    async def test_star_not_linked_to_program_is_skipped(self, tmp_path, monkeypatch):
        await star_insert(StarCreate(name="UnlinkedStar", ra=10.0, dec=10.0))
        await program_insert(ProgramCreate(name="UnlinkedProgram"))
        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, UnlinkedProgram, UnlinkedStar, x\n"
        ))

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["target"] is None

    async def test_priority_ordering_lower_number_wins(self, tmp_path, monkeypatch):
        sid_low = await star_insert(StarCreate(name="LowPriorityStar", ra=1.0, dec=1.0))
        sid_high = await star_insert(StarCreate(name="HighPriorityStar", ra=2.0, dec=2.0))
        pid = await program_insert(ProgramCreate(name="PrioProgram"))
        await program_add_star(pid, sid_low)
        await program_add_star(pid, sid_high)

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path,
            "Priority, Program, Star, commentaire\n"
            "3, PrioProgram, LowPriorityStar, x\n"
            "1, PrioProgram, HighPriorityStar, x\n"
        ))

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["target"].name == "HighPriorityStar"
        assert obs.scheduler_data["priority"] == 1
        assert_scheduler_contract(obs)

    async def test_repeated_target_at_different_priorities_lets_intermediate_priority_through(
        self, tmp_path, monkeypatch
    ):
        """A target requested twice at different priorities (2 and 4) must not
        'borrow' its earliest priority for its second occurrence — a target at
        an intermediate priority (3) must be offered between the two."""
        sid_repeated = await star_insert(StarCreate(name="RepeatedPriorityStar", ra=1.0, dec=1.0))
        sid_between = await star_insert(StarCreate(name="BetweenPriorityStar", ra=2.0, dec=2.0))
        pid = await program_insert(ProgramCreate(name="InterleavedPrioProgram"))
        await program_add_star(pid, sid_repeated)
        await program_add_star(pid, sid_between)

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path,
            "Priority, Program, Star, commentaire\n"
            "2, InterleavedPrioProgram, RepeatedPriorityStar, x\n"
            "3, InterleavedPrioProgram, BetweenPriorityStar, x\n"
            "4, InterleavedPrioProgram, RepeatedPriorityStar, x\n"
        ))

        session = await self._make_session()

        obs1 = await SchedulerPlugin().run(self._make_obs(session.session_id))
        assert (obs1.scheduler_data["target"].name, obs1.scheduler_data["priority"]) == ("RepeatedPriorityStar", 2)
        assert_scheduler_contract(obs1)
        await db_insert_observation(obs1, session.session_id)

        obs2 = await SchedulerPlugin().run(self._make_obs(session.session_id))
        assert (obs2.scheduler_data["target"].name, obs2.scheduler_data["priority"]) == ("BetweenPriorityStar", 3)
        assert_scheduler_contract(obs2)
        await db_insert_observation(obs2, session.session_id)

        obs3 = await SchedulerPlugin().run(self._make_obs(session.session_id))
        assert (obs3.scheduler_data["target"].name, obs3.scheduler_data["priority"]) == ("RepeatedPriorityStar", 4)
        assert_scheduler_contract(obs3)

    async def test_alias_lines_count_as_same_target(self, tmp_path, monkeypatch):
        """Two lines for the same star (once by its canonical name, once by an
        alias) count as a single target requested twice — not two distinct
        targets."""
        sid = await star_insert(StarCreate(name="alf Lyr Alias Test", ra=279.23, dec=38.78))
        pid = await program_insert(ProgramCreate(name="Be_stars_alias_test"))
        await program_add_star(pid, sid)
        await star_add_alias(sid, "VegaSchedAliasTest")

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path,
            "Priority, Program, Star, commentaire\n"
            "1, Be_stars_alias_test, VegaSchedAliasTest, x\n"
            "1, Be_stars_alias_test, alf Lyr Alias Test, x\n"
        ))

        session = await self._make_session()

        # 1st and 2nd call: the target stays consumable (2 lines requested, 0 then 1 observed)
        obs1 = await SchedulerPlugin().run(self._make_obs(session.session_id))
        assert obs1.scheduler_data["target"].name == "alf Lyr Alias Test"
        assert_scheduler_contract(obs1)
        await db_insert_observation(obs1, session.session_id)

        obs2 = await SchedulerPlugin().run(self._make_obs(session.session_id))
        assert obs2.scheduler_data["target"].name == "alf Lyr Alias Test"
        assert_scheduler_contract(obs2)
        await db_insert_observation(obs2, session.session_id)

        # 3rd call: fully consumed (2 lines, 2 already observed)
        obs3 = await SchedulerPlugin().run(self._make_obs(session.session_id))
        assert obs3.scheduler_data["target"] is None

    async def test_already_consumed_target_is_skipped(self, tmp_path, monkeypatch):
        sid = await star_insert(StarCreate(name="ConsumedStar", ra=5.0, dec=5.0))
        pid = await program_insert(ProgramCreate(name="ConsumedProgram"))
        await program_add_star(pid, sid)

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, ConsumedProgram, ConsumedStar, x\n"
        ))

        session = await self._make_session()
        obs1 = await SchedulerPlugin().run(self._make_obs(session.session_id))
        assert obs1.scheduler_data["target"] is not None
        assert_scheduler_contract(obs1)
        await db_insert_observation(obs1, session.session_id)

        obs2 = await SchedulerPlugin().run(self._make_obs(session.session_id))
        assert obs2.scheduler_data["target"] is None

    async def test_exposure_time_none_when_not_set_in_program_stars(self, tmp_path, monkeypatch):
        sid = await star_insert(StarCreate(name="NoExpStar", ra=6.0, dec=6.0))
        pid = await program_insert(ProgramCreate(name="NoExpProgram"))
        await program_add_star(pid, sid)  # no exposure_time

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, NoExpProgram, NoExpStar, x\n"
        ))

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["exposure"].exposure_time is None
        assert_scheduler_contract(obs)

    async def test_skips_target_below_minimum_altitude(self, tmp_path, monkeypatch):
        sid = await star_insert(StarCreate(name="TooLowStar", ra=1.0, dec=1.0))
        pid = await program_insert(ProgramCreate(name="AltitudeProgram"))
        await program_add_star(pid, sid)

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, AltitudeProgram, TooLowStar, x\n"
        ))
        monkeypatch.setattr(scheduler_mod, "_target_altitude_deg", lambda ra, dec: 5.0)  # below the 10° default

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["target"] is None

    async def test_low_altitude_target_lets_lower_priority_through(self, tmp_path, monkeypatch):
        """The priority-1 target is below the minimum altitude — the priority-2
        target, on the other hand, is observable and must be selected instead."""
        sid_low_alt = await star_insert(StarCreate(name="TooLowPriority1", ra=1.0, dec=1.0))
        sid_high_alt = await star_insert(StarCreate(name="ObservablePriority2", ra=2.0, dec=2.0))
        pid = await program_insert(ProgramCreate(name="MixedAltitudeProgram"))
        await program_add_star(pid, sid_low_alt)
        await program_add_star(pid, sid_high_alt)

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path,
            "Priority, Program, Star, commentaire\n"
            "1, MixedAltitudeProgram, TooLowPriority1, x\n"
            "2, MixedAltitudeProgram, ObservablePriority2, x\n"
        ))
        monkeypatch.setattr(
            scheduler_mod, "_target_altitude_deg",
            lambda ra, dec: 5.0 if ra == 1.0 else 45.0,
        )

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["target"].name == "ObservablePriority2"
        assert_scheduler_contract(obs)

    async def test_below_threshold_target_is_not_marked_consumed(self, tmp_path, monkeypatch):
        """A target skipped for insufficient altitude must stay offerable as
        soon as it becomes observable again — it's not a missed observation."""
        sid = await star_insert(StarCreate(name="LaterObservableStar", ra=1.0, dec=1.0))
        pid = await program_insert(ProgramCreate(name="LaterObservableProgram"))
        await program_add_star(pid, sid)

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, LaterObservableProgram, LaterObservableStar, x\n"
        ))

        session = await self._make_session()

        monkeypatch.setattr(scheduler_mod, "_target_altitude_deg", lambda ra, dec: 5.0)
        obs1 = await SchedulerPlugin().run(self._make_obs(session.session_id))
        assert obs1.scheduler_data["target"] is None

        monkeypatch.setattr(scheduler_mod, "_target_altitude_deg", lambda ra, dec: 45.0)
        obs2 = await SchedulerPlugin().run(self._make_obs(session.session_id))
        assert obs2.scheduler_data["target"].name == "LaterObservableStar"

    async def test_target_below_local_horizon_is_skipped_and_next_line_offered(self, tmp_path, monkeypatch):
        """Both stars are at 20° (above the flat 10° floor). The priority-1 star
        sits at az 10 (mask 40°): hidden. The priority-2 star sits at az 200
        (mask 5°): free, and must be selected instead."""
        sid_hidden = await star_insert(StarCreate(name="HiddenByTrees", ra=1.0, dec=1.0))
        sid_free = await star_insert(StarCreate(name="FreeSkyStar", ra=2.0, dec=2.0))
        pid = await program_insert(ProgramCreate(name="HorizonMaskProgram"))
        await program_add_star(pid, sid_hidden)
        await program_add_star(pid, sid_free)

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path,
            "Priority, Program, Star, commentaire\n"
            "1, HorizonMaskProgram, HiddenByTrees, x\n"
            "2, HorizonMaskProgram, FreeSkyStar, x\n"
        ))
        monkeypatch.setattr(scheduler_mod, "HORIZON_FILE", _write_horizon(tmp_path, HORIZON_NORTH_EAST_HIGH))
        monkeypatch.setattr(scheduler_mod, "_target_altitude_deg", lambda ra, dec: 20.0)
        monkeypatch.setattr(
            scheduler_mod, "_target_azimuth_deg",
            lambda ra, dec: 10.0 if ra == 1.0 else 200.0,
        )

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["target"].name == "FreeSkyStar"
        assert_scheduler_contract(obs)

    async def test_target_below_local_horizon_is_not_marked_consumed(self, tmp_path, monkeypatch):
        """A target hidden by the local horizon must stay offerable as soon as
        it moves to an open azimuth — it's not a missed observation."""
        sid = await star_insert(StarCreate(name="LaterClearsTheTrees", ra=1.0, dec=1.0))
        pid = await program_insert(ProgramCreate(name="HorizonLaterProgram"))
        await program_add_star(pid, sid)

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, HorizonLaterProgram, LaterClearsTheTrees, x\n"
        ))
        monkeypatch.setattr(scheduler_mod, "HORIZON_FILE", _write_horizon(tmp_path, HORIZON_NORTH_EAST_HIGH))
        monkeypatch.setattr(scheduler_mod, "_target_altitude_deg", lambda ra, dec: 20.0)
        session = await self._make_session()

        monkeypatch.setattr(scheduler_mod, "_target_azimuth_deg", lambda ra, dec: 10.0)
        obs1 = await SchedulerPlugin().run(self._make_obs(session.session_id))
        assert obs1.scheduler_data["target"] is None

        monkeypatch.setattr(scheduler_mod, "_target_azimuth_deg", lambda ra, dec: 200.0)
        obs2 = await SchedulerPlugin().run(self._make_obs(session.session_id))
        assert obs2.scheduler_data["target"].name == "LaterClearsTheTrees"

    async def test_flat_minimum_altitude_still_applies_above_the_local_horizon(self, tmp_path, monkeypatch):
        """8° is above the 5° local horizon at this azimuth, but still below the
        flat 10° floor: the two checks combine, the mask never replaces the floor."""
        sid = await star_insert(StarCreate(name="AboveMaskBelowFloor", ra=1.0, dec=1.0))
        pid = await program_insert(ProgramCreate(name="FloorStillAppliesProgram"))
        await program_add_star(pid, sid)

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, FloorStillAppliesProgram, AboveMaskBelowFloor, x\n"
        ))
        monkeypatch.setattr(scheduler_mod, "HORIZON_FILE", _write_horizon(tmp_path, HORIZON_NORTH_EAST_HIGH))
        monkeypatch.setattr(scheduler_mod, "_target_altitude_deg", lambda ra, dec: 8.0)
        monkeypatch.setattr(scheduler_mod, "_target_azimuth_deg", lambda ra, dec: 200.0)

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["target"] is None

    async def test_target_exactly_at_local_horizon_altitude_is_accepted(self, tmp_path, monkeypatch):
        """Strict comparison: alt == horizon altitude is observable."""
        sid = await star_insert(StarCreate(name="RightOnTheHorizon", ra=1.0, dec=1.0))
        pid = await program_insert(ProgramCreate(name="OnTheHorizonProgram"))
        await program_add_star(pid, sid)

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, OnTheHorizonProgram, RightOnTheHorizon, x\n"
        ))
        monkeypatch.setattr(scheduler_mod, "HORIZON_FILE", _write_horizon(tmp_path, HORIZON_NORTH_EAST_HIGH))
        monkeypatch.setattr(scheduler_mod, "_target_altitude_deg", lambda ra, dec: 40.0)
        monkeypatch.setattr(scheduler_mod, "_target_azimuth_deg", lambda ra, dec: 45.0)  # mask: 40°

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["target"].name == "RightOnTheHorizon"

    async def test_no_horizon_file_configured_means_no_local_horizon_check(self, tmp_path, monkeypatch):
        """HORIZON_FILE unset (the autouse default): a target at az 10 / 20° is
        selected, exactly as before the mask existed."""
        sid = await star_insert(StarCreate(name="NoMaskConfigured", ra=1.0, dec=1.0))
        pid = await program_insert(ProgramCreate(name="NoMaskProgram"))
        await program_add_star(pid, sid)

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, NoMaskProgram, NoMaskConfigured, x\n"
        ))
        monkeypatch.setattr(scheduler_mod, "_target_altitude_deg", lambda ra, dec: 20.0)
        monkeypatch.setattr(scheduler_mod, "_target_azimuth_deg", lambda ra, dec: 10.0)

        session = await self._make_session()
        obs = await SchedulerPlugin().run(self._make_obs(session.session_id))

        assert obs.scheduler_data["target"].name == "NoMaskConfigured"

    async def test_missing_horizon_file_raises_fatal_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n"
        ))
        monkeypatch.setattr(scheduler_mod, "HORIZON_FILE", tmp_path / "NoSuchHorizon.txt")

        session = await self._make_session()
        with pytest.raises(SchedulerFatalError, match="Horizon file not found"):
            await SchedulerPlugin().run(self._make_obs(session.session_id))

    async def test_malformed_horizon_file_raises_fatal_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n"
        ))
        monkeypatch.setattr(
            scheduler_mod, "HORIZON_FILE", _write_horizon(tmp_path, "0 10\n90 20\nnot numbers\n"),
        )

        session = await self._make_session()
        with pytest.raises(SchedulerFatalError, match="line 3"):
            await SchedulerPlugin().run(self._make_obs(session.session_id))

    async def test_missing_observation_list_file_raises_fatal_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", tmp_path / "DoesNotExist.txt")

        session = await self._make_session()
        with pytest.raises(SchedulerFatalError, match="not found"):
            await SchedulerPlugin().run(self._make_obs(session.session_id))

    async def test_missing_column_in_observation_list_raises_fatal_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program_name, Star, commentaire\n1, Be_stars, Vega, Test\n"
        ))

        session = await self._make_session()
        with pytest.raises(SchedulerFatalError, match="missing column"):
            await SchedulerPlugin().run(self._make_obs(session.session_id))

    async def test_invalid_priority_value_raises_fatal_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\nabc, Be_stars, Vega, Test\n"
        ))

        session = await self._make_session()
        with pytest.raises(SchedulerFatalError, match="invalid line"):
            await SchedulerPlugin().run(self._make_obs(session.session_id))

    async def test_invalid_exposure_time_in_db_raises_fatal_error(self, tmp_path, monkeypatch):
        """A corrupted exposure_time value in the database (e.g. '' instead of
        NULL, via an external manual edit) must not loop indefinitely."""
        import aiosqlite
        from rosace.targets import TARGETS_DB_PATH

        sid = await star_insert(StarCreate(name="CorruptExpStar", ra=7.0, dec=7.0))
        pid = await program_insert(ProgramCreate(name="CorruptExpProgram"))
        async with aiosqlite.connect(TARGETS_DB_PATH) as db:
            await db.execute(
                "INSERT INTO program_stars (program_id, star_id, exposure_time) VALUES (?, ?, ?)",
                (pid, sid, ""),
            )
            await db.commit()

        monkeypatch.setattr(scheduler_mod, "OBSERVATION_LIST_PATH", _write_observation_list(
            tmp_path, "Priority, Program, Star, commentaire\n1, CorruptExpProgram, CorruptExpStar, x\n"
        ))

        session = await self._make_session()
        with pytest.raises(SchedulerFatalError, match="Invalid data"):
            await SchedulerPlugin().run(self._make_obs(session.session_id))
