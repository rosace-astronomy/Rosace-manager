import asyncio
import logging

from ..models import SessionContext

log = logging.getLogger("spectro.plugin.init_session")


class InitSessionPlugin:
    """Initialise la session : connexion instruments, vérifications pré-nuit."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        log.info(f"[INIT_SESSION] Démarrage session {ctx.session_id}")
        # TODO : connexion INDI/ASCOM, vérification météo, mise en température CCD…
        await asyncio.sleep(1.0)
        log.info("[INIT_SESSION] Session initialisée")
        return ctx
