"""
REDUCTION sub-plugin — Instrumental response correction
Divides the spectrum by the instrumental response declared in the program.

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "Response"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.response")

PLACEHOLDER_SECONDS = 1.0


class ResponsePlugin(SubPlugin):
    """Divides the spectrum by the instrumental response declared in the program. Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["response"] = {"status": "placeholder"}
        log.info("[RESPONSE] Placeholder — no computation yet")
        return obs
