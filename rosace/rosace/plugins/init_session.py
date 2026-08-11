import asyncio
import logging
from datetime import datetime, timezone

from ..config import OBSERVATIONS_DIR, SEQUENCER_BACKEND, SITE_NAME
from ..models import SessionContext
from .hardware_connectivity import connect_session_hardware

log = logging.getLogger("spectro.plugin.init_session")


class InitSessionPlugin:
    """Initialise la session : répertoire de session, connexion instruments, vérifications pré-nuit."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        log.info(f"[INIT_SESSION] Démarrage session {ctx.session_id}")

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        safe_site = SITE_NAME.replace(" ", "_").replace("/", "-")
        session_dir = OBSERVATIONS_DIR / f"{date_str}_{safe_site}"
        session_dir.mkdir(parents=True, exist_ok=True)
        ctx.session_dir = str(session_dir)
        log.info(f"[INIT_SESSION] Session directory: {session_dir}")

        if SEQUENCER_BACKEND == "indi":
            log.info("[INIT_SESSION] Connecting hardware (INDI devices + PHD2)…")
            connected = await connect_session_hardware()
            ctx.hardware = connected.adapters
            ctx.hardware_config = connected.hardware_config
            log.info(f"[INIT_SESSION] Hardware connected: {list(ctx.hardware)}")
        else:
            log.info("[INIT_SESSION] Running in DEMO mode (sequencer_backend=internal) — no hardware involved")

        # TODO : vérification météo, mise en température CCD…
        await asyncio.sleep(1.0)
        log.info("[INIT_SESSION] Session initialisée")
        return ctx
