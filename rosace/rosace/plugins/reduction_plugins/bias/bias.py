"""
Sous-plugin REDUCTION — Étape 1 : Soustraction du biais
Soustrait le niveau de biais du capteur CCD à chaque image brute.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.bias")


class BiasPlugin(SubPlugin):
    """Soustraction du biais (offset électronique du capteur) — simulation interne."""

    async def execute(self, obs: Observation) -> Observation:
        raw_files: list[str] = obs.sequencer_data.get("raw_files", [])
        if not raw_files:
            raise RuntimeError("BIAS : aucun fichier brut dans sequencer_data (raw_files vide)")

        bias_level_adu = 1000

        log.info(f"[BIAS] Soustraction biais sur {len(raw_files)} fichier(s) — niveau={bias_level_adu} ADU")

        bias_corrected: list[str] = []
        for raw in raw_files:
            out = raw.replace(".fits", "_b.fits")
            await asyncio.sleep(0.05)
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).touch()
            log.info(f"[BIAS] Simulation : {Path(raw).name} → {Path(out).name}")
            bias_corrected.append(out)

        obs.reduction_data["bias_corrected_files"] = bias_corrected
        obs.reduction_data["bias_done"] = True
        log.info(f"[BIAS] Correction terminée — {len(bias_corrected)} fichier(s)")
        return obs
