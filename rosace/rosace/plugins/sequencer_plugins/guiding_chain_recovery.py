"""
Recovery of the guiding chain (guiding camera + PHD2), for real-hardware
(sequencer_backend=indi) SEQUENCER sub-plugins — plain functions, not a
sub-plugin of their own: same status as acquisition_helpers.py, not resolved
by pipeline_loader.py, never declared as a step in a program YAML.

Found 2026-09-25: after a guiding-camera USB reset failed (ZWO driver:
"Failed to reopen camera after USB reset (ASI_ERROR_CAMERA_REMOVED)"), the
camera never came back and 30 observations in a row failed at the first PHD2
call of pointing_72ed, each one restarting from SCHEDULER, until the observer
stopped the session by hand seven hours later. Nothing in a sub-plugin could
tell a dead camera from an ordinary failure (no stars, cloud), and the
orchestrator knows nothing about plugins — so the check lives here, at the
place where the failure happens.

run_with_guiding_recovery() wraps one guiding-chain operation. If it raises a
device error, a short probe exposure tells whether the guiding camera still
answers:

- it does: the failure is not a camera fault, the original error is re-raised
  untouched (ordinary failure, the orchestrator restarts from SCHEDULER as
  before). A healthy camera is never disconnected;
- it does not: the camera is disconnected and reconnected (up to
  MAX_RECOVERY_ATTEMPTS times), the probe is repeated, and once it answers
  the operation is replayed once;
- it still does not after every attempt: SessionFatalError, which stops the
  session — retrying cannot fix a camera that no longer answers.

PHD2's equipment link is deliberately left alone: on INDI, disconnecting it
could also drop the mount's own connection (not verified), and it already
repairs itself on the next start_guiding() (see Phd2Client.start_guiding()).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

from ...errors import SessionFatalError
from ...hardware.errors import DeviceError
from ...models import Observation

log = logging.getLogger("spectro.subplugin.guiding_chain_recovery")

PROBE_EXPOSURE_SECONDS = 1.0
MAX_RECOVERY_ATTEMPTS = 2
PAUSE_BETWEEN_ATTEMPTS_SECONDS = 15.0
CALL_TIMEOUT_SECONDS = 30.0
PHD2_STOP_TIMEOUT_SECONDS = 10.0

T = TypeVar("T")


async def run_with_guiding_recovery(
    obs: Observation, operation: Callable[[], Awaitable[T]], what: str
) -> T:
    """Runs operation() and returns its result. On a DeviceError, probes the
    guiding camera and recovers it if it no longer answers — see the module
    docstring for the three possible outcomes. `what` names the operation in
    the log. Without a guiding camera in obs.hardware (internal backend,
    demo), operation() simply runs unguarded."""
    camera = obs.hardware.get("guiding_camera")
    if camera is None:
        return await operation()

    try:
        return await operation()
    except DeviceError as error:
        log.warning(f"[GUIDING_RECOVERY] {what} failed ({error}) -- checking the guiding camera")
        await _release_camera(obs)
        if await _camera_answers(camera):
            log.info("[GUIDING_RECOVERY] Guiding camera answers -- not a camera fault, re-raising the original error")
            raise
        await _recover_camera(camera)
        log.info(f"[GUIDING_RECOVERY] Guiding camera recovered -- replaying {what}")
        return await operation()


async def _release_camera(obs: Observation) -> None:
    """Best effort: asks PHD2 to stop looping/guiding so it stops holding the
    camera. A failure here is only logged — PHD2 may well be the very thing
    that is stuck on a dead camera."""
    phd2 = obs.hardware.get("phd2")
    if phd2 is None:
        return
    try:
        await asyncio.wait_for(phd2.stop_capture(timeout=PHD2_STOP_TIMEOUT_SECONDS), CALL_TIMEOUT_SECONDS)
    except Exception as e:
        log.warning(f"[GUIDING_RECOVERY] PHD2 stop_capture failed ({e}) -- continuing")


async def _camera_answers(camera) -> bool:
    """Probe: one short exposure, nothing written to disk."""
    try:
        await asyncio.wait_for(camera.expose(PROBE_EXPOSURE_SECONDS), CALL_TIMEOUT_SECONDS)
    except Exception as e:
        log.warning(f"[GUIDING_RECOVERY] Probe exposure failed: {e}")
        return False
    return True


async def _recover_camera(camera) -> None:
    """Disconnects and reconnects the camera, then probes it again, up to
    MAX_RECOVERY_ATTEMPTS times. Returns once the camera answers; raises
    SessionFatalError if it never does."""
    last_error = "no answer"
    for attempt in range(1, MAX_RECOVERY_ATTEMPTS + 1):
        log.warning(f"[GUIDING_RECOVERY] Reconnecting the guiding camera (attempt {attempt}/{MAX_RECOVERY_ATTEMPTS})")
        try:
            await asyncio.wait_for(camera.disconnect(), CALL_TIMEOUT_SECONDS)
        except Exception as e:
            log.debug(f"[GUIDING_RECOVERY] Disconnect failed ({e}) -- reconnecting anyway")
        try:
            await asyncio.wait_for(camera.connect(), CALL_TIMEOUT_SECONDS)
            await asyncio.wait_for(camera.expose(PROBE_EXPOSURE_SECONDS), CALL_TIMEOUT_SECONDS)
            return
        except Exception as e:
            last_error = str(e) or type(e).__name__
            log.warning(f"[GUIDING_RECOVERY] Attempt {attempt} failed: {last_error}")
        if attempt < MAX_RECOVERY_ATTEMPTS:
            await asyncio.sleep(PAUSE_BETWEEN_ATTEMPTS_SECONDS)
    raise SessionFatalError(
        f"guiding camera unresponsive after {MAX_RECOVERY_ATTEMPTS} recovery attempts: {last_error}"
    )
