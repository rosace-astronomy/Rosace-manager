"""
Sous-plugin SEQUENCER — Exposition CCD
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
        if "slew" not in obs.sequencer_data:
            raise RuntimeError("EXPOSE : télescope non pointé (sequencer_data['slew'] manquant)")

        if not obs.obs_dir:
            raise RuntimeError("EXPOSE : répertoire d'observation (obs_dir) non défini")

        exposure = obs.scheduler_data.get("exposure")
        if not exposure:
            raise RuntimeError("EXPOSE : exposure manquant dans scheduler_data")

        log.info("[EXPOSE] (placeholder INDI) Exposition")
        # TODO : driver CCD réel via INDI (ccd.expose(...) via un client INDI)
        await asyncio.sleep(1)

        obs.sequencer_data["expose"] = {"raw_files": [], "count": 0}
        log.info("[EXPOSE] (placeholder INDI) Exposition terminée")
        return obs
