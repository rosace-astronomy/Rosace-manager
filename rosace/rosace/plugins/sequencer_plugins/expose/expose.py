"""
Sous-plugin SEQUENCER — Exposition CCD
Backend "indi" — appelle réellement le hardware via obs.hardware["science_camera"]
(voir hardware/camera.py pour le contrat générique, hardware_indi/adapters/camera.py
pour l'implémentation INDI). Minimal à dessein : une seule exposition, pas
d'empilement/num_exposures, pas de calcul du temps de pose depuis la magnitude —
à enrichir quand ce sous-plugin deviendra une vraie fonctionnalité.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.expose")


class ExposePlugin(SubPlugin):
    """Réalise une exposition spectrale via le hardware INDI réel."""

    async def execute(self, obs: Observation) -> Observation:
        if "slew" not in obs.sequencer_data:
            raise RuntimeError("EXPOSE : télescope non pointé (sequencer_data['slew'] manquant)")

        if not obs.obs_dir:
            raise RuntimeError("EXPOSE : répertoire d'observation (obs_dir) non défini")

        exposure = obs.scheduler_data.get("exposure")
        if not exposure:
            raise RuntimeError("EXPOSE : exposure manquant dans scheduler_data")
        if exposure.exposure_time is None:
            raise RuntimeError("EXPOSE : exposure_time non défini (calcul depuis la magnitude pas encore implémenté)")

        log.info(f"[EXPOSE] Exposition de {exposure.exposure_time}s")
        camera = obs.hardware["science_camera"]
        image = await camera.expose(exposure.exposure_time)

        raw_path = Path(obs.obs_dir) / "spectrum_0001.fits"
        image.writeto(raw_path, overwrite=True)

        obs.sequencer_data["expose"] = {"raw_files": [str(raw_path)], "count": 1}
        log.info(f"[EXPOSE] Exposition terminée — {raw_path}")
        return obs
