"""
REDUCTION sub-plugin — Smile correction
Measures the line curvature (smile) on the lamp and removes it from lamp and target frames by a horizontal shift per row.

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "Smile"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.smile")

PLACEHOLDER_SECONDS = 1.0


class SmilePlugin(SubPlugin):
    """Measures the line curvature (smile) on the lamp and removes it from lamp and target frames by a horizontal shift per row. Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["smile"] = {"status": "placeholder"}
        log.info("[SMILE] Placeholder — no computation yet")
        return obs
