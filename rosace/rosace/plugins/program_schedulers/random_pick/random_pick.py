"""
Random pick planner: proposes a target of its program at random — the
simplest real planner, shared by any program that has no planner of its own
yet (PN, Cataclysmic).

At every call, the program's targets (targets.db) are shuffled and the first
one that passes is proposed: no successful observation tonight (a target is
observed at most once per night) nor in the last min_interval_days, not failed in the last
failure_backoff_hours, and feasible now (checks.check_feasible, with the
program's optional min_altitude_deg). The exposure is the one set in
program_stars, otherwise computed from the magnitude.

Params (scheduler.params of the program's YAML): priority (3: ordinary, so
that the time shares break the ties with the other programs' ordinary
candidates), min_interval_days (0: only the once-per-night rule),
min_altitude_deg (none: the installation floor only).
"""

import logging
import random
from datetime import timedelta

from ....database import db_get_last_success_per_target, db_get_successes_for_target
from ....models import ExposureConfig
from ....targets import program_get_by_name, program_get_stars
from .. import PRIORITY_ORDINARY, Candidate, SchedulingContext
from ..checks import check_feasible, check_not_recently_failed

log = logging.getLogger("spectro.plugin.planner.random_pick")


class RandomPickScheduler:

    async def propose(self, ctx: SchedulingContext) -> Candidate | None:
        program = await program_get_by_name(ctx.program) if ctx.program else None
        if program is None:
            log.warning(f"[SCHEDULER] Program '{ctx.program}' not found in targets.db")
            return None

        priority = int(ctx.params.get("priority", PRIORITY_ORDINARY))
        min_altitude_deg = ctx.params.get("min_altitude_deg")
        min_interval_days = float(ctx.params.get("min_interval_days", 0))
        min_interval = timedelta(days=min_interval_days)
        last_success = await db_get_last_success_per_target(program.name) if min_interval else {}
        stars = await program_get_stars(program.id)
        random.shuffle(stars)
        for star in stars:
            successes, _ = await db_get_successes_for_target(program.name, star.name, ctx.now)
            if successes > 0:
                continue
            last = last_success.get(star.name)
            if last is not None and ctx.now - last < min_interval:
                continue
            if await check_not_recently_failed(program.name, star.name, ctx.now) is not None:
                continue
            exposure = ExposureConfig(exposure_time=star.exposure_time)
            if check_feasible(star, exposure, ctx.now, min_altitude_deg) is not None:
                continue
            return Candidate(
                queried_name=star.name,
                program=program.name,
                priority=priority,
                exposure=exposure,
                reason=f"random pick among the {len(stars)} targets of {program.name} (not observed tonight"
                        + (f" nor in the last {min_interval_days:g} d" if min_interval else "") + ", feasible now)",
            )
        return None
