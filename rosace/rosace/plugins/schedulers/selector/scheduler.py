"""
Selector — the SCHEDULER stage plugin of multi-program scheduling.

At every SCHEDULER step, asks each active program's planner
(plugins/program_schedulers/) for at most one candidate, checks the
candidates, and writes the chosen one into obs.scheduler_data.

SCHEDULER contract (full description in plugins/schedulers/blank/scheduler.py,
checked by this plugin's tests with conftest.py's assert_scheduler_contract()):
fills the mandatory keys target, queried_name, program, priority and
exposure, and both optional keys, proposed_by (the program whose planner
proposed the candidate) and estimated_duration_s. Also writes reason (the
planner's explanation), its own information, read by nobody else. Nothing
selectable -> target = None, and main.py asks again later.

Active programs: every programs/*.yaml with a `scheduler` key. manual_list is
required (SchedulerFatalError without it).

Each planner runs in its own thread, with its own event loop, bounded by
planner_timeout_seconds: a slow or blocked planner never freezes Rosace, it
only loses its turn. Python cannot kill a thread, so a planner that timed out
is skipped until its thread has ended — threads never pile up.
An exception skips the program for this round; SchedulerFatalError disables
it until the session ends (for manual_list it is re-raised: the session stops).

Time windows: a candidate whose launch time (start_after minus the fixed
overhead) is still ahead, within lookahead_minutes, is "booked". It is never
observed early: if it is the only thing left, target = None and main.py asks
again a minute later. An immediate candidate that is not more urgent than a
booked one and would end after its launch time is dropped, so the booked
observation starts on time.

Selection: lowest priority level first; at equal level, (a) at level 1 the
program of the previous observation keeps the hand, (b) manual_list before
the other programs, (c) a candidate with a window before one without, the
window closing first between two, (d) the program furthest behind its time
share, (e) program name. The selector knows nothing about the programs'
science, nor about series; the failure backoff is not applied at level 1.

Time shares: optional `share_percent` in a program's `scheduler` block. Share
obtained = the program's telescope time (ended_at - created_at, failures
included) over the last SHARE_WINDOW_DAYS, divided by the time of all
programs. Checked at the first call of each session: total above 100 % ->
SchedulerFatalError, below -> WARNING, none declared -> shares off (rule (d)
changes nothing). manual_list escapes the shares (its share is ignored), but
the observations it proposes count for the program they execute.
"""

import asyncio
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

from ....config import SITE_ELEVATION_M, SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG
from ....database import db_get_program_time_s, db_get_recent_observations
from ....models import Observation, Target
from ....targets import star_get_by_name
from ...pipeline_loader import list_programs, load_program_yaml
from ...program_schedulers import (
    PRIORITY_SERIES, Candidate, ProgramScheduler, SchedulingContext, Site, load_planner,
)
from ...program_schedulers.checks import (
    PLANNER_TIMEOUT_SECONDS, backoff_applies, check_feasible, check_not_recently_failed,
    check_window, estimate_duration_s, launch_time,
)
from .. import SchedulerFatalError

log = logging.getLogger("spectro.plugin.scheduler")

MANUAL_LIST = "manual_list"
SHARE_WINDOW_DAYS = 60
_executor = ThreadPoolExecutor(thread_name_prefix="planner")

# Tie-break rules, in the order of the sort key after the level (for the log).
_RULES = ("series keeps the hand", "manual list first", "time window", "time share", "program name")


class Program(NamedTuple):
    name: str
    planner: str
    params: dict
    share_percent: Optional[float]


class Retained(NamedTuple):
    program: str
    candidate: Candidate
    star: object
    duration_s: float
    launch: Optional[datetime]     # set for a booked candidate only


