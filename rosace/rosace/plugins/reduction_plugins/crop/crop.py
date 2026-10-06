"""
REDUCTION sub-plugin — Wavelength crop
Crops the spectrum to the wavelength bounds declared in the program.

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "Crop"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.crop")

PLACEHOLDER_SECONDS = 1.0


class CropPlugin(SubPlugin):
    """Crops the spectrum to the wavelength bounds declared in the program. Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["crop"] = {"status": "placeholder"}
        log.info("[CROP] Placeholder — no computation yet")
        return obs
