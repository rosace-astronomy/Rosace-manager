"""
Camera cooling, called from INIT_SESSION/CLOSE_SESSION (sequencer_backend=indi
only). Two distinct responsibilities:

- activate_cooling_for_every_camera() / deactivate_cooling_for_every_camera():
  generic, applies to *any* connected camera-role device that declares
  cooling: true in the active hardware YAML -- not just the science camera.
  A guiding or pointing camera can independently have its own cooler; this
  turns it on for the whole session (INIT_SESSION, right after
  connect_session_hardware()) and off again at the very end (CLOSE_SESSION,
  before devices are disconnected -- the cooler command needs a live
  connection).
- cool_science_camera_to_target(): specific to the science camera only. Reads
  the observatory's ambient temperature from the focuser's own probe
  (hardware/focuser.py::Focuser.get_temperature()), computes the session's
  fixed target sensor temperature (compute_camera_target_temperature(),
  below) and commands the science camera to
  it, then waits for it to actually be reached (within the camera's own
  temperature_tolerance_deg_c) before returning -- this is what lets every
  DARK/BIAS frame taken later in the session, and every dark-library
  lookup, use a single temperature for the whole night (the sub-plugins
  that need it read the target from session_data). Assumes
  the science camera's cooler is already on (activate_cooling_for_every_camera()
  runs first) -- it only ever sets/polls the temperature setpoint itself.

Deliberately simple for now: if the target isn't reached within
CAMERA_COOLDOWN_TIMEOUT_SECONDS, cool_science_camera_to_target() raises --
INIT_SESSION lets it propagate and the session does not start, exactly like
an unreachable INDI device (see main.py's INIT_SESSION failure handling). No
automatic best-effort fallback; revisit later if this proves too strict in
practice.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Callable, Optional

from ..config import CAMERA_COOLDOWN_TIMEOUT_SECONDS

log = logging.getLogger("spectro.plugin.camera_cooldown")

POLL_INTERVAL_SECONDS = 5.0

# The session target temperature is a multiple of this step, so that darks
# can be reused from one night to the next: the dark library's temperature
# buckets (sequencer_plugins/dark_library_helpers.py::DARK_LIBRARY_TEMP_STEP_C)
# follow the same step -- a convention, keep both equal.
TARGET_TEMPERATURE_STEP_C = 5

# Safety floor for compute_camera_target_temperature() below -- see its own
# docstring for why this is needed at all (round_to_5's ceiling behavior can
# otherwise put the naive target above ambient with a weak cooling_capability_deg_c).
MIN_COOLING_MARGIN_DEG_C = 5


def round_to_5(value: float) -> int:
    """Rounds up to the next multiple of TARGET_TEMPERATURE_STEP_C (e.g.
    -9 -> -5, 3 -> 5, -5 -> -5, 0 -> 0) -- used for the session's camera
    cooldown target."""
    return TARGET_TEMPERATURE_STEP_C * math.ceil(value / TARGET_TEMPERATURE_STEP_C)


def compute_camera_target_temperature(ambient_temp_c: float, cooling_capability_deg_c: float) -> int:
    """Target sensor temperature for the whole session: 80% of the camera's
    cooling capability (a delta, taken as abs() since cooling_capability_deg_c
    is stored as a negative value in the hardware YAML) below ambient, rounded
    up to the next multiple of 5 -- the margin keeps enough cooling headroom
    for the whole night, and the rounding is what lets darks be reused across
    sessions/observations at the same bucket instead of one-off temperatures.

    Always guarantees at least MIN_COOLING_MARGIN_DEG_C below ambient, even
    after rounding -- round_to_5's ceiling behavior can otherwise push the
    naive target above, or right at, ambient itself when
    cooling_capability_deg_c is small (found on a real first-light test: a
    weak enough cooling capability produced a target hotter than ambient,
    which a cooling-only camera can never reach, timing out INIT_SESSION's
    cooldown wait). When the naive rounded-up target doesn't leave that
    margin, the next colder multiple of 5 is used instead -- still a valid
    dark-library bucket, just one step colder."""
    delta = 0.8 * abs(cooling_capability_deg_c)
    target = round_to_5(ambient_temp_c - delta)
    max_safe_target = TARGET_TEMPERATURE_STEP_C * math.floor(
        (ambient_temp_c - MIN_COOLING_MARGIN_DEG_C) / TARGET_TEMPERATURE_STEP_C
    )
    return min(target, max_safe_target)


async def activate_cooling_for_every_camera(hardware: dict[str, Any], hardware_config: dict[str, dict]) -> None:
    """Turns the cooler on for every connected device whose hardware YAML
    entry declares cooling: true -- only camera-role devices ever declare
    that field at all, so no explicit role check is needed. Called once
    from INIT_SESSION, before cool_science_camera_to_target() below. Lets
    any failure propagate (same posture as connect_session_hardware()) --
    INIT_SESSION treats it as a critical, session-cancelling failure."""
    for device_name, device_config in hardware_config.items():
        if device_config.get("cooling"):
            await hardware[device_name].set_cooling_on()
            log.info(f"[CAMERA_COOLDOWN] Cooling turned on for '{device_name}'")


async def deactivate_cooling_for_every_camera(hardware: dict[str, Any], hardware_config: dict[str, dict]) -> None:
    """Turns the cooler off for every connected device whose hardware YAML
    entry declares cooling: true -- symmetric to
    activate_cooling_for_every_camera() above. Called once from
    CLOSE_SESSION, before devices are disconnected. Each device's failure
    is caught and logged, not raised -- same "one failure doesn't block the
    others" posture as CLOSE_SESSION's own device-disconnect loop, since
    the session is ending regardless."""
    for device_name, device_config in hardware_config.items():
        if device_config.get("cooling"):
            try:
                await hardware[device_name].set_cooling_off()
                log.info(f"[CLOSE_SESSION] Cooling turned off for '{device_name}'")
            except Exception as e:
                log.error(f"[CLOSE_SESSION] Failed to turn off cooling for '{device_name}': {e}")


async def cool_science_camera_to_target(
    camera: object,
    focuser: object,
    cooling_capability_deg_c: float,
    tolerance_deg_c: float,
    stop_check: Optional[Callable[[], bool]] = None,
) -> tuple[float, float]:
    """Returns (ambient_temp_c, target_temp_c). Raises RuntimeError if the
    target isn't reached within CAMERA_COOLDOWN_TIMEOUT_SECONDS. A stop
    request (stop_check() becoming true) simply interrupts the wait and
    returns the target as-is -- not treated as a failure, the cooldown
    keeps converging in the background regardless. Logs the current
    temperature once per poll (every POLL_INTERVAL_SECONDS, 5s) so a
    long cooldown is visible in progress, not silent until it either
    reaches target or times out."""
    ambient = await focuser.get_temperature()
    target = compute_camera_target_temperature(ambient, cooling_capability_deg_c)
    log.info(f"[CAMERA_COOLDOWN] Ambient temperature {ambient}C -- cooling science camera to {target}C")
    await camera.set_temperature(target)

    elapsed = 0.0
    while elapsed < CAMERA_COOLDOWN_TIMEOUT_SECONDS:
        if stop_check and stop_check():
            log.info("[CAMERA_COOLDOWN] Stop requested -- interrupting cooldown wait")
            return ambient, target

        # Sleep *before* the first read: right after set_temperature(), the
        # INDI client's local CCD_TEMPERATURE property still holds the setpoint
        # we just wrote, not the sensor reading -- the driver's own update
        # hasn't arrived yet. Reading immediately would report "target reached"
        # on a sensor that hasn't started cooling.
        step = min(POLL_INTERVAL_SECONDS, CAMERA_COOLDOWN_TIMEOUT_SECONDS - elapsed)
        await asyncio.sleep(step)
        elapsed += step

        current = await camera.get_temperature()
        log.info(f"[CAMERA_COOLDOWN] Current temperature: {current}C (target {target}C)")
        if abs(current - target) <= tolerance_deg_c:
            log.info(f"[CAMERA_COOLDOWN] Target reached ({current}C)")
            return ambient, target

    raise RuntimeError(
        f"CAMERA_COOLDOWN: target {target}C not reached within {CAMERA_COOLDOWN_TIMEOUT_SECONDS}s "
        f"(tolerance {tolerance_deg_c}C)"
    )
