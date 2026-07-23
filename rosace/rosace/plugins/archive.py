import asyncio
import logging

from ..models import Observation

log = logging.getLogger("spectro.plugin.archive")


class ArchivePlugin:
    """Archive les fichiers réduits dans le stockage long terme."""

    async def run(self, obs: Observation) -> Observation:
        reduced_files = obs.reduction_data.get("reduced_files", [])
        if not reduced_files:
            raise RuntimeError("ARCHIVE : aucun fichier réduit dans reduction_data")

        log.info(f"[ARCHIVE] Archivage de {len(reduced_files)} fichier(s)")
        for f in reduced_files:
            await asyncio.sleep(0.3)  # TODO : transfert vers archive
            log.info(f"[ARCHIVE] Archivé : {f}")

        return obs
