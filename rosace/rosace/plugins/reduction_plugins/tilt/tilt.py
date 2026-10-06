"""
REDUCTION sub-plugin — Tilt correction
Measures the spectrum's tilt on the target and removes it from target and lamp frames by a vertical shift per column.

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "Tilt"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.tilt")

PLACEHOLDER_SECONDS = 1.0


class TiltPlugin(SubPlugin):
    """Measures the spectrum's tilt on the target and removes it from target and lamp frames by a vertical shift per column. Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["tilt"] = {"status": "placeholder"}
        log.info("[TILT] Placeholder — no computation yet")
        return obs
