"""
Sous-plugin REDUCTION — Étape 4 : Extraction spectrale
Extrait le spectre 1D depuis l'image 2D calibrée et produit le fichier final.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.extract")


class ExtractPlugin(SubPlugin):
    """Extraction du spectre 1D depuis l'image spectrale 2D calibrée."""

    CONFIG_FILE = "config.yaml"

    async def execute(self, context: dict) -> dict:
        # Prend les fichiers les plus avancés dans la chaîne
        input_files: list[str] = (
            context.get("flat_corrected_files")
            or context.get("dark_corrected_files")
            or context.get("bias_corrected_files")
            or context.get("raw_files", [])
        )
        if not input_files:
            raise RuntimeError("EXTRACT : aucun fichier calibré disponible dans le contexte")

        method = self.config.get("method", "sum")
        aperture_px = self.config.get("aperture_px", 10)
        wavelength_calib = self.config.get("wavelength_calibration", False)
        output_format = self.config.get("output_format", "fits")
        simulate = self.config.get("simulate", True)

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

            if simulate:
                await asyncio.sleep(0.1)
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).touch()
                log.info(f"[EXTRACT] Simulation : {Path(f).name} → {Path(out).name}")
            else:
                # TODO : extraction réelle (ex: specreduce, PySpectrograph, ReSpefo)
                # spectrum = Spectrum1D.read(f)
                # extracted = extract_optimal(spectrum, aperture=aperture_px)
                # extracted.write(out, overwrite=True)
                await asyncio.sleep(0.1)
            reduced_files.append(out)

        context["reduced_files"] = reduced_files
        context["extract_done"] = True
        log.info(f"[EXTRACT] Extraction terminée — {len(reduced_files)} spectre(s) produit(s)")
        return context
