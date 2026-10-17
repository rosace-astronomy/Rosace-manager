"""
Tests for the Be stars planner (BeSchedulerScheduler): levels, order, BeSS vs
Rosace history, guards, and the BeSS robot file (copy, download, fallback).
Same pattern as test_blank_program.py: sky computation replaced by fixed
values, history written into rosace.db as real observations, robot file
written in tmp_path, download replaced by a fake (no network access).
"""

import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from rosace.database import db_init, db_insert_observation, db_insert_session, db_set_stage_status, db_stamp_stage
from rosace.models import Observation, OrchestratorStage, SessionContext, Target
from rosace.plugins.program_schedulers import (
    PRIORITY_FILLER, PRIORITY_HIGH, PRIORITY_ORDINARY, Candidate, SchedulingContext, Site,
)
from rosace.plugins.program_schedulers import checks
from rosace.plugins.program_schedulers.be_scheduler import be_scheduler
from rosace.plugins.program_schedulers.be_scheduler.be_scheduler import BeSchedulerScheduler
from rosace.targets import (
    ProgramCreate, StarCreate, program_add_star, program_insert, star_add_alias, star_insert, targets_db_init,
)

# In the past on purpose: the test database is shared, and observations dated in the
# future would look like "the latest observation" to other tests.
NOW = datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc)
HEADER = ("File created on Saturday 3rd of October 2026 03:07:50 AM\n"
          "Name           HD      RA            DEC           Magn    Last_Obs              Prior.  Obs_Period  \n")


def _uid() -> str:
    return uuid.uuid4().hex[:6]


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
    monkeypatch.setattr(checks, "OVERHEAD_SECONDS", 300)
    monkeypatch.setattr(checks, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
    monkeypatch.setattr(checks, "MAX_EXPOSURE_TIME_SECONDS", 300)
    monkeypatch.setattr(checks, "MIN_EXPOSURE_COUNT", 5)
    monkeypatch.setattr(checks, "FAILURE_BACKOFF_HOURS", 1)
    monkeypatch.setattr(be_scheduler, "_last_download_attempt", None)
    monkeypatch.setattr(be_scheduler, "_reported", set())


@pytest.fixture
def no_download(monkeypatch):
    """Fails the test if a download is attempted."""
    async def forbidden(url):
        raise AssertionError("unexpected download")
    monkeypatch.setattr(be_scheduler, "_download", forbidden)


def _robot_line(name, last="0000-00-00 00:00:00", period=365, hd="0", mag=5.0):
    return f"{name:<15}{hd:<8}+05 37 38.7   +21 08 33.2   {mag:<8.2f}{last}   3       {period:<12}\n"


def _write_robot(path, *lines, age_hours=0.0):
    path.write_text(HEADER + "".join(lines), encoding="utf-8")
    if age_hours:
        old = time.time() - age_hours * 3600
        os.utime(path, (old, old))
    return path


def _ctx(program, robot_path, now=NOW, **params) -> SchedulingContext:
    return SchedulingContext(
        now=now,
        site=Site(latitude_deg=45.0, longitude_deg=5.0, elevation_m=950),
        program=program,
        params={"robot_path": str(robot_path), **params},
    )


async def _program(*stars):
    """stars: (name, magnitude) pairs, each at its own RA (10, 20, ...)."""
    program = f"be_{_uid()}"
    pid = await program_insert(ProgramCreate(name=program))
    for i, (name, magnitude) in enumerate(stars):
        sid = await star_insert(StarCreate(name=name, ra=10.0 * (i + 1), dec=20.0, magnitude_v=magnitude))
        await program_add_star(pid, sid)
    return program


async def _observed(program, star, created_at, success=True):
    session = SessionContext(site_name="Test")
    await db_insert_session(session)
    obs = Observation(
        id=_uid(), created_at=created_at, session_id=session.session_id,
        scheduler_data={"target": Target(name=star, ra=1.0, dec=1.0), "queried_name": star,
                        "program": program, "priority": 3},
    )
    await db_insert_observation(obs, session.session_id)
    if success:
        await db_stamp_stage(obs.id, OrchestratorStage.SEQUENCER)
    else:
        await db_set_stage_status(obs.id, OrchestratorStage.SEQUENCER, "error")


def _bess_date(days_ago):
    return f"{NOW - timedelta(days=days_ago):%Y-%m-%d %H:%M:%S}"


async def test_proposes_a_valid_candidate(tmp_path, no_download):
    star = f"S{_uid()}"
    program = await _program((star, 3.0))
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(star, last=_bess_date(40)))

    candidate = await BeSchedulerScheduler().propose(_ctx(program, robot))

    assert isinstance(candidate, Candidate)
    assert candidate.queried_name == star and candidate.program == program
    assert candidate.priority == PRIORITY_ORDINARY
    assert "last observed 40 d ago" in candidate.reason and "period 30 d" in candidate.reason


