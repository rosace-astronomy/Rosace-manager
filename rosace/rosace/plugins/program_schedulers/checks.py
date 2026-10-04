"""
Shared feasibility checks for multi-program scheduling.

Used by the planners to choose their candidate and by the selector to verify
it: both apply exactly the same rules, so a planner that relies on them never
sees its candidate rejected. The rules are the installation's (floor
altitude, local horizon, mount and meridian), set in subplugin_params.selector
(conf/config.yaml), plus HORIZON_FILE and the site position (Core).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

from ...astro_math import altitude_deg, azimuth_deg, hour_angle_deg
from ...config import HORIZON_FILE, SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG, SUBPLUGIN_PARAMS
from ...database import db_get_last_failure
from ...horizon import HorizonFileError, horizon_altitude_deg, load_horizon
from ...models import ExposureConfig
from ...targets import Star
from . import PRIORITY_SERIES, TimeWindow
from ..schedulers import SchedulerFatalError
from ..sequencer_plugins.acquisition_helpers import compute_exposure_time_and_count

_params = SUBPLUGIN_PARAMS.get("selector", {})
MIN_ALTITUDE_DEG = _params.get("min_altitude_deg", 10)            # installation floor
MIN_MERIDIAN_DISTANCE_DEG = _params.get("min_meridian_distance_deg", 0)   # 0 = off
# How far past the meridian the mount keeps tracking without a flip; None = not checked.
MERIDIAN_TOLERANCE_DEG = _params.get("meridian_tolerance_deg")
OVERHEAD_SECONDS = 60 * _params.get("overhead_minutes", 5)       # pointing, guiding, calibrations
PLANNER_TIMEOUT_SECONDS = _params.get("planner_timeout_seconds", 10)
FAILURE_BACKOFF_HOURS = _params.get("failure_backoff_hours", 1)   # no retry before this delay
LOOKAHEAD_MINUTES = _params.get("lookahead_minutes", 60)   # how far ahead a booked observation is announced

# Same root keys and defaults as star_spectra_acquisition, which does the
# actual split into sub-exposures at acquisition time.
EXPOSURE_TIME_MAG0_SECONDS = SUBPLUGIN_PARAMS.get("exposure_time_mag0_seconds", 1.0)
MAX_EXPOSURE_TIME_SECONDS = SUBPLUGIN_PARAMS.get("max_exposure_time_seconds", 300)
MIN_EXPOSURE_COUNT = SUBPLUGIN_PARAMS.get("min_exposure_count", 5)

SAMPLING_STEP_SECONDS = 300   # visibility checked every 5 minutes over the observation


def estimate_duration_s(star: Star, exposure: ExposureConfig) -> Optional[float]:
    """Estimated duration of an observation (seconds): its total exposure, as
    star_spectra_acquisition will split it, plus the fixed overhead. None if
    neither an exposure time nor the star's magnitude is known."""
    if exposure.exposure_time is not None:
        unit_s, count = compute_exposure_time_and_count(
            exposure_time=exposure.exposure_time,
            max_exposure_time=MAX_EXPOSURE_TIME_SECONDS, min_exposure_count=MIN_EXPOSURE_COUNT,
        )
    elif star.magnitude_v is not None:
        unit_s, count = compute_exposure_time_and_count(
            magnitude=star.magnitude_v, exposure_time_mag0=EXPOSURE_TIME_MAG0_SECONDS,
            max_exposure_time=MAX_EXPOSURE_TIME_SECONDS, min_exposure_count=MIN_EXPOSURE_COUNT,
        )
    else:
        return None
    return unit_s * count + OVERHEAD_SECONDS


