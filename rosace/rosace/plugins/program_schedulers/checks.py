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
from ...models import ExposureConfig
from ...targets import Star
from ..schedulers.scheduler_rosace_2026.horizon import horizon_altitude_deg, load_horizon
from ..sequencer_plugins.acquisition_helpers import compute_exposure_time_and_count

_params = SUBPLUGIN_PARAMS.get("selector", {})
MIN_ALTITUDE_DEG = _params.get("min_altitude_deg", 10)            # installation floor
MIN_MERIDIAN_DISTANCE_DEG = _params.get("min_meridian_distance_deg", 0)   # 0 = off
OVERHEAD_SECONDS = 60 * _params.get("overhead_minutes", 5)       # pointing, guiding, calibrations
PLANNER_TIMEOUT_SECONDS = _params.get("planner_timeout_seconds", 10)
FAILURE_BACKOFF_HOURS = _params.get("failure_backoff_hours", 1)   # no retry before this delay

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
) -> Optional[str]:
    """Returns None if the observation can be made now, otherwise the reason
    (one short sentence, meant for the log).

    min_altitude_deg: the planner's own scientific minimum, on top of the
    installation floor. duration_s: defaults to estimate_duration_s(); a
    planner running a series passes the whole series' duration instead.
    """
    if duration_s is None:
        duration_s = estimate_duration_s(star, exposure)
        if duration_s is None:
            return "duration unknown (no exposure time and no magnitude)"

    if MIN_MERIDIAN_DISTANCE_DEG > 0:
        hour_angle = hour_angle_deg(star.ra, SITE_LONGITUDE_DEG, now)
        if abs(hour_angle) < MIN_MERIDIAN_DISTANCE_DEG:
            return (f"too close to the meridian "
                    f"(HA {hour_angle:+.1f}° within ±{MIN_MERIDIAN_DISTANCE_DEG:.1f}°)")

    floor = MIN_ALTITUDE_DEG if min_altitude_deg is None else max(MIN_ALTITUDE_DEG, min_altitude_deg)
    horizon = load_horizon(HORIZON_FILE) if HORIZON_FILE else None
    steps = max(1, math.ceil(duration_s / SAMPLING_STEP_SECONDS))
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


async def check_not_recently_failed(program: str, target_name: str, now: datetime) -> Optional[str]:
    """Returns None if the target may be tried again for this program, otherwise
    the reason: its last observation tonight failed less than
    FAILURE_BACKOFF_HOURS ago. target_name is the canonical name (targets.db)."""
    last_failure = await db_get_last_failure(program, target_name, now)
    if last_failure is None:
        return None
    elapsed_h = (now - last_failure).total_seconds() / 3600
    if elapsed_h < FAILURE_BACKOFF_HOURS:
        return (f"failed {elapsed_h * 60:.0f} min ago, not retried before "
                f"{FAILURE_BACKOFF_HOURS:g} h")
    return None
