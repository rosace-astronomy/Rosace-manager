"""
Shared acquisition helpers for real-hardware (sequencer_backend=indi)
SEQUENCER sub-plugins — plain functions, not a sub-plugin of their own: not
resolved by pipeline_loader.py's dynamic importlib convention, no programme
YAML ever declares this module as a step. Each caller still owns its own
execute() and its own key in obs.sequencer_data; this module only factors
out logic that would otherwise be duplicated across sub-plugins.

capture_guide_image() wraps a guiding-role camera's expose()-then-writeto()
sequence — the same pattern expose.py already uses for the science camera
(see hardware/camera.py for the generic Camera contract).

compute_exposure_plan() computes the (per-exposure duration, count) needed
for a target's magnitude — maps directly onto ExposureConfig.exposure_time/
num_exposures. Splits into several equal, max-duration sub-exposures rather
than one long one once the raw computed duration would exceed the
configured maximum (e.g. a raw 700s at a 300s max becomes count=3 at 300s
each, not one 700s exposure) — used by expose.py whenever a programme
doesn't set an explicit exposure_time.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any


async def capture_guide_image(
    camera: Any, exposure_time: float, obs_dir: str, filename: str = "guide_0001.fits"
) -> Path:
    """Exposes a guiding-role camera for exposure_time seconds and writes the
    resulting FITS image to obs_dir/filename. Returns the written path."""
    image = await camera.expose(exposure_time)
    path = Path(obs_dir) / filename
    image.writeto(path, overwrite=True)
    return path


def compute_exposure_plan(
    magnitude: float, exposure_time_mag0: float, max_exposure_time: float
) -> tuple[float, int]:
    """(per-exposure duration in seconds, count) for a target of the given
    magnitude: exposure_time_mag0 * 10**(magnitude / 2.5), split into several
    equal max_exposure_time-long exposures once that raw value exceeds
    max_exposure_time (e.g. a raw 700s at a 300s max -> (300.0, 3))."""
    exposure_time = exposure_time_mag0 * 10 ** (magnitude / 2.5)
    if exposure_time <= max_exposure_time:
        return exposure_time, 1
    count = math.ceil(exposure_time / max_exposure_time)
    return max_exposure_time, count
