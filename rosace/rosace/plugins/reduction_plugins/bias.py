"""
Sous-plugin REDUCTION — Étape 1 : Soustraction du biais
Soustrait le niveau de biais du capteur CCD à chaque image brute.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ...models import Observation
from ..sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.bias")


class BiasPlugin(SubPlugin):
    """Soustraction du biais (offset électronique du capteur)."""

    CONFIG_FILE = "config.yaml"

    async def execute(self, obs: Observation) -> Observation:
        raw_files: list[str] = obs.sequencer_data.get("raw_files", [])
        if not raw_files:
            raise RuntimeError("BIAS : aucun fichier brut dans sequencer_data (raw_files vide)")

        master_bias = self.config.get("master_bias_path", "")
        simulate = self.config.get("simulate", True)
        bias_level_adu = self.config.get("bias_level_adu", 1000)

        log.info(f"[BIAS] Soustraction biais sur {len(raw_files)} fichier(s) — niveau={bias_level_adu} ADU")

        bias_corrected: list[str] = []
        for raw in raw_files:
            out = raw.replace(".fits", "_b.fits")
            if simulate:
                await asyncio.sleep(0.05)
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).touch()
                log.info(f"[BIAS] Simulation : {Path(raw).name} → {Path(out).name}")
            else:
                # TODO : soustraction réelle via astropy / ccdproc
                # ccd = CCDData.read(raw, unit='adu')
                # ccd = ccdp.subtract_bias(ccd, master_bias_frame)
                # ccd.write(out, overwrite=True)
                await asyncio.sleep(0.05)
            bias_corrected.append(out)

        obs.reduction_data["bias_corrected_files"] = bias_corrected
        obs.reduction_data["bias_done"] = True
        log.info(f"[BIAS] Correction terminée — {len(bias_corrected)} fichier(s)")
        return obs
