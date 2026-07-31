"""
Sous-plugin REDUCTION — Étape 4 : Extraction spectrale
Extrait le spectre 1D depuis l'image 2D calibrée et produit le fichier final.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.extract")


class ExtractPlugin(SubPlugin):
    """Extraction du spectre 1D depuis l'image spectrale 2D calibrée — simulation interne."""

    async def execute(self, obs: Observation) -> Observation:
        # Prend les fichiers les plus avancés dans la chaîne
        input_files: list[str] = (
            obs.reduction_data.get("flat_corrected_files")
            or obs.reduction_data.get("dark_corrected_files")
            or obs.reduction_data.get("bias_corrected_files")
            or obs.sequencer_data.get("raw_files", [])
        )
        if not input_files:
            raise RuntimeError("EXTRACT : aucun fichier calibré disponible")

        method = "sum"
        aperture_px = 10
        wavelength_calib = False

        log.info(
            f"[EXTRACT] Extraction spectrale — méthode={method}, "
            f"ouverture={aperture_px}px, calib_lambda={wavelength_calib}"
        )

        reduced_files: list[str] = []
        for f in input_files:
            # Produit un fichier _reduced.fits (le nom final standard)
            base = f
            for suffix in ("_f.fits", "_bd.fits", "_b.fits", "_d.fits", ".fits"):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break
            out = base + "_reduced.fits"

            await asyncio.sleep(0.1)
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).touch()
            log.info(f"[EXTRACT] Simulation : {Path(f).name} → {Path(out).name}")
            reduced_files.append(out)

        obs.reduction_data["reduced_files"] = reduced_files
        obs.reduction_data["extract_done"] = True
        log.info(f"[EXTRACT] Extraction terminée — {len(reduced_files)} spectre(s) produit(s)")
        return obs
