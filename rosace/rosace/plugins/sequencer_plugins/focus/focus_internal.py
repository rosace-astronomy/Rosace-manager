"""
Sous-plugin SEQUENCER — Étape 2 : Mise au point (Focus)
Effectue la mise au point automatique avant l'exposition.
Backend "internal" — simulation interne, sans dépendance externe.
"""

from __future__ import annotations

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.focus")


class FocusPlugin(SubPlugin):
    """Mise au point automatique du spectro/télescope — simulation interne."""

    async def execute(self, obs: Observation) -> Observation:
        if not obs.sequencer_data.get("slew_done"):
            raise RuntimeError("FOCUS : le télescope n'est pas encore pointé (slew_done manquant)")

        method = "v_curve"
        step_size = 50  # µm
        n_steps = 7

        log.info(f"[FOCUS] Méthode={method}, pas={step_size}µm, n={n_steps}")

        duration = n_steps * 0.05  # simulation très rapide
        await asyncio.sleep(duration)
        best_position = 12450  # µm, valeur simulée
        fwhm = 2.3  # pixels, valeur simulée
        log.info(f"[FOCUS] Simulation — position optimale={best_position}µm, FWHM={fwhm}px")

        obs.sequencer_data["focus_done"] = True
        obs.sequencer_data["focus_position_um"] = best_position
        obs.sequencer_data["focus_fwhm_px"] = fwhm
        log.info(f"[FOCUS] Mise au point terminée")
        return obs
