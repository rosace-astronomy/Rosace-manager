"""
Sous-plugin REDUCTION — Division par le flat-field
Corrige les variations de sensibilité pixel-à-pixel et le vignettage.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.flat")


class FlatPlugin(SubPlugin):
    """Division par le flat-field (correction de réponse spectrale) — simulation interne."""

    async def execute(self, obs: Observation) -> Observation:
        if "dark" not in obs.reduction_data:
            raise RuntimeError("FLAT : reduction_data['dark'] manquant — DARK doit avoir tourné avant FLAT")
        input_files: list[str] = obs.reduction_data["dark"]["corrected_files"]
        if not input_files:
            raise RuntimeError("FLAT : aucun fichier d'entrée (reduction_data['dark']['corrected_files'] vide)")

        normalize = True

        log.info(f"[FLAT] Division flat sur {len(input_files)} fichier(s) — normalize={normalize}")

        flat_corrected: list[str] = []
        for f in input_files:
            out = f.replace("_bd.fits", "_bdf.fits")
            await asyncio.sleep(0.05)
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).touch()
            log.info(f"[FLAT] Simulation : {Path(f).name} → {Path(out).name}")
            flat_corrected.append(out)

        obs.reduction_data["flat"] = {"corrected_files": flat_corrected}
        log.info(f"[FLAT] Correction terminée — {len(flat_corrected)} fichier(s)")
        return obs
