"""
Sous-plugin SEQUENCER — Étape 3 : Exposition CCD
Backend "indi" — PLACEHOLDER : à implémenter avec le vrai protocole INDI
(chantier suivant). Pour l'instant, action minimale (sleep) mais structure
et contrat (clés posées dans sequencer_data) identiques à internal/expose.py.
"""

from __future__ import annotations

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.expose")


class ExposePlugin(SubPlugin):
    """Réalise les expositions spectrales — placeholder INDI, pas encore implémenté."""

    async def execute(self, obs: Observation) -> Observation:
        if not obs.sequencer_data.get("slew_done"):
            raise RuntimeError("EXPOSE : télescope non pointé (slew_done manquant)")

        if not obs.obs_dir:
            raise RuntimeError("EXPOSE : répertoire d'observation (obs_dir) non défini")

        exposure = obs.scheduler_data.get("exposure")
        if not exposure:
            raise RuntimeError("EXPOSE : exposure manquant dans scheduler_data")

        log.info("[EXPOSE] (placeholder INDI) Exposition")
        # TODO : driver CCD réel via INDI (ccd.expose(...) via un client INDI)
        await asyncio.sleep(1)

        obs.sequencer_data["raw_files"] = obs.sequencer_data.get("raw_files", [])
        obs.sequencer_data["expose_done"] = True
        obs.sequencer_data["expose_count"] = 0
        log.info("[EXPOSE] (placeholder INDI) Exposition terminée")
        return obs
