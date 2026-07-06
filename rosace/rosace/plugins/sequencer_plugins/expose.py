"""
Sous-plugin SEQUENCER — Étape 3 : Exposition CCD
Déclenche les expositions et collecte les fichiers FITS produits.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.expose")


class ExposePlugin(SubPlugin):
    """Réalise les expositions spectrales et produit les fichiers FITS bruts."""

    CONFIG_FILE = "config.yaml"

    async def execute(self, context: dict) -> dict:
        if not context.get("slew_done"):
            raise RuntimeError("EXPOSE : télescope non pointé (slew_done manquant)")

        obs = context.get("observation")
        obs_dir = context.get("obs_dir")
        if not obs_dir:
            raise RuntimeError("EXPOSE : répertoire d'observation (obs_dir) non défini")

        # Paramètres d'exposition depuis l'observation ou la config par défaut
        if obs and hasattr(obs, "request"):
            exposure_time = obs.request.exposure.exposure_time
            num_exposures = obs.request.exposure.num_exposures
            binning = obs.request.exposure.binning
            grating = obs.request.exposure.grating
        else:
            exposure_time = context.get("exposure_time", self.config.get("default_exposure_sec", 300.0))
            num_exposures = context.get("num_exposures", self.config.get("default_num_exposures", 1))
            binning = context.get("binning", self.config.get("default_binning", 1))
            grating = context.get("grating", self.config.get("default_grating", "R1200"))

        simulate = self.config.get("simulate", True)
        fits_prefix = self.config.get("fits_prefix", "spectrum")
        simulate_speed = self.config.get("simulate_speed_factor", 0.05)

        target = context.get("target_name", "target")
        raw_files: list[str] = []

        log.info(
            f"[EXPOSE] Cible={target}, t={exposure_time}s × {num_exposures}, "
            f"binning={binning}x, réseau={grating}"
        )

        for i in range(1, num_exposures + 1):
            filename = str(Path(obs_dir) / f"{fits_prefix}_{i:04d}.fits")
            log.info(f"[EXPOSE] Exposition {i}/{num_exposures} → {filename}")

            if simulate:
                await asyncio.sleep(exposure_time * simulate_speed)
                # Créer un fichier placeholder en simulation
                Path(filename).parent.mkdir(parents=True, exist_ok=True)
                Path(filename).touch()
                log.info(f"[EXPOSE] Simulation — fichier créé : {filename}")
            else:
                # TODO : driver CCD réel (INDI/ASCOM)
                # await ccd.expose(duration=exposure_time, binning=binning)
                # fits_path = await ccd.save_image(filename)
                await asyncio.sleep(exposure_time * simulate_speed)

            raw_files.append(filename)

        # Mise à jour de l'observation et du contexte
        if obs and hasattr(obs, "files"):
            obs.files.extend(raw_files)

        context["raw_files"] = context.get("raw_files", []) + raw_files
        context["expose_done"] = True
        context["expose_count"] = num_exposures

        log.info(f"[EXPOSE] {num_exposures} exposition(s) terminée(s) — {len(raw_files)} fichier(s) produit(s)")
        return context
