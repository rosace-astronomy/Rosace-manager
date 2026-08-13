"""
Sous-plugin REDUCTION — Soustraction du courant d'obscurité (dark)
Soustrait le courant thermique du capteur CCD, mis à l'échelle du temps d'exposition.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.dark")


class DarkPlugin(SubPlugin):
    """Soustraction du dark (courant d'obscurité du capteur) — simulation interne."""

    async def execute(self, obs: Observation) -> Observation:
        if "bias" not in obs.reduction_data:
            raise RuntimeError("DARK : reduction_data['bias'] manquant — BIAS doit avoir tourné avant DARK")
        input_files: list[str] = obs.reduction_data["bias"]["corrected_files"]
        if not input_files:
            raise RuntimeError("DARK : aucun fichier d'entrée (reduction_data['bias']['corrected_files'] vide)")
        if not obs.obs_dir:
            raise RuntimeError("DARK : observation directory (obs_dir) not set")

        scale_to_exposure = True

        log.info(f"[DARK] Soustraction dark sur {len(input_files)} fichier(s) — scale={scale_to_exposure}")

        dark_corrected: list[str] = []
        for f in input_files:
            out_name = f.replace("_b.fits", "_bd.fits")
            out_path = Path(obs.obs_dir) / out_name
            await asyncio.sleep(0.05)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.touch()
            log.info(f"[DARK] Simulation : {f} → {out_name}")
            dark_corrected.append(out_name)

        obs.reduction_data["dark"] = {"corrected_files": dark_corrected}
        log.info(f"[DARK] Correction terminée — {len(dark_corrected)} fichier(s)")
        return obs
