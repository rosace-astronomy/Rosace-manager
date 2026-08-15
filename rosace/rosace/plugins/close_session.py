import asyncio
import logging

from ..models import SessionContext
from .camera_cooldown import deactivate_cooling_for_every_camera

log = logging.getLogger("spectro.plugin.close_session")


class CloseSessionPlugin:
    """Clôture la session : stationnement télescope, déconnexion instruments."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        log.info(f"[CLOSE_SESSION] Clôture session {ctx.session_id}")

        await deactivate_cooling_for_every_camera(ctx.hardware, ctx.hardware_config)

        for device_name, adapter in ctx.hardware.items():
            try:
                await adapter.disconnect()
                log.info(f"[CLOSE_SESSION] Device '{device_name}' disconnected")
            except Exception as e:
                log.error(f"[CLOSE_SESSION] Failed to disconnect '{device_name}': {e}")

        await asyncio.sleep(7.0)  # TODO : stationner télescope, fermer coupole…
        log.info("[CLOSE_SESSION] Session clôturée")
        return ctx
