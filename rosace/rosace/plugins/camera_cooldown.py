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
  fixed target sensor temperature (dark_library_helpers.py::
  compute_camera_target_temperature()) and commands the science camera to
  it, then waits for it to actually be reached (within the camera's own
  temperature_tolerance_deg_c) before returning -- this is what lets every
  DARK/BIAS frame taken later in the session, and every dark-library
  lookup, use a single temperature for the whole night (see
  plugins/sequencer_plugins/dark_acquisition/, bias_acquisition/). Assumes
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
from typing import Any, Callable, Optional

from ..config import CAMERA_COOLDOWN_TIMEOUT_SECONDS
from .sequencer_plugins.dark_library_helpers import compute_camera_target_temperature

log = logging.getLogger("spectro.plugin.camera_cooldown")

POLL_INTERVAL_SECONDS = 5.0


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

        current = await camera.get_temperature()
        log.info(f"[CAMERA_COOLDOWN] Current temperature: {current}C (target {target}C)")
        if abs(current - target) <= tolerance_deg_c:
            log.info(f"[CAMERA_COOLDOWN] Target reached ({current}C)")
            return ambient, target

        step = min(POLL_INTERVAL_SECONDS, CAMERA_COOLDOWN_TIMEOUT_SECONDS - elapsed)
        await asyncio.sleep(step)
        elapsed += step

    raise RuntimeError(
        f"CAMERA_COOLDOWN: target {target}C not reached within {CAMERA_COOLDOWN_TIMEOUT_SECONDS}s "
        f"(tolerance {tolerance_deg_c}C)"
    )
