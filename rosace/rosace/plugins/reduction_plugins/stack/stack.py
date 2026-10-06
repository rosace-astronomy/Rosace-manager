"""
REDUCTION sub-plugin — Spectrum stacking
Stacks the 1D spectra of the series into one spectrum.

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "Stack"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.stack")

PLACEHOLDER_SECONDS = 1.0


class StackPlugin(SubPlugin):
    """Stacks the 1D spectra of the series into one spectrum. Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["stack"] = {"status": "placeholder"}
        log.info("[STACK] Placeholder — no computation yet")
        return obs
