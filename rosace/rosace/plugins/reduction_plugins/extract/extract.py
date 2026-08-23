"""
REDUCTION sub-plugin — Spectral extraction
Extracts the 1D spectrum from the calibrated 2D image and produces the final file.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.extract")


class ExtractPlugin(SubPlugin):
    """Extracts the 1D spectrum from the calibrated 2D spectral image — internal simulation."""

    async def execute(self, obs: Observation) -> Observation:
        if "flat" not in obs.reduction_data:
            raise RuntimeError("EXTRACT : reduction_data['flat'] manquant — FLAT doit avoir tourné avant EXTRACT")
        input_files: list[str] = obs.reduction_data["flat"]["corrected_files"]
        if not input_files:
            raise RuntimeError("EXTRACT : aucun fichier d'entrée (reduction_data['flat']['corrected_files'] vide)")
        if not obs.obs_dir:
            raise RuntimeError("EXTRACT : observation directory (obs_dir) not set")

        method = "sum"
        aperture_px = 10
        wavelength_calib = False

        log.info(
            f"[EXTRACT] Extraction spectrale — méthode={method}, "
            f"ouverture={aperture_px}px, calib_lambda={wavelength_calib}"
        )

        reduced_files: list[str] = []
        for f in input_files:
            out_name = f.replace("_bdf.fits", "_reduced.fits")
            out_path = Path(obs.obs_dir) / out_name
            await asyncio.sleep(0.1)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.touch()
            log.info(f"[EXTRACT] Simulation : {f} → {out_name}")
            reduced_files.append(out_name)

        obs.reduction_data["extract"] = {"reduced_files": reduced_files}
        log.info(f"[EXTRACT] Extraction terminée — {len(reduced_files)} spectre(s) produit(s)")
        return obs