async def test_levels(tmp_path, no_download):
    uid = _uid()
    high, ordinary, filler = f"H{uid}", f"O{uid}", f"F{uid}"
    robot = _write_robot(tmp_path / "robot.txt",
                         _robot_line(high, last=_bess_date(10), period=7),     # BeSS asks 7 d: overdue, level 2
                         _robot_line(ordinary, last=_bess_date(40)),           # overdue on our 30 d: level 3
                         _robot_line(filler, last=_bess_date(5)))              # not due: level 4
    planner = BeSchedulerScheduler()

    for star, level in ((high, PRIORITY_HIGH), (ordinary, PRIORITY_ORDINARY), (filler, PRIORITY_FILLER)):
        program = await _program((star, 3.0))
        candidate = await planner.propose(_ctx(program, robot))
        assert (candidate.queried_name, candidate.priority) == (star, level)


async def test_min_period_floors_a_short_bess_period(tmp_path, no_download):
    uid = _uid()
    recent, old = f"R{uid}", f"O{uid}"
    robot = _write_robot(tmp_path / "robot.txt",
                         _robot_line(recent, last=_bess_date(5), period=1),   # due on 1 d, not on 10 d
                         _robot_line(old, last=_bess_date(12), period=1))     # due on 10 d too
    planner = BeSchedulerScheduler()

    candidate = await planner.propose(_ctx(await _program((recent, 3.0)), robot, min_period_days=10))
    assert candidate.priority == PRIORITY_FILLER
    candidate = await planner.propose(_ctx(await _program((old, 3.0)), robot, min_period_days=10))
    assert candidate.priority == PRIORITY_HIGH
    assert "period 10 d (BeSS 1 d)" in candidate.reason


async def test_bess_period_longer_than_ours_is_level_3(tmp_path, no_download):
    star = f"S{_uid()}"
    program = await _program((star, 3.0))
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(star, last=_bess_date(40), period=365))

    assert (await BeSchedulerScheduler().propose(_ctx(program, robot))).priority == PRIORITY_ORDINARY


async def test_order_lateness_then_altitude_then_brightness(tmp_path, monkeypatch, no_download):
    uid = _uid()
    late, low, high, bright, faint = (f"{c}{uid}" for c in "LWHBF")
    program = await _program((late, 5.0), (low, 3.0), (high, 4.0), (bright, 2.0), (faint, 4.5))
    robot = _write_robot(tmp_path / "robot.txt",
                         _robot_line(late, last=_bess_date(100)),    # lateness 3.3 -> step 6
                         _robot_line(low, last=_bess_date(46)),      # 1.53 -> step 3
                         _robot_line(high, last=_bess_date(50)),     # 1.67 -> step 3
                         _robot_line(bright, last=_bess_date(31)),   # 1.03 -> step 2
                         _robot_line(faint, last=_bess_date(32)))    # 1.07 -> step 2
    # RA 20 (low) is lower in the sky than RA 30 (high).
    monkeypatch.setattr(checks, "altitude_deg", lambda ra, dec, lat, lon, at: 30.0 if ra == 20.0 else 60.0)
    planner = BeSchedulerScheduler()

    order = []
    for _ in range(5):
        candidate = await planner.propose(_ctx(program, robot))
        order.append(candidate.queried_name)
        await _observed(program, candidate.queried_name, NOW - timedelta(minutes=10))
    assert order == [late, high, low, bright, faint]


