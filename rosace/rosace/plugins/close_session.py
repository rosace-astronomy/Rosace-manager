import asyncio
import logging

from ..models import SessionContext

log = logging.getLogger("spectro.plugin.close_session")


class CloseSessionPlugin:
    """Clôture la session : stationnement télescope, déconnexion instruments."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        log.info(f"[CLOSE_SESSION] Clôture session {ctx.session_id}")
        await asyncio.sleep(1.0)  # TODO : stationner télescope, fermer coupole…
        log.info("[CLOSE_SESSION] Session clôturée")
        return ctx
