import asyncio
import logging

from ..models import ObservationContext

log = logging.getLogger("spectro.plugin.sharing")


class SharingPlugin:
    """Diffuse les fichiers réduits (envoi observateur, publication)."""

    async def run(self, obs_ctx: ObservationContext) -> ObservationContext:
        if not obs_ctx.reduced_files:
            raise RuntimeError("SHARING : aucun fichier réduit dans le contexte")

        log.info(f"[SHARING] Diffusion de {len(obs_ctx.reduced_files)} fichier(s)")
        for f in obs_ctx.reduced_files:
            await asyncio.sleep(0.3)  # TODO : envoi email / API
            log.info(f"[SHARING] Diffusé : {f}")

        return obs_ctx
