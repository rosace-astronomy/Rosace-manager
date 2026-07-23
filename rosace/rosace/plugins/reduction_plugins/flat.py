"""
Sous-plugin REDUCTION — Étape 3 : Division par le flat-field
Corrige les variations de sensibilité pixel-à-pixel et le vignettage.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ...models import Observation
from ..sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.flat")


class FlatPlugin(SubPlugin):
    """Division par le flat-field (correction de réponse spectrale)."""

    CONFIG_FILE = "config.yaml"

    async def execute(self, obs: Observation) -> Observation:
        # Chaîne de traitement : dark_corrected → bias_corrected → raw
        input_files: list[str] = (
            obs.reduction_data.get("dark_corrected_files")
            or obs.reduction_data.get("bias_corrected_files")
            or obs.sequencer_data.get("raw_files", [])
        )
        if not input_files:
            raise RuntimeError("FLAT : aucun fichier d'entrée disponible")

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

        obs.reduction_data["flat_corrected_files"] = flat_corrected
        obs.reduction_data["flat_done"] = True
        log.info(f"[FLAT] Correction terminée — {len(flat_corrected)} fichier(s)")
        return obs
