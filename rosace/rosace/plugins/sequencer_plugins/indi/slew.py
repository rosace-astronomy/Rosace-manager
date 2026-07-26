"""
Sous-plugin SEQUENCER — Étape 1 : Pointage (Slew)
Backend "indi" — PLACEHOLDER : à implémenter avec le vrai protocole INDI
(chantier suivant). Pour l'instant, action minimale (sleep) mais structure
et contrat (clés posées dans sequencer_data) identiques à internal/slew.py,
pour rester compatible avec n'importe quel programme YAML.
"""

from __future__ import annotations

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.slew")


class SlewPlugin(SubPlugin):
    """Pointe le télescope vers la cible (RA/Dec) — placeholder INDI, pas encore implémenté."""

    async def execute(self, obs: Observation) -> Observation:
        target = obs.scheduler_data.get("target")
        target_name = target.name if target else "inconnue"

        log.info(f"[SLEW] (placeholder INDI) Pointage vers {target_name}")
        # TODO : commande INDI réelle (télescope.slew_to(ra, dec) via un client INDI)
        await asyncio.sleep(1)

        obs.sequencer_data["slew_done"] = True
        obs.sequencer_data["slew_target"] = target_name
        log.info(f"[SLEW] (placeholder INDI) Télescope en position sur {target_name}")
        return obs