def check_feasible(
    star: Star,
    exposure: ExposureConfig,
    now: datetime,
    min_altitude_deg: Optional[float] = None,
    duration_s: Optional[float] = None,
    start: Optional[datetime] = None,
) -> Optional[str]:
    """Returns None if the observation can be made, otherwise the reason
    (one short sentence, meant for the log).

    min_altitude_deg: the planner's own scientific minimum, on top of the
    installation floor. duration_s: defaults to estimate_duration_s(); a
    planner running a series passes the whole series' duration instead.
    start: when the observation would begin, `now` by default; a booked
    observation passes its launch_time().
    """
    now = start or now
    if duration_s is None:
        duration_s = estimate_duration_s(star, exposure)
        if duration_s is None:
            return "duration unknown (no exposure time and no magnitude)"

    if MIN_MERIDIAN_DISTANCE_DEG > 0:
        hour_angle = hour_angle_deg(star.ra, SITE_LONGITUDE_DEG, now)
        if abs(hour_angle) < MIN_MERIDIAN_DISTANCE_DEG:
            return (f"too close to the meridian "
                    f"(HA {hour_angle:+.1f}° within ±{MIN_MERIDIAN_DISTANCE_DEG:.1f}°)")

    if MERIDIAN_TOLERANCE_DEG is not None:
        reason = _check_meridian_tolerance(star, now, duration_s)
        if reason is not None:
            return reason

    floor = MIN_ALTITUDE_DEG if min_altitude_deg is None else max(MIN_ALTITUDE_DEG, min_altitude_deg)
    try:
        horizon = load_horizon(HORIZON_FILE) if HORIZON_FILE else None
    except HorizonFileError as e:
        raise SchedulerFatalError(str(e)) from e
    steps =max(1, math.ceil(duration_s / SAMPLING_STEP_SECONDS))
    for i in range(steps + 1):
        offset_s = min(i * SAMPLING_STEP_SECONDS, duration_s)
        at = now + timedelta(seconds=offset_s)
        altitude = altitude_deg(star.ra, star.dec, SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG, at)
        if altitude < floor:
            return f"below minimum altitude ({altitude:.1f}° < {floor}° at +{offset_s / 60:.0f} min)"
        if horizon is not None:
            azimuth = azimuth_deg(star.ra, star.dec, SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG, at)
            horizon_altitude = horizon_altitude_deg(horizon, azimuth)
            if altitude < horizon_altitude:
                return (f"below local horizon ({altitude:.1f}° < {horizon_altitude:.1f}° "
                        f"at az {azimuth:.1f}°, +{offset_s / 60:.0f} min)")
    return None


def _check_meridian_tolerance(star: Star, start: datetime, duration_s: float) -> Optional[str]:
    """A target east of the meridian at the start (hour angle < 0) must not be
    tracked further than MERIDIAN_TOLERANCE_DEG past the meridian by the end
    of the observation: beyond that, the mount stops or flips. A target
    already west at the start is tracked away from the meridian, so it is
    always accepted."""
    ha_start = hour_angle_deg(star.ra, SITE_LONGITUDE_DEG, start)
    if ha_start >= 0:
        return None
    ha_end = hour_angle_deg(star.ra, SITE_LONGITUDE_DEG, start + timedelta(seconds=duration_s))
    if ha_end < ha_start:   # wrapped through +-180 deg (the antimeridian), not the meridian
        return None
    if ha_end > MERIDIAN_TOLERANCE_DEG:
        return (f"would track {ha_end:.1f}° past the meridian, mount tolerance "
                f"{MERIDIAN_TOLERANCE_DEG:g}° (HA {ha_start:+.1f}° → {ha_end:+.1f}°)")
    return None


def launch_time(window: Optional[TimeWindow]) -> Optional[datetime]:
    """When an observation with this window may be launched: start_after (the
    start of the science exposures) minus the fixed overhead (pointing,
    calibrations). None when the window has no start."""
    if window is None or window.start_after is None:
        return None
    return window.start_after - timedelta(seconds=OVERHEAD_SECONDS)


def check_window(window: Optional[TimeWindow], now: datetime, duration_s: float) -> tuple[str, Optional[str]]:
    """Status of an observation's time window at `now`, for an observation of
    duration_s seconds:

    - ("open", None): it can be launched now and ends before end_before;
    - ("booked", None): its launch time is still ahead, within
      LOOKAHEAD_MINUTES, and launched then it ends before end_before;
    - ("refused", reason): window closed or too short, or launch too far ahead.
    """
    if window is None:
        return "open", None
    launch = launch_time(window)
    start = max(now, launch) if launch else now
    if window.end_before is not None and start + timedelta(seconds=duration_s) > window.end_before:
        return "refused", (f"window too short or closed (would end after "
                           f"{window.end_before:%H:%M} UT)")
    if launch is None or launch <= now:
        return "open", None
    if launch - now > timedelta(minutes=LOOKAHEAD_MINUTES):
        return "refused", (f"window opens at {window.start_after:%H:%M} UT, launch at "
                           f"{launch:%H:%M} UT is more than {LOOKAHEAD_MINUTES:g} min ahead")
    return "booked", None


def backoff_applies(priority: int) -> bool:
    """The failure backoff applies to every level except 1: the next element
    of a series is retried at once, its planner bounds the retries."""
    return priority != PRIORITY_SERIES


async def check_not_recently_failed(program: str, target_name: str, now: datetime) -> Optional[str]:
    """Returns None if the target may be tried again for this program, otherwise
    the reason: its last observation tonight failed less than
    FAILURE_BACKOFF_HOURS ago. target_name is the canonical name (targets.db).
    Not applied to a level-1 candidate (backoff_applies())."""
    last_failure = await db_get_last_failure(program, target_name, now)
    if last_failure is None:
        return None
    elapsed_h = (now - last_failure).total_seconds() / 3600
    if elapsed_h < FAILURE_BACKOFF_HOURS:
        return (f"failed {elapsed_h * 60:.0f} min ago, not retried before "
                f"{FAILURE_BACKOFF_HOURS:g} h")
    return None
