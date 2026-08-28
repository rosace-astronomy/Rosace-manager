"""
SEQUENCER sub-plugin — Pointing (Slew)
Backend "indi" — calls the real hardware via obs.hardware["mount"] (see
hardware/mount.py for the generic contract, hardware_indi/adapters/mount.py
for the INDI implementation). Deliberately minimal: no fine-grained
pointing error handling, no post-slew check yet — to be enriched once this
sub-plugin becomes a real feature, not just wiring.
"""

from __future__ import annotations

DISPLAY_NAME = "Slew"

import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.slew")


class SlewPlugin(SubPlugin):
    """Points the telescope at the target (RA/Dec) via the real INDI hardware."""

    async def execute(self, obs: Observation) -> Observation:
        target = obs.scheduler_data.get("target")
        if not target:
            raise RuntimeError("SLEW: target missing from scheduler_data")

        queried_name = obs.scheduler_data.get("queried_name")
        log.info(f"[SLEW] Slewing to {queried_name}")
        mount = obs.hardware["mount"]
        await mount.slew_to(target.ra, target.dec)

        # No sequencer_data casier: the target is already in scheduler_data, and
        # nothing downstream needs to know specifically which SEQUENCER sub-plugin
        # pointed the telescope.
        log.info(f"[SLEW] Telescope in position on {queried_name}")
        return obs
