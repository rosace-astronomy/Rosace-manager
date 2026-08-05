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

TODO (separate chantier, not built yet): a science-camera exposure-time
helper computing exposure duration from a target's magnitude
(exposure_time_mag0 * 10**(magnitude / 2.5)), splitting into several
sub-exposures once the computed duration exceeds a configured maximum —
belongs here once it's built, next to capture_guide_image().
"""

from __future__ import annotations

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
