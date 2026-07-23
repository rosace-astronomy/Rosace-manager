"""
Sous-plugin REDUCTION — Étape 2 : Soustraction du courant d'obscurité (dark)
Soustrait le courant thermique du capteur CCD, mis à l'échelle du temps d'exposition.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ...models import Observation
from ..sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.dark")


class DarkPlugin(SubPlugin):
    """Soustraction du dark (courant d'obscurité du capteur)."""

    CONFIG_FILE = "config.yaml"

    async def execute(self, obs: Observation) -> Observation:
        # Travaille sur les fichiers corrigés du biais, sinon les bruts
        input_files: list[str] = obs.reduction_data.get(
            "bias_corrected_files", obs.sequencer_data.get("raw_files", [])
        )
        if not input_files:
            raise RuntimeError("DARK : aucun fichier d'entrée (bias_corrected_files ou raw_files vide)")

        master_dark = self.config.get("master_dark_path", "")
        simulate = self.config.get("simulate", True)
        scale_to_exposure = self.config.get("scale_to_exposure", True)
        skip_if_temp_below = self.config.get("skip_if_ccd_temp_below_celsius", None)

        # Vérification température CCD
        ccd_temp = obs.reduction_data.get("ccd_temp_celsius")
        if skip_if_temp_below is not None and ccd_temp is not None:
            if ccd_temp < skip_if_temp_below:
                log.info(f"[DARK] Température CCD {ccd_temp}°C < seuil {skip_if_temp_below}°C — dark ignoré")
                obs.reduction_data["dark_skipped"] = True
                obs.reduction_data["dark_corrected_files"] = input_files
                return obs

        log.info(f"[DARK] Soustraction dark sur {len(input_files)} fichier(s) — scale={scale_to_exposure}")

        dark_corrected: list[str] = []
        for f in input_files:
            out = f.replace("_b.fits", "_bd.fits").replace(".fits", "_d.fits") if "_b.fits" not in f else f.replace("_b.fits", "_bd.fits")
            if simulate:
                await asyncio.sleep(0.05)
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).touch()
                log.info(f"[DARK] Simulation : {Path(f).name} → {Path(out).name}")
            else:
                # TODO : soustraction dark réelle
                # ccd = CCDData.read(f, unit='adu')
                # ccd = ccdp.subtract_dark(ccd, master_dark, exposure_time='exptime', scale=scale_to_exposure)
                # ccd.write(out, overwrite=True)
                await asyncio.sleep(0.05)
            dark_corrected.append(out)

        obs.reduction_data["dark_corrected_files"] = dark_corrected
        obs.reduction_data["dark_done"] = True
        log.info(f"[DARK] Correction terminée — {len(dark_corrected)} fichier(s)")
        return obs
