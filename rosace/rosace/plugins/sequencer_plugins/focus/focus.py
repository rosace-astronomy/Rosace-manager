"""
Sous-plugin SEQUENCER — Mise au point (Focus)
Backend "indi" — PLACEHOLDER : à implémenter avec le vrai protocole INDI
(chantier suivant). Pour l'instant, action minimale (sleep) mais structure
et contrat (clés posées dans sequencer_data) identiques à internal/focus.py.
"""

from __future__ import annotations

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.focus")


class FocusPlugin(SubPlugin):
    """Mise au point automatique du spectro/télescope — placeholder INDI, pas encore implémenté."""

    async def execute(self, obs: Observation) -> Observation:
        if "slew" not in obs.sequencer_data:
            raise RuntimeError("FOCUS : le télescope n'est pas encore pointé (sequencer_data['slew'] manquant)")

        log.info("[FOCUS] (placeholder INDI) Mise au point")
        # TODO : autofocus réel via INDI (focuser.autofocus(...) via un client INDI)
        await asyncio.sleep(1)

        obs.sequencer_data["focus"] = {"position_um": None, "fwhm_px": None}
        log.info("[FOCUS] (placeholder INDI) Mise au point terminée")
        return obs
