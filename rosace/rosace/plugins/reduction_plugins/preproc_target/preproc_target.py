"""
REDUCTION sub-plugin — Target preprocessing
Corrects every science frame with the master dark and the master flat.

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "Preproc T"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.preproc_target")

PLACEHOLDER_SECONDS = 1.0


class PreprocTargetPlugin(SubPlugin):
    """Corrects every science frame with the master dark and the master flat. Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["preproc_target"] = {"status": "placeholder"}
        log.info("[PREPROC_TARGET] Placeholder — no computation yet")
        return obs
