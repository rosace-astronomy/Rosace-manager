"""
SEQUENCER sub-plugin — Pointing (Slew)
Points the telescope at the target's RA/Dec coordinates.
Backend "internal" — internal simulation, no external dependency.
"""

from __future__ import annotations

DISPLAY_NAME = "Slew"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.slew")


class SlewPlugin(SubPlugin):
    """Points the telescope at the target (RA/Dec) — internal simulation."""

    async def execute(self, obs: Observation) -> Observation:
        target = obs.scheduler_data.get("target")
        queried_name = obs.scheduler_data.get("queried_name") or "inconnue"  # logs: name as requested
        ra = target.ra if target else 0.0
        dec = target.dec if target else 0.0

        slew_speed = 2.0  # deg/s, simulation
        settle_time = 3.0  # s, simulation

        log.info(f"[SLEW] Slewing to {queried_name} RA={ra:.4f}° Dec={dec:.4f}°")

        slew_duration = min(settle_time, 30.0 / slew_speed)
        await asyncio.sleep(slew_duration * 0.1)  # sped up for simulation
        log.info(f"[SLEW] Simulation — slew complete in {slew_duration:.1f}s (accelerated)")

        # No sequencer_data casier: the target is already in scheduler_data, and
        # nothing downstream needs to know specifically which SEQUENCER sub-plugin
        # pointed the telescope.
        log.info(f"[SLEW] Telescope in position on {queried_name}")
        return obs
