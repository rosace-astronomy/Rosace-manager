"""
REDUCTION sub-plugin — Calibration-lamp preprocessing
Corrects the calibration-lamp frames with the masters.

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "Preproc C"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.preproc_calib")

PLACEHOLDER_SECONDS = 1.0


class PreprocCalibPlugin(SubPlugin):
    """Corrects the calibration-lamp frames with the masters. Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["preproc_calib"] = {"status": "placeholder"}
        log.info("[PREPROC_CALIB] Placeholder — no computation yet")
        return obs
