"""
REDUCTION sub-plugin — Dispersion law
Computes the pixel-to-wavelength law from the declared lamp series, or reuses the one already computed for that series.

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "Disp. law"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.dispersion_law")

PLACEHOLDER_SECONDS = 1.0


class DispersionLawPlugin(SubPlugin):
    """Computes the pixel-to-wavelength law from the declared lamp series, or reuses the one already computed for that series. Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["dispersion_law"] = {"status": "placeholder"}
        log.info("[DISPERSION_LAW] Placeholder — no computation yet")
        return obs