async def test_never_observed_first(tmp_path, no_download):
    uid = _uid()
    old, never = f"O{uid}", f"N{uid}"
    program = await _program((old, 2.0), (never, 5.0))
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(old, last=_bess_date(300)), _robot_line(never))

    candidate = await BeSchedulerScheduler().propose(_ctx(program, robot))

    assert candidate.queried_name == never and "never observed" in candidate.reason


async def test_most_recent_of_bess_and_rosace(tmp_path, no_download):
    uid = _uid()
    star = f"S{uid}"
    program = await _program((star, 3.0))
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(star, last=_bess_date(40)))
    planner = BeSchedulerScheduler()

    await _observed(program, star, NOW - timedelta(days=60))   # older than BeSS: BeSS wins
    assert (await planner.propose(_ctx(program, robot))).priority == PRIORITY_ORDINARY

    await _observed(program, star, NOW - timedelta(days=3))    # newer than BeSS: Rosace wins
    candidate = await planner.propose(_ctx(program, robot))
    assert candidate.priority == PRIORITY_FILLER and "last observed 3 d ago" in candidate.reason


async def test_failed_rosace_observation_does_not_count(tmp_path, no_download):
    star = f"S{_uid()}"
    program = await _program((star, 3.0))
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(star, last=_bess_date(40)))

    await _observed(program, star, NOW - timedelta(days=3), success=False)

    assert (await BeSchedulerScheduler().propose(_ctx(program, robot))).priority == PRIORITY_ORDINARY


async def test_matched_by_hd_alias(tmp_path, no_download):
    uid = _uid()
    star = f"* S{uid}"
    program = await _program((star, 3.0))
    sid_alias = f"HD 9{uid[:5].translate(str.maketrans('abcdef', '123456'))}"
    from rosace.targets import star_get_by_name
    await star_add_alias((await star_get_by_name(star)).id, sid_alias)
    robot = _write_robot(tmp_path / "robot.txt",
                         _robot_line(f"BESS{uid}", hd=sid_alias.split()[1], last=_bess_date(5)))

    assert (await BeSchedulerScheduler().propose(_ctx(program, robot))).priority == PRIORITY_FILLER


async def test_limit_magnitude(tmp_path, no_download):
    star = f"S{_uid()}"
    program = await _program((star, 7.5))
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(star))

    assert await BeSchedulerScheduler().propose(_ctx(program, robot)) is None
    assert (await BeSchedulerScheduler().propose(_ctx(program, robot, limit_magnitude=8))).queried_name == star


async def test_observed_tonight_never_proposed_again(tmp_path, no_download):
    star = f"S{_uid()}"
    program = await _program((star, 3.0))
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(star))

    await _observed(program, star, NOW - timedelta(hours=2))

    assert await BeSchedulerScheduler().propose(_ctx(program, robot)) is None


async def test_recent_failure_backoff(tmp_path, no_download):
    star = f"S{_uid()}"
    program = await _program((star, 3.0))
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(star))

    # The failure time is recorded at the real current time (ended_at): decide at that time too.
    # Created now, not some minutes ago: a creation before local noon would fall in the
    # previous astronomical day when the test runs just after noon.
    now = datetime.now(timezone.utc)
    await _observed(program, star, now, success=False)

    assert await BeSchedulerScheduler().propose(_ctx(program, robot, now=now)) is None


async def test_duration_guard(tmp_path, caplog, no_download):
    uid = _uid()
    slow, quick = f"Z{uid}", f"Q{uid}"
    program = await _program((slow, 6.0), (quick, 3.0))   # mag 6 at 1 s for mag 0: 251 s x 5 + 5 min = 26 min
    # slow never observed, so first in order; quick observed 40 days ago.
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(slow), _robot_line(quick, last=_bess_date(40)))
    planner = BeSchedulerScheduler()

    with caplog.at_level(logging.WARNING, logger="spectro.plugin.planner.be_scheduler"):
        assert (await planner.propose(_ctx(program, robot, max_duration_minutes=20))).queried_name == quick
        await planner.propose(_ctx(program, robot, max_duration_minutes=20))
    warnings = [r for r in caplog.records if slow in r.getMessage()]
    assert len(warnings) == 1 and "max_duration_minutes" in warnings[0].getMessage()


