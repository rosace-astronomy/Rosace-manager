import asyncio
import logging

from ..models import SessionContext
from .camera_cooldown import deactivate_cooling_for_every_camera

log = logging.getLogger("spectro.plugin.close_session")


class CloseSessionPlugin:
    """Closes the session: telescope parking, instrument disconnection."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        log.info(f"[CLOSE_SESSION] Closing session {ctx.session_id}")

        await deactivate_cooling_for_every_camera(ctx.hardware, ctx.hardware_config)

        # Generic, mount-agnostic: any mount benefits from being parked at
        # the end of the session -- unlike home_sync_am3 (AM3-specific
        # recovery procedure), park() alone is a standard Mount Protocol
        # primitive. Needs a live connection, hence before the disconnect
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

        await asyncio.sleep(7.0)
        log.info("[CLOSE_SESSION] Session closed")
        return ctx
