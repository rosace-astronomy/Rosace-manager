"""
SEQUENCER sub-plugin — Focus
Performs autofocus before exposure.
Backend "internal" — internal simulation, no external dependency.
"""

from __future__ import annotations

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.focus")


class FocusPlugin(SubPlugin):
    """Autofocus for the spectroscope/telescope — internal simulation."""

    async def execute(self, obs: Observation) -> Observation:
        # No precondition check on a previous step's casier: if pointing had failed,
        # the orchestrator's own try/except would already have stopped the sequence
        # before ever reaching this sub-plugin -- not this sub-plugin's job to re-verify.
        method = "v_curve"
        step_size = 50  # µm
        n_steps = 7

        log.info(f"[FOCUS] Method={method}, step={step_size}µm, n={n_steps}")

        duration = n_steps * 0.05  # very fast simulation
        await asyncio.sleep(duration)
        best_position = 12450  # µm, simulated value
        fwhm = 2.3  # pixels, simulated value
        log.info(f"[FOCUS] Simulation — optimal position={best_position}µm, FWHM={fwhm}px")

        obs.sequencer_data["focus"] = {"position_um": best_position, "fwhm_px": fwhm}
        log.info(f"[FOCUS] Focusing complete")
        return obs
