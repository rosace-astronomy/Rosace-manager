"""
REDUCTION sub-plugin — Flat-field division
Corrects pixel-to-pixel sensitivity variations and vignetting.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.flat")


class FlatPlugin(SubPlugin):
    """Flat-field division (spectral response correction) — internal simulation."""

    async def execute(self, obs: Observation) -> Observation:
        if "dark" not in obs.reduction_data:
            raise RuntimeError("FLAT: reduction_data['dark'] missing — DARK must run before FLAT")
        input_files: list[str] = obs.reduction_data["dark"]["corrected_files"]
        if not input_files:
            raise RuntimeError("FLAT: no input file (reduction_data['dark']['corrected_files'] empty)")
        if not obs.obs_dir:
            raise RuntimeError("FLAT : observation directory (obs_dir) not set")

        normalize = True

        log.info(f"[FLAT] Flat division on {len(input_files)} file(s) — normalize={normalize}")

        flat_corrected: list[str] = []
        for f in input_files:
            out_name = f.replace("_bd.fits", "_bdf.fits")
            out_path = Path(obs.obs_dir) / out_name
            await asyncio.sleep(0.05)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.touch()
            log.info(f"[FLAT] Simulation: {f} → {out_name}")
            flat_corrected.append(out_name)

        obs.reduction_data["flat"] = {"corrected_files": flat_corrected}
        log.info(f"[FLAT] Correction complete — {len(flat_corrected)} file(s)")
        return obs
