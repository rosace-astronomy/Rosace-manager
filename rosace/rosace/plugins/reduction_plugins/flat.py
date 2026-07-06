"""
Sous-plugin REDUCTION — Étape 3 : Division par le flat-field
Corrige les variations de sensibilité pixel-à-pixel et le vignettage.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.flat")


class FlatPlugin(SubPlugin):
    """Division par le flat-field (correction de réponse spectrale)."""

    CONFIG_FILE = "config.yaml"

    async def execute(self, context: dict) -> dict:
        # Chaîne de traitement : dark_corrected → bias_corrected → raw
        input_files: list[str] = (
            context.get("dark_corrected_files")
            or context.get("bias_corrected_files")
            or context.get("raw_files", [])
        )
        if not input_files:
            raise RuntimeError("FLAT : aucun fichier d'entrée disponible dans le contexte")

        master_flat = self.config.get("master_flat_path", "")
        simulate = self.config.get("simulate", True)
        normalize = self.config.get("normalize", True)

        log.info(f"[FLAT] Division flat sur {len(input_files)} fichier(s) — normalize={normalize}")

        flat_corrected: list[str] = []
        for f in input_files:
            # Suffixe : _f pour flat
            base = f
            for suffix in ("_bd.fits", "_b.fits", "_d.fits", ".fits"):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break
            out = base + "_f.fits"

            if simulate:
                await asyncio.sleep(0.05)
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).touch()
                log.info(f"[FLAT] Simulation : {Path(f).name} → {Path(out).name}")
            else:
                # TODO : division flat réelle
                # ccd = CCDData.read(f, unit='adu')
                # if normalize:
                #     master_flat_norm = master_flat.data / np.median(master_flat.data)
                # ccd = ccdp.flat_correct(ccd, master_flat_norm)
                # ccd.write(out, overwrite=True)
                await asyncio.sleep(0.05)
            flat_corrected.append(out)

        context["flat_corrected_files"] = flat_corrected
        context["flat_done"] = True
        log.info(f"[FLAT] Correction terminée — {len(flat_corrected)} fichier(s)")
        return context
