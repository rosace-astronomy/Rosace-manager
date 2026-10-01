"""
Selector — the SCHEDULER stage plugin of multi-program scheduling.

At every SCHEDULER step, asks each active program's planner
(plugins/program_schedulers/) for at most one candidate, checks the
candidates, and writes the chosen one into obs.scheduler_data, with the same
contract as every SCHEDULER plugin (target, queried_name, program, priority,
exposure) plus proposed_by, estimated_duration_s and reason. Nothing
selectable -> target = None, and main.py asks again later.

Active programs: every programs/*.yaml with a `scheduler` key. manual_list is
required (SchedulerFatalError without it).

Each planner runs in its own thread, with its own event loop, bounded by
planner_timeout_seconds: a slow or blocked planner never freezes Rosace, it
only loses its turn. Python cannot kill a thread, so a planner that timed out
is skipped until its thread has ended — threads never pile up.
An exception skips the program for this round; SchedulerFatalError disables
it until the session ends (for manual_list it is re-raised: the session stops).

Selection: lowest priority level first; at equal level, (a) at level 1 the
program of the previous observation keeps the hand, (b) manual_list before
the other programs, (c) program name. The selector knows nothing about the
programs' science, nor about series.
"""

import asyncio
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from ....config import SITE_ELEVATION_M, SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG
from ....database import db_get_recent_observations
from ....models import Observation, Target
from ....targets import star_get_by_name
from ...pipeline_loader import list_programs, load_program_yaml
from ...program_schedulers import (
    PRIORITY_SERIES, Candidate, ProgramScheduler, SchedulingContext, Site, load_planner,
)
from ...program_schedulers.checks import PLANNER_TIMEOUT_SECONDS, check_feasible, estimate_duration_s
from .. import SchedulerFatalError

log = logging.getLogger("spectro.plugin.scheduler")

MANUAL_LIST = "manual_list"
_executor = ThreadPoolExecutor(thread_name_prefix="planner")


class SelectorPlugin:

    def __init__(self) -> None:
        self._planners: dict[str, ProgramScheduler] = {}   # program -> planner instance
        self._busy: dict[str, Future] = {}                  # program -> thread still running after a timeout
        self._disabled: set[str] = set()                    # disabled until the session ends
        self._session_id: Optional[str] = None

    async def run(self, obs: Observation) -> Observation:
        log.info("[SCHEDULER] Selecting next target…")
        if obs.session_id != self._session_id:
            self._session_id, self._disabled = obs.session_id, set()

        now = datetime.now(timezone.utc)
        site = Site(latitude_deg=SITE_LATITUDE_DEG, longitude_deg=SITE_LONGITUDE_DEG,
                    elevation_m=SITE_ELEVATION_M)

        retained: list[tuple[str, Candidate, object, float]] = []
        for program, planner_name, params in self._active_programs():
            if program in self._disabled:
                continue
            ctx = SchedulingContext(now=now, site=site, params=params,
                                    program=None if program == MANUAL_LIST else program)
            candidate = await self._ask(program, planner_name, ctx)
            if candidate is None:
                continue
            checked = await self._verify(program, candidate, now)
            if checked is not None:
                retained.append((program, candidate, *checked))

        if not retained:
            log.info("[SCHEDULER] No target available to observe right now")
            obs.scheduler_data["target"] = None
            return obs

        recent = await db_get_recent_observations(limit=1)
        previous_program = recent[0]["program_name"] if recent else None

        def sort_key(item):
            program, candidate, _, _ = item
            keeps_hand = candidate.priority == PRIORITY_SERIES and candidate.program == previous_program
            return (candidate.priority, not keeps_hand, program != MANUAL_LIST, program)

        program, candidate, star, duration_s = min(retained, key=sort_key)
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
            f"[{len(retained)} candidate(s) retained]"
        )
        return obs

    def _active_programs(self) -> list[tuple[str, str, dict]]:
        """(program, planner name, params) of every program YAML with a `scheduler` key."""
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
            active.append((program, scheduler["name"], scheduler.get("params") or {}))
        if MANUAL_LIST not in {p for p, _, _ in active}:
            raise SchedulerFatalError(
                f"programs/{MANUAL_LIST}.yaml missing or without a `scheduler` key: "
                f"the manual list is required by the selector"
            )
        return active

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

    async def _verify(self, program: str, candidate: Candidate, now: datetime):
        """The selector's own check: (star, estimated duration) if the candidate
        is acceptable, None otherwise (with a WARNING — a rejection here means
        the planner did not apply the shared checks)."""
        def reject(why: str) -> None:
            log.warning(f"[SCHEDULER] {program}: candidate {candidate.queried_name} rejected — {why}")

        if candidate.window is not None:
            return reject("time windows are not supported yet")
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
        why = check_feasible(star, candidate.exposure, now, duration_s=duration_s)
        if why:
            return reject(why)
        return star, duration_s
