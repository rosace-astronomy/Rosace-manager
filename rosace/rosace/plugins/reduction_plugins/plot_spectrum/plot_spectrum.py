"""
REDUCTION sub-plugin — Spectrum plot
Plots the final spectrum as PNG, PDF and SVG with its main information.

Placeholder for now: waits 1 s and records a neutral casier, so the whole
reduction chain runs and shows in the GUI before each step gets its real
computation.
"""

from __future__ import annotations

DISPLAY_NAME = "Plot"

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.plot_spectrum")

PLACEHOLDER_SECONDS = 1.0


class PlotSpectrumPlugin(SubPlugin):
    """Plots the final spectrum as PNG, PDF and SVG with its main information. Placeholder: no computation yet."""

    async def execute(self, obs: Observation) -> Observation:
        await asyncio.sleep(PLACEHOLDER_SECONDS)
        obs.reduction_data["plot_spectrum"] = {"status": "placeholder"}
        log.info("[PLOT_SPECTRUM] Placeholder — no computation yet")
        return obs
