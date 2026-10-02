"""
Tests for the manual list planner (ManualListScheduler).
The databases are shared by the whole test session: every test creates its
own program and stars, with unique names.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from rosace.database import db_init, db_insert_observation, db_insert_session, db_set_stage_status, db_stamp_stage
from rosace.models import Observation, OrchestratorStage, SessionContext, Target
from rosace.plugins.program_schedulers import SchedulingContext, Site
from rosace.plugins.program_schedulers import checks
from rosace.plugins.program_schedulers.manual_list import manual_list as manual_list_mod
from rosace.plugins.program_schedulers.manual_list.manual_list import ManualListScheduler
from rosace.targets import (
    ProgramCreate, StarCreate, program_add_star, program_insert, star_add_alias,
    star_insert, targets_db_init,
)

HEADER = "Priority, Program, Star, Start_after, End_before, Series, comment\n"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture(autouse=True)
async def init_db():
    await db_init()
    await targets_db_init()


@pytest.fixture(autouse=True)
def _installation(monkeypatch):
    """Every star well above the horizon unless a test says otherwise; fixed
    installation settings, independent of the active config and of the date."""
    monkeypatch.setattr(checks, "altitude_deg", lambda ra, dec, lat, lon, at: 45.0)
    monkeypatch.setattr(checks, "MIN_ALTITUDE_DEG", 10)
    monkeypatch.setattr(checks, "MIN_MERIDIAN_DISTANCE_DEG", 0)
    monkeypatch.setattr(checks, "HORIZON_FILE", None)
    monkeypatch.setattr(manual_list_mod, "_previously_reported", set())


async def _program_with_stars(*star_names, exposure_time=60.0):
    """Creates a program and its stars (ra = 10, 20, 30... to tell them apart)."""
    program = f"prog_{_uid()}"
    pid = await program_insert(ProgramCreate(name=program))
    ids = {}
    for i, name in enumerate(star_names):
        sid = await star_insert(StarCreate(name=name, ra=10.0 * (i + 1), dec=20.0, magnitude_v=3.0))
        await program_add_star(pid, sid, exposure_time=exposure_time)
        ids[name] = sid
    return program, ids


def _ctx(path, **params) -> SchedulingContext:
    return SchedulingContext(
        now=datetime.now(timezone.utc),
        site=Site(latitude_deg=45.0, longitude_deg=5.0, elevation_m=950),
        program=None,
        params={"observation_list_path": str(path), **params},
    )


def _write(tmp_path, body: str, header: str = HEADER):
    path = tmp_path / "ObservationList.txt"
    path.write_text(header + body)
    return path


async def _observed(program: str, star: str, success: bool = True):
    """Records one observation of `star` for `program` tonight, successful
    (SEQUENCER completed) or failed (SEQUENCER error)."""
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
    else:
        await db_set_stage_status(obs.id, OrchestratorStage.SEQUENCER, "error")


class TestSelection:

    async def test_first_valid_line(self, tmp_path):
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program, _ = await _program_with_stars(a, b)
        path = _write(tmp_path, f"3, {program}, {a}, none, none, none, x\n3, {program}, {b}, none, none, none, x\n")

        candidate = await ManualListScheduler().propose(_ctx(path))

        assert candidate.queried_name == a
        assert candidate.program == program
        assert candidate.priority == 3
        assert candidate.exposure.exposure_time == 60.0
        assert candidate.reason == "ObservationList line 2 (priority 3)"

    async def test_priority_order_then_file_order(self, tmp_path):
        a, b, c = f"A_{_uid()}", f"B_{_uid()}", f"C_{_uid()}"
        program, _ = await _program_with_stars(a, b, c)
        path = _write(tmp_path, f"4, {program}, {a}, , , , x\n2, {program}, {b}, , , , x\n2, {program}, {c}, , , , x\n")

        candidate = await ManualListScheduler().propose(_ctx(path))
        assert candidate.queried_name == b

    async def test_old_three_column_file_accepted(self, tmp_path):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"2, {program}, {a}, x\n", header="Priority, Program, Star, comment\n")

        candidate = await ManualListScheduler().propose(_ctx(path))
        assert candidate.queried_name == a

    async def test_nothing_left(self, tmp_path):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"3, {program}, {a}, none, none, none, x\n")
        await _observed(program, a)

        assert await ManualListScheduler().propose(_ctx(path)) is None


class TestRepeatedStar:

    async def test_intermediate_priority_between_two_occurrences(self, tmp_path):
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program, _ = await _program_with_stars(a, b)
        path = _write(tmp_path, f"2, {program}, {a}, , , , x\n3, {program}, {b}, , , , x\n4, {program}, {a}, , , , x\n")
        planner = ManualListScheduler()

        assert (await planner.propose(_ctx(path))).queried_name == a
        await _observed(program, a)
        assert (await planner.propose(_ctx(path))).queried_name == b
        await _observed(program, b)
        second = await planner.propose(_ctx(path))
        assert second.queried_name == a and second.priority == 4
        await _observed(program, a)
        assert await planner.propose(_ctx(path)) is None

    async def test_alias_counts_as_same_star(self, tmp_path):
        a, alias = f"A_{_uid()}", f"Alias_{_uid()}"
        program, ids = await _program_with_stars(a)
        await star_add_alias(ids[a], alias)
        path = _write(tmp_path, f"3, {program}, {a}, , , , x\n3, {program}, {alias}, , , , x\n")
        await _observed(program, a)

        candidate = await ManualListScheduler().propose(_ctx(path))
        assert candidate.queried_name == alias      # second request for the same star


class TestLinesIgnored:

    @pytest.mark.parametrize("line_fields, message", [
        ("1, {p}, {a}, none, none, none, x", "priority 1 is reserved"),
        ("5, {p}, {a}, none, none, none, x", "priority 5 not in"),
        ("abc, {p}, {a}, none, none, none, x", "invalid line"),
        ("3, {p}, {a}, 8pm, none, none, x", "is not HH:MM nor YYYY-MM-DD HH:MM"),
        ("3, {p}, {a}, none, 25:00, none, x", "is not HH:MM nor YYYY-MM-DD HH:MM"),
        ("3, {p}, {a}, none, none, yes, x", "Series 'yes' is not true, false or none"),
        ("3, {p}, {a}, 2026-10-01 23:00, 2026-10-01 22:00, none, x", "Start_after is not before End_before"),
    ])
    async def test_bad_line_ignored_others_kept(self, tmp_path, caplog, line_fields, message):
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program, _ = await _program_with_stars(a, b)
        bad = line_fields.format(p=program, a=a)
        path = _write(tmp_path, f"{bad}\n4, {program}, {b}, none, none, none, x\n")

        with caplog.at_level(logging.DEBUG, logger="spectro.plugin.planner.manual_list"):
            candidate = await ManualListScheduler().propose(_ctx(path))

        assert candidate.queried_name == b
        assert any(r.levelno == logging.ERROR and message in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("problem", ["unknown star", "unknown program", "not linked"])
    async def test_unresolved_line_ignored(self, tmp_path, caplog, problem):
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program, _ = await _program_with_stars(a, b)
        other_program, _ = await _program_with_stars(f"Other_{_uid()}")
        bad = {
            "unknown star": f"2, {program}, Nowhere_{_uid()}",
            "unknown program": f"2, NoProgram_{_uid()}, {a}",
            "not linked": f"2, {other_program}, {a}",
        }[problem]
        path = _write(tmp_path, f"{bad}, none, none, none, x\n3, {program}, {b}, none, none, none, x\n")

        with caplog.at_level(logging.ERROR, logger="spectro.plugin.planner.manual_list"):
            candidate = await ManualListScheduler().propose(_ctx(path))

        assert candidate.queried_name == b
        assert any(problem in r.getMessage() for r in caplog.records)

    async def test_corrupted_exposure_time_in_db(self, tmp_path, caplog):
        import aiosqlite
        from rosace.targets import TARGETS_DB_PATH

        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program, _ = await _program_with_stars(b)
        corrupt_program = f"corrupt_{_uid()}"
        sid = await star_insert(StarCreate(name=a, ra=7.0, dec=7.0, magnitude_v=3.0))
        pid = await program_insert(ProgramCreate(name=corrupt_program))
        async with aiosqlite.connect(TARGETS_DB_PATH) as db:
            await db.execute(
                "INSERT INTO program_stars (program_id, star_id, exposure_time) VALUES (?, ?, ?)",
                (pid, sid, ""),
            )
            await db.commit()
        path = _write(tmp_path, f"2, {corrupt_program}, {a}, , , , x\n3, {program}, {b}, , , , x\n")

        with caplog.at_level(logging.ERROR, logger="spectro.plugin.planner.manual_list"):
            candidate = await ManualListScheduler().propose(_ctx(path))

        assert candidate.queried_name == b
        assert any("invalid data in targets.db" in r.getMessage() for r in caplog.records)


class TestFileProblemsNeverStopTheSession:

    async def test_missing_file(self, tmp_path, caplog):
        with caplog.at_level(logging.ERROR, logger="spectro.plugin.planner.manual_list"):
            assert await ManualListScheduler().propose(_ctx(tmp_path / "absent.txt")) is None
        assert any("unreadable" in r.getMessage() for r in caplog.records)

    async def test_missing_column(self, tmp_path, caplog):
        path = _write(tmp_path, "3, Vega\n", header="Priority, Star\n")
        with caplog.at_level(logging.ERROR, logger="spectro.plugin.planner.manual_list"):
            assert await ManualListScheduler().propose(_ctx(path)) is None
        assert any("missing column" in r.getMessage() for r in caplog.records)

    async def test_same_error_reported_once_then_at_debug(self, tmp_path, caplog):
        path = tmp_path / "absent.txt"
        planner = ManualListScheduler()
        with caplog.at_level(logging.DEBUG, logger="spectro.plugin.planner.manual_list"):
            await planner.propose(_ctx(path))
            await planner.propose(_ctx(path))
        levels = [r.levelno for r in caplog.records if "unreadable" in r.getMessage()]
        assert levels == [logging.ERROR, logging.DEBUG]

    async def test_corrected_file_is_used_at_next_call(self, tmp_path):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = tmp_path / "ObservationList.txt"
        planner = ManualListScheduler()

        assert await planner.propose(_ctx(path)) is None          # not written yet
        path.write_text(HEADER + f"3, {program}, {a}, none, none, none, x\n")
        assert (await planner.propose(_ctx(path))).queried_name == a


class TestFeasibility:

    async def test_too_low_skipped_next_line_offered(self, tmp_path, monkeypatch):
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program, _ = await _program_with_stars(a, b)      # a at ra 10, b at ra 20
        monkeypatch.setattr(checks, "altitude_deg",
                            lambda ra, dec, lat, lon, at: 5.0 if ra == 10.0 else 45.0)
        path = _write(tmp_path, f"2, {program}, {a}, , , , x\n3, {program}, {b}, , , , x\n")

        assert (await ManualListScheduler().propose(_ctx(path))).queried_name == b

    async def test_own_minimum_altitude(self, tmp_path, monkeypatch):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        monkeypatch.setattr(checks, "altitude_deg", lambda ra, dec, lat, lon, at: 25.0)
        path = _write(tmp_path, f"3, {program}, {a}, , , , x\n")

        assert await ManualListScheduler().propose(_ctx(path, min_altitude_deg=20)) is not None
        assert await ManualListScheduler().propose(_ctx(path, min_altitude_deg=30)) is None


class TestFailures:

    async def test_failed_observation_does_not_consume_its_line(self, tmp_path, monkeypatch):
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program, _ = await _program_with_stars(a, b)
        path = _write(tmp_path, f"2, {program}, {a}, none, none, none, x\n3, {program}, {b}, none, none, none, x\n")
        await _observed(program, a, success=False)

        # Within the backoff: a is skipped, b offered.
        assert (await ManualListScheduler().propose(_ctx(path))).queried_name == b

        # Backoff over: a is offered again, its line was not consumed.
        monkeypatch.setattr(checks, "FAILURE_BACKOFF_HOURS", 0)
        assert (await ManualListScheduler().propose(_ctx(path))).queried_name == a

    async def test_failure_after_success_keeps_the_line_done(self, tmp_path):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"3, {program}, {a}, none, none, none, x\n")
        await _observed(program, a, success=True)
        await _observed(program, a, success=False)

        assert await ManualListScheduler().propose(_ctx(path)) is None


@pytest.fixture
def ten_minutes(monkeypatch):
    """Every observation lasts 10 minutes: 5 x 60 s + 5 min overhead."""
    monkeypatch.setattr(checks, "OVERHEAD_SECONDS", 300)
    monkeypatch.setattr(checks, "MAX_EXPOSURE_TIME_SECONDS", 300)
    monkeypatch.setattr(checks, "MIN_EXPOSURE_COUNT", 5)
    monkeypatch.setattr(checks, "LOOKAHEAD_MINUTES", 60)
    monkeypatch.setattr(checks, "FAILURE_BACKOFF_HOURS", 1)


def _ut(minutes: float) -> str:
    """A full UT date, `minutes` from now, as written in the observation list."""
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")


class TestTimeFormat:

    def test_clock_time_is_tonight(self):
        # The astronomical night starts at local noon (10:00 or 11:00 UT in France).
        now = datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)
        assert manual_list_mod._parse_ut("21:30", now) == datetime(2026, 10, 2, 21, 30, tzinfo=timezone.utc)
        assert manual_list_mod._parse_ut("02:00", now) == datetime(2026, 10, 3, 2, 0, tzinfo=timezone.utc)
        after_midnight = datetime(2026, 10, 3, 1, 0, tzinfo=timezone.utc)
        assert manual_list_mod._parse_ut("21:30", after_midnight) == datetime(2026, 10, 2, 21, 30, tzinfo=timezone.utc)

    def test_full_date(self):
        now = datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)
        expected = datetime(2026, 10, 5, 21, 30, tzinfo=timezone.utc)
        assert manual_list_mod._parse_ut("2026-10-05 21:30", now) == expected
        assert manual_list_mod._parse_ut("2026-10-05T21:30", now) == expected

    @pytest.mark.parametrize("value", ["21h30", "25:00", "2026-13-01 21:30", "tonight"])
    def test_invalid(self, value):
        with pytest.raises(ValueError):
            manual_list_mod._parse_ut(value, datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc))


class TestWindows:

    async def test_open_window_proposed_with_its_window(self, tmp_path, ten_minutes):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"3, {program}, {a}, {_ut(-30)}, {_ut(60)}, none, x\n")

        candidate = await ManualListScheduler().propose(_ctx(path))

        assert candidate.queried_name == a and candidate.priority == 3
        assert candidate.window.end_before is not None

    async def test_booked_line_returned_when_nothing_else(self, tmp_path, ten_minutes):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"2, {program}, {a}, {_ut(31)}, none, none, x\n")

        candidate = await ManualListScheduler().propose(_ctx(path))

        assert candidate.queried_name == a and candidate.priority == 2
        assert "booked" in candidate.reason

    async def test_too_far_ahead_not_proposed(self, tmp_path, ten_minutes):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"2, {program}, {a}, {_ut(120)}, none, none, x\n")

        assert await ManualListScheduler().propose(_ctx(path)) is None

    async def test_closed_window_not_proposed(self, tmp_path, ten_minutes):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"2, {program}, {a}, none, {_ut(5)}, none, x\n")

        assert await ManualListScheduler().propose(_ctx(path)) is None

    async def test_filler_that_ends_before_the_booked_launch(self, tmp_path, ten_minutes):
        # Booked at +31 min -> launch at +26; a 10-minute filler fits.
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program, _ = await _program_with_stars(a, b)
        path = _write(tmp_path, f"2, {program}, {a}, {_ut(31)}, none, none, x\n"
                                f"3, {program}, {b}, none, none, none, x\n")

        candidate = await ManualListScheduler().propose(_ctx(path))

        assert candidate.queried_name == b and candidate.window is None

    async def test_no_filler_that_would_delay_the_booked_line(self, tmp_path, ten_minutes):
        # Booked at +13 min -> launch at +8; a 10-minute filler would end too late.
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program, _ = await _program_with_stars(a, b)
        path = _write(tmp_path, f"2, {program}, {a}, {_ut(13)}, none, none, x\n"
                                f"3, {program}, {b}, none, none, none, x\n")

        candidate = await ManualListScheduler().propose(_ctx(path))

        assert candidate.queried_name == a and "booked" in candidate.reason

    async def test_more_urgent_line_goes_first(self, tmp_path, ten_minutes):
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program, _ = await _program_with_stars(a, b)
        path = _write(tmp_path, f"3, {program}, {a}, {_ut(13)}, none, none, x\n"
                                f"2, {program}, {b}, none, none, none, x\n")

        assert (await ManualListScheduler().propose(_ctx(path))).queried_name == b


class TestSeries:

    async def test_first_element_at_line_priority_then_level_1(self, tmp_path, ten_minutes):
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program, _ = await _program_with_stars(a, b)
        path = _write(tmp_path, f"3, {program}, {a}, none, none, true, x\n"
                                f"2, {program}, {b}, none, none, none, x\n")
        await _observed(program, b)          # the priority-2 line is done

        first = await ManualListScheduler().propose(_ctx(path))
        assert first.queried_name == a and first.priority == 3

        await _observed(program, a)
        for _ in range(2):
            following = await ManualListScheduler().propose(_ctx(path))
            assert following.queried_name == a and following.priority == 1
            assert "series in progress" in following.reason
            await _observed(program, a)

    async def test_failed_element_retried_at_once(self, tmp_path, ten_minutes):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"3, {program}, {a}, none, none, true, x\n")
        await _observed(program, a)
        await _observed(program, a, success=False)
        await _observed(program, a, success=False)

        candidate = await ManualListScheduler().propose(_ctx(path))

        assert candidate.queried_name == a and candidate.priority == 1

    async def test_stopped_after_consecutive_failures_line_consumed(self, tmp_path, ten_minutes, caplog):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"3, {program}, {a}, none, none, true, x\n")
        await _observed(program, a)
        for _ in range(3):
            await _observed(program, a, success=False)

        with caplog.at_level(logging.INFO, logger="spectro.plugin.planner.manual_list"):
            assert await ManualListScheduler().propose(_ctx(path)) is None
        assert any("series stopped after 3 consecutive failures" in r.getMessage() for r in caplog.records)

    async def test_failure_limit_is_a_parameter(self, tmp_path, ten_minutes):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"3, {program}, {a}, none, none, true, x\n")
        await _observed(program, a)
        for _ in range(3):
            await _observed(program, a, success=False)

        candidate = await ManualListScheduler().propose(_ctx(path, max_series_failures=4))

        assert candidate.priority == 1

    async def test_success_resets_the_failure_count(self, tmp_path, ten_minutes):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"3, {program}, {a}, none, none, true, x\n")
        for success in (True, False, False, True, False, False):
            await _observed(program, a, success=success)

        assert (await ManualListScheduler().propose(_ctx(path))).priority == 1

    async def test_no_success_line_not_consumed(self, tmp_path, ten_minutes, monkeypatch):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"3, {program}, {a}, none, none, true, x\n")
        for _ in range(3):
            await _observed(program, a, success=False)

        # Stopped, and within the failure backoff: nothing.
        assert await ManualListScheduler().propose(_ctx(path)) is None
        # Backoff over: the series starts again at the line's priority.
        monkeypatch.setattr(checks, "FAILURE_BACKOFF_HOURS", 0)
        assert (await ManualListScheduler().propose(_ctx(path))).priority == 3

    async def test_series_interrupted_by_another_observation_is_over(self, tmp_path, ten_minutes):
        a, b = f"A_{_uid()}", f"B_{_uid()}"
        program, _ = await _program_with_stars(a, b)
        path = _write(tmp_path, f"3, {program}, {a}, none, none, true, x\n")
        await _observed(program, a)
        await _observed(program, b)          # something else was observed in between

        assert await ManualListScheduler().propose(_ctx(path)) is None

    async def test_bounded_series_must_be_feasible_until_its_end(self, tmp_path, ten_minutes, monkeypatch):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        start = datetime.now(timezone.utc)
        monkeypatch.setattr(checks, "altitude_deg",
                            lambda ra, dec, lat, lon, at: 45.0 if at < start + timedelta(minutes=60) else 5.0)
        short = _write(tmp_path, f"3, {program}, {a}, none, {_ut(45)}, true, x\n")
        assert (await ManualListScheduler().propose(_ctx(short))).queried_name == a

        long = _write(tmp_path, f"3, {program}, {a}, none, {_ut(120)}, true, x\n")
        assert await ManualListScheduler().propose(_ctx(long)) is None


class TestWindowedLinesCounting:

    async def test_success_of_an_ordinary_line_does_not_consume_a_windowed_one(self, tmp_path, ten_minutes):
        # A booked series line and an ordinary line for the same star: once the
        # ordinary line is observed, it is done, and the windowed line still waits.
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"2, {program}, {a}, {_ut(31)}, {_ut(60)}, true, booked series\n"
                                f"2, {program}, {a}, none, none, none, ordinary\n")

        first = await ManualListScheduler().propose(_ctx(path))
        assert first.window is None                      # the ordinary line fills the wait
        await _observed(program, a)

        second = await ManualListScheduler().propose(_ctx(path))
        assert second.window is not None and "booked" in second.reason

    async def test_windowed_line_done_by_a_success_inside_its_window(self, tmp_path, ten_minutes):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"2, {program}, {a}, {_ut(-20)}, {_ut(60)}, none, x\n")
        await _observed(program, a)

        assert await ManualListScheduler().propose(_ctx(path)) is None

    async def test_series_not_running_before_its_window(self, tmp_path, ten_minutes, caplog):
        a = f"A_{_uid()}"
        program, _ = await _program_with_stars(a)
        path = _write(tmp_path, f"2, {program}, {a}, {_ut(31)}, {_ut(60)}, true, x\n")
        await _observed(program, a)                      # observed earlier, outside the window

        with caplog.at_level(logging.INFO, logger="spectro.plugin.planner.manual_list"):
            candidate = await ManualListScheduler().propose(_ctx(path))
        assert candidate.priority == 2 and "booked" in candidate.reason
        assert not any("series over" in r.getMessage() for r in caplog.records)
