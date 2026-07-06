import asyncio
import logging

from ..models import SessionContext

log = logging.getLogger("spectro.plugin.sharing")


class SharingPlugin:
    """Diffuse les fichiers réduits (envoi observateur, publication)."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        if not ctx.reduced_files:
            raise RuntimeError("SHARING : aucun fichier réduit dans le contexte")

        log.info(f"[SHARING] Diffusion de {len(ctx.reduced_files)} fichier(s)")
        for f in ctx.reduced_files:
            await asyncio.sleep(0.3)  # TODO : envoi email / API
            log.info(f"[SHARING] Diffusé : {f}")

        return ctx
