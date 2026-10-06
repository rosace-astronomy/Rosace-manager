"""
REDUCTION sub-plugin — Master bias
Combines the session's bias series into a master bias (sigma-clipped mean).

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "M. bias"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.master_bias")

PLACEHOLDER_SECONDS = 1.0


class MasterBiasPlugin(SubPlugin):
    """Combines the session's bias series into a master bias (sigma-clipped mean). Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["master_bias"] = {"status": "placeholder"}
        log.info("[MASTER_BIAS] Placeholder — no computation yet")
        return obs
