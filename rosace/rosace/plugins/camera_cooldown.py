"""
Science camera cooldown -- called once per session from INIT_SESSION
(sequencer_backend=indi only), right after connect_session_hardware(). Reads
the observatory's ambient temperature from the focuser's own probe
(hardware/focuser.py::Focuser.get_temperature()), computes the session's
fixed target sensor temperature (dark_library_helpers.py::
compute_camera_target_temperature()) and commands the science camera to it,
then waits for it to actually be reached (within the camera's own
temperature_tolerance_deg_c) before returning -- this is what lets every
DARK frame taken later in the session, and every dark-library lookup, use a
single temperature for the whole night (see
plugins/sequencer_plugins/dark_acquisition/).

Deliberately simple for now: if the target isn't reached within
CAMERA_COOLDOWN_TIMEOUT_SECONDS, this raises -- INIT_SESSION lets it
propagate and the session does not start, exactly like an unreachable INDI
device (see main.py's INIT_SESSION failure handling). No automatic
best-effort fallback; revisit later if this proves too strict in practice.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from ..config import CAMERA_COOLDOWN_TIMEOUT_SECONDS
from .sequencer_plugins.dark_library_helpers import compute_camera_target_temperature

log = logging.getLogger("spectro.plugin.camera_cooldown")

POLL_INTERVAL_SECONDS = 5.0


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
    keeps converging in the background regardless."""
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
