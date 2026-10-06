"""
REDUCTION sub-plugin — Final spectrum writing
Writes the final 1D FITS spectrum with every keyword the BeSS specification requires.

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "FITS 1D"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.write_spectrum")

PLACEHOLDER_SECONDS = 1.0


class WriteSpectrumPlugin(SubPlugin):
    """Writes the final 1D FITS spectrum with every keyword the BeSS specification requires. Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["write_spectrum"] = {"status": "placeholder"}
        log.info("[WRITE_SPECTRUM] Placeholder — no computation yet")
        return obs
