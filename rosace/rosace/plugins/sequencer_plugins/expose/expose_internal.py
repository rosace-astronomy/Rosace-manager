"""
Sous-plugin SEQUENCER — Exposition CCD
Déclenche les expositions et collecte les fichiers FITS produits.
Backend "internal" — simulation interne, sans dépendance externe.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.expose")


class ExposePlugin(SubPlugin):
    """Réalise les expositions spectrales et produit les fichiers FITS bruts — simulation interne."""

    async def execute(self, obs: Observation) -> Observation:
        if "slew" not in obs.sequencer_data:
            raise RuntimeError("EXPOSE : télescope non pointé (sequencer_data['slew'] manquant)")

        if not obs.obs_dir:
            raise RuntimeError("EXPOSE : répertoire d'observation (obs_dir) non défini")

        target_obj = obs.scheduler_data.get("target")
        exposure = obs.scheduler_data.get("exposure")
        if not exposure:
            raise RuntimeError("EXPOSE : exposure manquant dans scheduler_data")

        exposure_time = exposure.exposure_time
        num_exposures = exposure.num_exposures
        binning = exposure.binning
        grating = exposure.grating

        fits_prefix = "spectrum"
        simulate_speed = 0.05

        target = target_obj.name if target_obj else "target"
        raw_files: list[str] = []

        log.info(
            f"[EXPOSE] Cible={target}, t={exposure_time}s × {num_exposures}, "
            f"binning={binning}x, réseau={grating}"
        )

        for i in range(1, num_exposures + 1):
            filename = str(Path(obs.obs_dir) / f"{fits_prefix}_{i:04d}.fits")
            log.info(f"[EXPOSE] Exposition {i}/{num_exposures} → {filename}")

            await asyncio.sleep(exposure_time * simulate_speed)
            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            Path(filename).touch()
            log.info(f"[EXPOSE] Simulation — fichier créé : {filename}")

            raw_files.append(filename)

        obs.sequencer_data["expose"] = {"raw_files": raw_files, "count": num_exposures}

        log.info(f"[EXPOSE] {num_exposures} exposition(s) terminée(s) — {len(raw_files)} fichier(s) produit(s)")
        return obs
