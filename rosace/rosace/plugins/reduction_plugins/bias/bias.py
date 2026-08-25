"""
REDUCTION sub-plugin — Bias subtraction
Subtracts the CCD sensor's bias level from each raw image.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.bias")


class BiasPlugin(SubPlugin):
    """Bias (sensor electronic offset) subtraction — internal simulation."""

    async def execute(self, obs: Observation) -> Observation:
        if "star_spectra_images" not in obs.sequencer_data:
            raise RuntimeError(
                "BIAS: sequencer_data['star_spectra_images'] missing — "
                "no acquisition sub-plugin ran before BIAS"
            )
        raw_files: list[str] = obs.sequencer_data["star_spectra_images"]
        if not raw_files:
            raise RuntimeError("BIAS: no raw file (sequencer_data['star_spectra_images'] empty)")
        if not obs.obs_dir:
            raise RuntimeError("BIAS: observation directory (obs_dir) not set")

        bias_level_adu = 1000

        log.info(f"[BIAS] Bias subtraction on {len(raw_files)} file(s) — level={bias_level_adu} ADU")

        bias_corrected: list[str] = []
        for raw in raw_files:
            out_name = raw.replace(".fits", "_b.fits")
            out_path = Path(obs.obs_dir) / out_name
            await asyncio.sleep(0.05)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.touch()
            log.info(f"[BIAS] Simulation: {raw} → {out_name}")
            bias_corrected.append(out_name)

        obs.reduction_data["bias"] = {"corrected_files": bias_corrected}
        log.info(f"[BIAS] Correction complete — {len(bias_corrected)} file(s)")
        return obs
