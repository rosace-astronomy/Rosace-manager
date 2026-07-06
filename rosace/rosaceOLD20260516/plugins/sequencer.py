import asyncio
import logging
from pathlib import Path

from ..models import SessionContext

log = logging.getLogger("spectro.plugin.sequencer")


class SequencerPlugin:
    """Exécute l'acquisition : une seule exposition."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        obs = ctx.current_observation
        if not obs:
            raise RuntimeError("SEQUENCER : aucune observation dans le contexte")
        if not ctx.obs_dir:
            raise RuntimeError("SEQUENCER : répertoire d'observation non défini dans le contexte")

        exposure = obs.request.exposure
        log.info(f"[SEQUENCER] Acquisition de {obs.request.target.name} ({exposure.exposure_time}s)")

        # TODO : driver d'acquisition réel
        await asyncio.sleep(exposure.exposure_time * 0.1)  # Simulation accélérée

        filename = str(Path(ctx.obs_dir) / "spectrum_0001.fits")
        obs.files.append(filename)
        ctx.raw_files.append(filename)

        log.info(f"[SEQUENCER] Fichier produit : {filename}")
        return ctx
