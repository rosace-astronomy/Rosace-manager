import asyncio
import logging

from ..models import Observation

log = logging.getLogger("spectro.plugin.sharing")


class SharingPlugin:
    """Diffuse les fichiers réduits (envoi observateur, publication)."""

    async def run(self, obs: Observation) -> Observation:
        reduced_files = obs.reduction_data.get("extract", {}).get("reduced_files", [])
        if not reduced_files:
            raise RuntimeError("SHARING : aucun fichier réduit dans reduction_data")

        log.info(f"[SHARING] Diffusion de {len(reduced_files)} fichier(s)")
        for f in reduced_files:
            await asyncio.sleep(0.3)  # TODO : envoi email / API
            log.info(f"[SHARING] Diffusé : {f}")

        return obs
