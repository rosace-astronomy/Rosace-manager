"""
Sous-plugin SEQUENCER — Étape 2 : Mise au point (Focus)
Effectue la mise au point automatique avant l'exposition.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.focus")


class FocusPlugin(SubPlugin):
    """Mise au point automatique du spectro/télescope."""

    CONFIG_FILE = "config.yaml"

    async def execute(self, context: dict) -> dict:
        if not context.get("slew_done"):
            raise RuntimeError("FOCUS : le télescope n'est pas encore pointé (slew_done manquant)")

        method = self.config.get("method", "v_curve")
        step_size = self.config.get("step_size_um", 50)
        n_steps = self.config.get("n_steps", 7)
        simulate = self.config.get("simulate", True)
        skip_if_recent_sec = self.config.get("skip_if_recent_sec", 0)

        # Vérification : mise au point récente ?
        last_focus_age = context.get("last_focus_age_sec")
        if skip_if_recent_sec and last_focus_age is not None:
            if last_focus_age < skip_if_recent_sec:
                log.info(f"[FOCUS] Mise au point récente ({last_focus_age}s < {skip_if_recent_sec}s) — ignorée")
                context["focus_skipped"] = True
                return context

        log.info(f"[FOCUS] Méthode={method}, pas={step_size}µm, n={n_steps}")

        if simulate:
            duration = n_steps * 0.05  # simulation très rapide
            await asyncio.sleep(duration)
            best_position = 12450  # µm, valeur simulée
            fwhm = 2.3  # pixels, valeur simulée
            log.info(f"[FOCUS] Simulation — position optimale={best_position}µm, FWHM={fwhm}px")
        else:
            # TODO : autofocus réel via INDI/ASCOM
            # best_position, fwhm = await focuser.autofocus(method=method, ...)
            best_position = None
            fwhm = None
            await asyncio.sleep(0.1)

        context["focus_done"] = True
        context["focus_position_um"] = best_position
        context["focus_fwhm_px"] = fwhm
        context["last_focus_age_sec"] = 0  # réinitialise le compteur
        log.info(f"[FOCUS] Mise au point terminée")
        return context
