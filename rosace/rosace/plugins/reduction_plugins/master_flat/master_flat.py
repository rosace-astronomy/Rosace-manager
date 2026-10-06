"""
REDUCTION sub-plugin — Master flat
Combines the flat series into a master flat (sigma-clipped mean).

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "M. flat"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.master_flat")

PLACEHOLDER_SECONDS = 1.0


class MasterFlatPlugin(SubPlugin):
    """Combines the flat series into a master flat (sigma-clipped mean). Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["master_flat"] = {"status": "placeholder"}
        log.info("[MASTER_FLAT] Placeholder — no computation yet")
        return obs