class SelectorPlugin:

    def __init__(self) -> None:
        self._planners: dict[str, ProgramScheduler] = {}   # program -> planner instance
        self._busy: dict[str, Future] = {}                  # program -> thread still running after a timeout
        self._disabled: set[str] = set()                    # disabled until the session ends
        self._session_id: Optional[str] = None
        self._reported: set[str] = set()                    # configuration errors already logged

    async def run(self, obs: Observation) -> Observation:
        log.info("[SCHEDULER] Selecting next target…")
        programs = self._active_programs()
        if obs.session_id != self._session_id:
            self._session_id, self._disabled = obs.session_id, set()
            self._check_shares(programs)

        now = datetime.now(timezone.utc)
        site = Site(latitude_deg=SITE_LATITUDE_DEG, longitude_deg=SITE_LONGITUDE_DEG,
                    elevation_m=SITE_ELEVATION_M)

        retained: list[Retained] = []
        for program in programs:
            if program.name in self._disabled:
                continue
            ctx = SchedulingContext(now=now, site=site, params=program.params,
                                    program=None if program.name == MANUAL_LIST else program.name)
            candidate = await self._ask(program.name, program.planner, ctx)
            if candidate is None:
                continue
            checked = await self._verify(program.name, candidate, now)
            if checked is not None:
                retained.append(checked)

        immediate = self._protect([r for r in retained if r.launch is None],
                                  [r for r in retained if r.launch is not None], now)
        booked = [r for r in retained if r.launch is not None]
        if not immediate and not booked:
            log.info("[SCHEDULER] No target available to observe right now")
            obs.scheduler_data["target"] = None
            return obs

        recent = await db_get_recent_observations(limit=1)
        previous_program = recent[0]["program_name"] if recent else None
        deficits = await self._share_deficits(programs, now)

        def sort_key(r: Retained):
            keeps_hand = r.candidate.priority == PRIORITY_SERIES and r.candidate.program == previous_program
            window = r.candidate.window
            closes = window.end_before.timestamp() if window and window.end_before else float("inf")
            deficit = deficits.get(r.program)
            return (r.candidate.priority, not keeps_hand, r.program != MANUAL_LIST,
                    (0, closes) if window else (1, 0.0),
                    (0, -deficit) if deficit is not None else (1, 0.0),
                    r.program)

        # A booked candidate is never observed early: it is chosen only when no
        # immediate candidate is left, and then the selector waits for it.
        ranked = sorted(immediate or booked, key=sort_key)
        best = ranked[0]
        if best.launch is not None:
            log.info(f"[SCHEDULER] Waiting for booked observation {best.candidate.queried_name} "
                     f"({best.program}, priority {best.candidate.priority}), launch at "
                     f"{best.launch:%H:%M} UT — {best.candidate.reason}")
            obs.scheduler_data["target"] = None
            return obs

        decided_by = ""
        if len(ranked) > 1:
            first, second = sort_key(best), sort_key(ranked[1])
            index = next((i for i in range(len(first)) if first[i] != second[i]), None)
            if index in (3, 4):
                decided_by = f", decided by {_RULES[index - 1]}"

        program, candidate, star, duration_s = best.program, best.candidate, best.star, best.duration_s
        obs.scheduler_data["target"] = Target(name=star.name, ra=star.ra, dec=star.dec,
                                              magnitude_v=star.magnitude_v)
        obs.scheduler_data["queried_name"] = candidate.queried_name
        obs.scheduler_data["program"] = candidate.program
        obs.scheduler_data["priority"] = candidate.priority
        obs.scheduler_data["exposure"] = candidate.exposure
        obs.scheduler_data["proposed_by"] = program
        obs.scheduler_data["estimated_duration_s"] = duration_s
        obs.scheduler_data["reason"] = candidate.reason
        log.info(
            f"[SCHEDULER] Target selected: {candidate.queried_name} ({star.name}, priority "
            f"{candidate.priority}, program {candidate.program}, proposed by {program}, "
            f"~{duration_s / 60:.0f} min) — {candidate.reason} "
            f"[{len(retained)} candidate(s) retained{decided_by}]"
        )
        return obs

    @staticmethod
    def _protect(immediate: list[Retained], booked: list[Retained], now: datetime) -> list[Retained]:
        """Drops the immediate candidates that would make a booked one start
        late: not more urgent than it, and ending after its launch time."""
        kept = []
        for r in immediate:
            end = now + timedelta(seconds=r.duration_s)
            blocking = next((b for b in booked
                             if r.candidate.priority >= b.candidate.priority and end > b.launch), None)
            if blocking is None:
                kept.append(r)
            else:
                log.info(f"[SCHEDULER] {r.program}: candidate {r.candidate.queried_name} set aside — "
                         f"would end after the launch of booked {blocking.candidate.queried_name} "
                         f"({blocking.launch:%H:%M} UT)")
        return kept

    def _check_shares(self, programs: list["Program"]) -> None:
        """Time shares, checked at the first call of each session."""
        declared = {p.name: p.share_percent for p in programs if p.share_percent is not None}
        if not declared:
            log.info("[SCHEDULER] No time share declared: shares not used")
            return
        total = sum(declared.values())
        detail = ", ".join(f"{name} {share:g} %" for name, share in sorted(declared.items()))
        if total > 100:
            raise SchedulerFatalError(
                f"Time shares add up to {total:g} % (more than 100 %): {detail} — "
                f"fix share_percent in the program YAMLs"
            )
        if total < 100:
            log.warning(f"[SCHEDULER] Time shares add up to {total:g} % only: {detail}")
        else:
            log.info(f"[SCHEDULER] Time shares: {detail}")

    async def _share_deficits(self, programs: list["Program"], now: datetime) -> dict[str, float]:
        """Target share minus share obtained (percent) over SHARE_WINDOW_DAYS,
        for the programs that declare a share; empty when none does."""
        declared = {p.name: p.share_percent for p in programs if p.share_percent is not None}
        if not declared:
            return {}
        used = await db_get_program_time_s(now - timedelta(days=SHARE_WINDOW_DAYS))
        total = sum(used.values())
        return {name: share - (100 * used.get(name, 0.0) / total if total else 0.0)
                for name, share in declared.items()}

    def _active_programs(self) -> list[Program]:
        """Every program YAML with a `scheduler` key."""
        active = []
        for program in list_programs():
            try:
                scheduler = (load_program_yaml(program) or {}).get("scheduler")
            except Exception as e:
                log.error(f"[SCHEDULER] programs/{program}.yaml unreadable, program ignored — {e}")
                continue
            if not scheduler:
                continue
            if not isinstance(scheduler, dict) or not scheduler.get("name"):
                log.error(f"[SCHEDULER] programs/{program}.yaml: `scheduler` needs a `name`, "
                          f"program ignored")
                continue
            active.append(Program(program, scheduler["name"], scheduler.get("params") or {},
                                  self._share_percent(program, scheduler)))
        if MANUAL_LIST not in {p.name for p in active}:
            raise SchedulerFatalError(
                f"programs/{MANUAL_LIST}.yaml missing or without a `scheduler` key: "
                f"the manual list is required by the selector"
            )
        return active

    def _share_percent(self, program: str, scheduler: dict) -> Optional[float]:
        """The program's share_percent, None if absent or unusable (logged)."""
        share = scheduler.get("share_percent")
        if share is None:
            return None
        if program == MANUAL_LIST:
            self._report_once(f"programs/{MANUAL_LIST}.yaml: share_percent ignored — "
                              f"manual observations escape the time shares")
            return None
        if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 <= share <= 100:
            self._report_once(f"programs/{program}.yaml: share_percent must be a number "
                              f"between 0 and 100, got {share!r} — ignored")
            return None
        return float(share)

    def _report_once(self, message: str) -> None:
        """ERROR the first time in this process, DEBUG afterwards."""
        level = logging.DEBUG if message in self._reported else logging.ERROR
        self._reported.add(message)
        log.log(level, f"[SCHEDULER] {message}")

    async def _ask(self, program: str, planner_name: str, ctx: SchedulingContext) -> Optional[Candidate]:
        """Calls one planner in its own thread, bounded by PLANNER_TIMEOUT_SECONDS."""
        busy = self._busy.get(program)
        if busy is not None and not busy.done():
            log.warning(f"[SCHEDULER] {program}: planner still running since an earlier "
                        f"timeout, skipped")
            return None
        self._busy.pop(program, None)

        try:
            if program not in self._planners:
                self._planners[program] = load_planner(planner_name)
            planner = self._planners[program]
        except ValueError as e:
            log.error(f"[SCHEDULER] {program}: {e}, program ignored")
            return None

        future = _executor.submit(asyncio.run, planner.propose(ctx))
        try:
            candidate = await asyncio.wait_for(asyncio.wrap_future(future), PLANNER_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self._busy[program] = future
            log.warning(f"[SCHEDULER] {program}: no answer within {PLANNER_TIMEOUT_SECONDS} s, "
                        f"skipped this round")
            return None
        except SchedulerFatalError as e:
            if program == MANUAL_LIST:
                raise
            self._disabled.add(program)
            log.error(f"[SCHEDULER] {program}: {e} — program disabled until the session ends")
            return None
        except Exception as e:
            log.warning(f"[SCHEDULER] {program}: planner error, skipped this round — "
                        f"{type(e).__name__}: {e}")
            return None

        if candidate is not None and not isinstance(candidate, Candidate):
            log.warning(f"[SCHEDULER] {program}: answer is not a Candidate, ignored")
            return None
        return candidate

    async def _verify(self, program: str, candidate: Candidate, now: datetime) -> Optional[Retained]:
        """The selector's own check: the candidate with its star, estimated
        duration and, when booked, launch time — or None (with a WARNING: a
        rejection here means the planner did not apply the shared checks)."""
        def reject(why: str) -> None:
            log.warning(f"[SCHEDULER] {program}: candidate {candidate.queried_name} rejected — {why}")

        try:
            load_program_yaml(candidate.program)
        except Exception as e:
            return reject(f"program '{candidate.program}' unusable: {e}")
        star = await star_get_by_name(candidate.queried_name)
        if star is None:
            return reject("unknown star in targets.db")
        duration_s = estimate_duration_s(star, candidate.exposure)
        if duration_s is None:
            return reject("duration unknown (no exposure time and no magnitude)")
        status, why = check_window(candidate.window, now, duration_s)
        if status == "refused":
            return reject(why)
        launch = launch_time(candidate.window) if status == "booked" else None
        why = check_feasible(star, candidate.exposure, now, duration_s=duration_s, start=launch)
        if not why and backoff_applies(candidate.priority):
            why = await check_not_recently_failed(candidate.program, star.name, now)
        if why:
            return reject(why)
        return Retained(program, candidate, star, duration_s, launch)
