import asyncio
import logging

from ..models import SessionContext
from ..session_log_extractor import extract_session_log
from .camera_cooldown import deactivate_cooling_for_every_camera

log = logging.getLogger("spectro.plugin.close_session")


class CloseSessionPlugin:
    """Closes the session: telescope parking, instrument disconnection."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        log.info(f"[CLOSE_SESSION] Closing session {ctx.session_id}")

        await deactivate_cooling_for_every_camera(ctx.hardware, ctx.hardware_config)

        # Generic, mount-agnostic: any mount benefits from being parked at
        # the end of the session -- unlike a mount-specific procedure declared
        # by the hardware profile's sequencer_prefix, park() alone is a
        # standard Mount Protocol primitive. Needs a live connection, hence before the disconnect
        # loop below, same reasoning as cooling deactivation above.
        if "mount" in ctx.hardware:
            try:
                await ctx.hardware["mount"].park()
                log.info("[CLOSE_SESSION] Mount parked")
            except Exception as e:
                log.error(f"[CLOSE_SESSION] Failed to park mount: {e}")

        for device_name, adapter in ctx.hardware.items():
            try:
                await adapter.disconnect()
                log.info(f"[CLOSE_SESSION] Device '{device_name}' disconnected")
            except Exception as e:
                log.error(f"[CLOSE_SESSION] Failed to disconnect '{device_name}': {e}")

        # Makes the session directory self-contained for post-mortem diagnosis,
        # without digging through the global rotating log -- same posture as the
        # other steps above: one failure here shouldn't block the session from
        # closing cleanly.
        try:
            log_path = extract_session_log(ctx)
            log.info(f"[CLOSE_SESSION] Session log extracted to {log_path.name}")
        except Exception as e:
            log.error(f"[CLOSE_SESSION] Failed to extract session log: {e}")

        await asyncio.sleep(2.0)
        log.info("[CLOSE_SESSION] Session closed")
        return ctx
