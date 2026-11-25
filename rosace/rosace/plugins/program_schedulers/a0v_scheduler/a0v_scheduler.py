"""
A0V planner: one reference star (CALSPEC or clean A0V) at the start of the
night, then one every interval_hours, for the night's atmospheric response.

The program's stars are its targets in targets.db (program_stars). At every
call, from rosace.db's history only (no state kept):

- the program's last success is tonight and less than interval_hours old ->
  nothing; its last failure tonight came after it and is less than
  retry_minutes old -> nothing (under clouds, one attempt per pause, not one
  per star);
- otherwise the candidates are the stars not failed recently
  (checks.check_not_recently_failed) and feasible now (checks.check_feasible:
  altitude, local horizon, meridian rules, Moon, plus the optional
  min_altitude_deg and min_moon_distance_deg),
  whose air mass at mid-observation (1 / sin(altitude)) is at most
  max_airmass — none: nothing;
- rotation: among the candidates within airmass_margin of the lowest air mass,
  the one observed least recently (never observed first), then the highest,
  then the name — the measurement stays nearly as good while the reference
  stars take turns.

Priority 2 (HIGH: without a reference star the night has no atmospheric
measurement). The first one of the night gets a time window ending
first_window_minutes after the night's first observation (any program), so
that it wins a level-2 tie with another program's candidate (the selector
puts a windowed candidate first); once that window has passed (clouds), it is
still proposed at level 2, without a window. The exposure is the one set in
program_stars, otherwise computed from the magnitude.

Params (scheduler.params of the program's YAML): interval_hours (4),
first_window_minutes (90), max_airmass (1.5), airmass_margin (0.1),
retry_minutes (30), min_altitude_deg (none: the installation floor only),
min_moon_distance_deg (none: the installation's only).
"""

import logging
import math
from datetime import datetime, timedelta
from typing import NamedTuple, Optional

from ....database import db_get_first_observation_time, db_get_last_program_failure, db_get_last_success_per_target
from ....models import ExposureConfig
from ....targets import StarInProgram, program_get_by_name, program_get_stars
from ....time_helpers import astronomical_day_start_utc
from .. import PRIORITY_HIGH, Candidate, SchedulingContext, TimeWindow
from .. import checks

log = logging.getLogger("spectro.plugin.planner.a0v_scheduler")


class _Reachable(NamedTuple):
    star: StarInProgram
    exposure: ExposureConfig
    altitude_deg: float
    airmass: float


class A0vSchedulerScheduler:

    async def propose(self, ctx: SchedulingContext) -> Candidate | None:
        program = await program_get_by_name(ctx.program) if ctx.program else None
        if program is None:
            log.warning(f"[SCHEDULER] Program '{ctx.program}' not found in targets.db")
            return None

        params = ctx.params
        interval = timedelta(hours=float(params.get("interval_hours", 4)))
        first_window = timedelta(minutes=float(params.get("first_window_minutes", 90)))
        max_airmass = float(params.get("max_airmass", 1.5))
        airmass_margin = float(params.get("airmass_margin", 0.1))
        retry = timedelta(minutes=float(params.get("retry_minutes", 30)))
        min_altitude_deg = params.get("min_altitude_deg")
        min_moon_distance_deg = params.get("min_moon_distance_deg")

        last_success_per_star = await db_get_last_success_per_target(program.name)
        last_success = max(last_success_per_star.values(), default=None)
        night_start = datetime.fromisoformat(astronomical_day_start_utc(ctx.now))
        tonight = last_success is not None and last_success >= night_start
        if tonight and ctx.now - last_success < interval:
            return None
        last_failure = await db_get_last_program_failure(program.name, ctx.now)
        if (last_failure is not None and (last_success is None or last_failure > last_success)
                and ctx.now - last_failure < retry):
            return None

        reachable = await self._reachable(await program_get_stars(program.id), ctx, program.name,
                                          max_airmass, min_altitude_deg, min_moon_distance_deg)
        if not reachable:
            log.debug(f"[SCHEDULER] {program.name}: no reference star at air mass <= {max_airmass:g}")
            return None

        best_airmass = min(r.airmass for r in reachable)
        near_best = [r for r in reachable if r.airmass <= best_airmass + airmass_margin]
        never = float("-inf")
        chosen = min(near_best, key=lambda r: (
            last_success_per_star[r.star.name].timestamp() if r.star.name in last_success_per_star else never,
            -r.altitude_deg, r.star.name))

        window = None
        if tonight:
            reason = f"last reference star {(ctx.now - last_success).total_seconds() / 3600:.1f} h ago"
        else:
            reason = "first reference star of the night"
            window = await self._first_window(ctx, chosen, first_window)
        last_seen = last_success_per_star.get(chosen.star.name)
        reason += (f", air mass {chosen.airmass:.2f}; least recently observed of {len(near_best)} within "
                   f"X {best_airmass:.2f}+{airmass_margin:g} ("
                   + (f"last {last_seen:%Y-%m-%d}" if last_seen else "never observed") + ")")
        return Candidate(
            queried_name=chosen.star.name,
            program=program.name,
            priority=PRIORITY_HIGH,
            exposure=chosen.exposure,
            window=window,
            reason=reason,
        )

    @staticmethod
    async def _reachable(stars: list[StarInProgram], ctx: SchedulingContext, program: str,
                         max_airmass: float, min_altitude_deg: Optional[float],
                         min_moon_distance_deg: Optional[float]) -> list[_Reachable]:
        """The stars that may be observed now, with their air mass at mid-observation."""
        reachable = []
        for star in stars:
            if await checks.check_not_recently_failed(program, star.name, ctx.now) is not None:
                continue
            exposure = ExposureConfig(exposure_time=star.exposure_time)
            if checks.check_feasible(star, exposure, ctx.now, min_altitude_deg,
                                     min_moon_distance_deg=min_moon_distance_deg) is not None:
                continue
            duration_s = checks.estimate_duration_s(star, exposure)
            altitude = checks.altitude_deg(star.ra, star.dec, ctx.site.latitude_deg, ctx.site.longitude_deg,
                                           ctx.now + timedelta(seconds=duration_s / 2))
            if altitude <= 0:
                continue
            airmass = 1 / math.sin(math.radians(altitude))
            if airmass <= max_airmass:
                reachable.append(_Reachable(star, exposure, altitude, airmass))
        return reachable

    @staticmethod
    async def _first_window(ctx: SchedulingContext, chosen: _Reachable,
                            first_window: timedelta) -> Optional[TimeWindow]:
        """Window of the night's first reference star: ends first_window after
        the night's first observation (now if none yet); None once it can no
        longer be met."""
        first = await db_get_first_observation_time(ctx.now) or ctx.now
        window = TimeWindow(end_before=first + first_window)
        duration_s = checks.estimate_duration_s(chosen.star, chosen.exposure)
        status, _ = checks.check_window(window, ctx.now, duration_s)
        return window if status == "open" else None
