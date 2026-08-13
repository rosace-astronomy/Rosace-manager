"""
Sous-plugin SEQUENCER — Pointage (Slew)
Pointe le télescope vers les coordonnées RA/Dec de la cible.
Backend "internal" — simulation interne, sans dépendance externe.
"""

from __future__ import annotations

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.slew")


class SlewPlugin(SubPlugin):
    """Pointe le télescope vers la cible (RA/Dec) — simulation interne."""

    async def execute(self, obs: Observation) -> Observation:
        target = obs.scheduler_data.get("target")
        queried_name = obs.scheduler_data.get("queried_name") or "inconnue"  # logs: name as requested
        ra = target.ra if target else 0.0
        dec = target.dec if target else 0.0

        slew_speed = 2.0  # deg/s, simulation
        settle_time = 3.0  # s, simulation

        log.info(f"[SLEW] Pointage vers {queried_name} RA={ra:.4f}° Dec={dec:.4f}°")

        slew_duration = min(settle_time, 30.0 / slew_speed)
        await asyncio.sleep(slew_duration * 0.1)  # accéléré en simulation
        log.info(f"[SLEW] Simulation — pointage terminé en {slew_duration:.1f}s (accéléré)")

        # No sequencer_data casier: the target is already in scheduler_data, and
        # nothing downstream needs to know specifically which SEQUENCER sub-plugin
        # pointed the telescope.
        log.info(f"[SLEW] Télescope en position sur {queried_name}")
        return obs
