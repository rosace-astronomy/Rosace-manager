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
        if not obs.obs_dir:
            raise RuntimeError("FLAT : observation directory (obs_dir) not set")

        normalize = True

        log.info(f"[FLAT] Division flat sur {len(input_files)} fichier(s) — normalize={normalize}")

        flat_corrected: list[str] = []
        for f in input_files:
            out_name = f.replace("_bd.fits", "_bdf.fits")
            out_path = Path(obs.obs_dir) / out_name
            await asyncio.sleep(0.05)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.touch()
            log.info(f"[FLAT] Simulation : {f} → {out_name}")
            flat_corrected.append(out_name)

        obs.reduction_data["flat"] = {"corrected_files": flat_corrected}
        log.info(f"[FLAT] Correction terminée — {len(flat_corrected)} fichier(s)")
        return obs
