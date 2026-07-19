import asyncio
import logging

from ..models import ObservationContext

log = logging.getLogger("spectro.plugin.archive")


class ArchivePlugin:
    """Archive les fichiers réduits dans le stockage long terme."""

    async def run(self, obs_ctx: ObservationContext) -> ObservationContext:
        if not obs_ctx.reduced_files:
            raise RuntimeError("ARCHIVE : aucun fichier réduit dans le contexte")

        log.info(f"[ARCHIVE] Archivage de {len(obs_ctx.reduced_files)} fichier(s)")
        for f in obs_ctx.reduced_files:
            await asyncio.sleep(0.3)  # TODO : transfert vers archive
            log.info(f"[ARCHIVE] Archivé : {f}")

        return obs_ctx
