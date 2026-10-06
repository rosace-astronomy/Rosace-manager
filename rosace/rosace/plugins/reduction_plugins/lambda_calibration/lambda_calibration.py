"""
REDUCTION sub-plugin — Wavelength calibration
Applies the dispersion law and resamples the spectrum on a constant wavelength step.

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "Calib"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.lambda_calibration")

PLACEHOLDER_SECONDS = 1.0


class LambdaCalibrationPlugin(SubPlugin):
    """Applies the dispersion law and resamples the spectrum on a constant wavelength step. Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["lambda_calibration"] = {"status": "placeholder"}
        log.info("[LAMBDA_CALIBRATION] Placeholder — no computation yet")
        return obs
