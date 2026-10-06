"""
REDUCTION sub-plugin — Master dark
Combines the dark series into a master dark (sigma-clipped mean; the dark keeps its bias).

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "M. dark"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.master_dark")

PLACEHOLDER_SECONDS = 1.0


class MasterDarkPlugin(SubPlugin):
    """Combines the dark series into a master dark (sigma-clipped mean; the dark keeps its bias). Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["master_dark"] = {"status": "placeholder"}
        log.info("[MASTER_DARK] Placeholder — no computation yet")
        return obs
