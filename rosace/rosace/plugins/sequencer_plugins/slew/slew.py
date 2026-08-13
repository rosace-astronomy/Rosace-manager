"""
Sous-plugin SEQUENCER — Pointage (Slew)
Backend "indi" — appelle réellement le hardware via obs.hardware["mount"]
(voir hardware/mount.py pour le contrat générique, hardware_indi/adapters/mount.py
pour l'implémentation INDI). Minimal à dessein : ni gestion fine des erreurs de
pointage, ni vérification post-slew — à enrichir quand ce sous-plugin deviendra
une vraie fonctionnalité, pas juste un câblage.
"""

from __future__ import annotations

import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.slew")


class SlewPlugin(SubPlugin):
    """Pointe le télescope vers la cible (RA/Dec) via le hardware INDI réel."""

    async def execute(self, obs: Observation) -> Observation:
        target = obs.scheduler_data.get("target")
        if not target:
            raise RuntimeError("SLEW : target manquant dans scheduler_data")

        queried_name = obs.scheduler_data.get("queried_name")
        log.info(f"[SLEW] Pointage vers {queried_name}")
        mount = obs.hardware["mount"]
        await mount.slew_to(target.ra, target.dec)

        obs.sequencer_data["slew"] = {"target": target.name}
        log.info(f"[SLEW] Télescope en position sur {queried_name}")
        return obs