async def test_infeasible_star_skipped(tmp_path, monkeypatch, no_download):
    uid = _uid()
    low, ok = f"L{uid}", f"K{uid}"
    program = await _program((low, 3.0), (ok, 5.0))   # low at RA 10
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(low), _robot_line(ok))
    monkeypatch.setattr(checks, "altitude_deg", lambda ra, dec, lat, lon, at: 5.0 if ra == 10.0 else 45.0)

    assert (await BeSchedulerScheduler().propose(_ctx(program, robot))).queried_name == ok
    assert await BeSchedulerScheduler().propose(_ctx(program, robot, min_altitude_deg=50)) is None


async def test_no_robot_file_uses_rosace_history_only(tmp_path, monkeypatch, caplog):
    async def unreachable(url):
        raise OSError("network unreachable")
    monkeypatch.setattr(be_scheduler, "_download", unreachable)
    star = f"S{_uid()}"
    program = await _program((star, 3.0))
    await _observed(program, star, NOW - timedelta(days=3))

    with caplog.at_level(logging.WARNING, logger="spectro.plugin.planner.be_scheduler"):
        candidate = await BeSchedulerScheduler().propose(_ctx(program, tmp_path / "missing.txt"))

    assert candidate.priority == PRIORITY_FILLER and "BeSS never" in candidate.reason
    assert "download failed" in caplog.text and "Rosace's own history only" in caplog.text


async def test_failed_download_keeps_previous_copy(tmp_path, monkeypatch, caplog):
    attempts = []

    async def unreachable(url):
        attempts.append(url)
        raise OSError("timeout")
    monkeypatch.setattr(be_scheduler, "_download", unreachable)
    star = f"S{_uid()}"
    program = await _program((star, 3.0))
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(star, last=_bess_date(5)), age_hours=20)
    planner = BeSchedulerScheduler()

    with caplog.at_level(logging.WARNING, logger="spectro.plugin.planner.be_scheduler"):
        assert (await planner.propose(_ctx(program, robot))).priority == PRIORITY_FILLER
        await planner.propose(_ctx(program, robot))

    assert len(attempts) == 1   # no new attempt before RETRY_MINUTES
    assert "previous copy kept" in caplog.text
    assert star in robot.read_text()


async def test_successful_download_replaces_old_copy(tmp_path, monkeypatch):
    star = f"S{_uid()}"
    program = await _program((star, 3.0))
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(star, last=_bess_date(40)), age_hours=20)

    async def fresh(url):
        return HEADER + _robot_line(star, last=_bess_date(2))
    monkeypatch.setattr(be_scheduler, "_download", fresh)

    assert (await BeSchedulerScheduler().propose(_ctx(program, robot))).priority == PRIORITY_FILLER
    assert _bess_date(2) in robot.read_text()


async def test_invalid_download_is_not_written(tmp_path, monkeypatch, caplog):
    star = f"S{_uid()}"
    program = await _program((star, 3.0))
    robot = _write_robot(tmp_path / "robot.txt", _robot_line(star, last=_bess_date(40)), age_hours=20)

    async def error_page(url):
        return "<html>Service unavailable</html>"
    monkeypatch.setattr(be_scheduler, "_download", error_page)

    with caplog.at_level(logging.WARNING, logger="spectro.plugin.planner.be_scheduler"):
        assert (await BeSchedulerScheduler().propose(_ctx(program, robot))).priority == PRIORITY_ORDINARY
    assert "not a BeSS robot file" in caplog.text
    assert _bess_date(40) in robot.read_text()


def test_parse_robot_file_skips_unreadable_lines():
    text = HEADER + _robot_line("gam Cas", last="2026-09-14 21:39:03", period=30, hd="5394") \
        + "garbage line\n" + _robot_line("ALCYONE", period=0)
    entries = be_scheduler.parse_robot_file(text)
    assert entries["gam cas"] == entries["hd 5394"]
    assert entries["gam cas"].last_obs == datetime(2026, 9, 14, 21, 39, 3, tzinfo=timezone.utc)
    assert entries["gam cas"].period_days == 30
    assert entries["alcyone"].last_obs is None and entries["alcyone"].period_days is None
    assert len(entries) == 3


async def test_unknown_program(tmp_path, no_download):
    robot = _write_robot(tmp_path / "robot.txt")
    assert await BeSchedulerScheduler().propose(_ctx(f"nowhere_{_uid()}", robot)) is None
