"""
REDUCTION sub-plugin — Tilt correction
Measures the spectrum's tilt on the target and removes it from target and lamp frames by a vertical shift per column.

Measured once, on the sum of the preprocessed target frames (shared key
preproc_target_images), by the principal axes of inertia, on the band lit
through the slit only (reduction_helpers.find_trace()). No trace found is an
error that stops the reduction.

No rotation: every column of every preprocessed target and lamp frame
(preproc_calib_images) is shifted vertically, the whole column, about
x = NAXIS1 / 2 (the trace keeps its height there), by overlap
redistribution, which keeps each column's total (reduction_helpers.
shift_columns()). Corrected frames go to the observation's intermediate
directory as "<name>_tilt.fits", under the shared keys tilt_target_images /
tilt_calib_images; the measurement goes to the casier reduction_data["tilt"].
"""

from __future__ import annotations

DISPLAY_NAME = "Tilt"

import logging
import math
from pathlib import Path

import numpy as np
from astropy.io import fits

from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import find_shared_input, find_trace, shift_columns, transform_frames

log = logging.getLogger("spectro.subplugin.tilt")


class TiltPlugin(SubPlugin):
    """Measures the spectrum's tilt on the target and removes it from target and lamp frames by a vertical shift per column."""

    async def execute(self, obs: Observation) -> Observation:
        session_dir = Path(obs.obs_dir or "")
        target_files = find_shared_input(obs, "preproc_target_images")
        calib_files = find_shared_input(obs, "preproc_calib_images")
        flat = fits.getdata(session_dir / find_shared_input(obs, "master_flat_image")).astype(np.float64)

        total = sum(fits.getdata(session_dir / name).astype(np.float64) for name in target_files)
        if total.shape != flat.shape:
            raise RuntimeError(f"TILT: target frames are {total.shape}, the master flat is {flat.shape}")
        axes = find_trace(total, flat)
        if axes is None:
            raise RuntimeError(f"TILT: no spectrum trace found on the sum of {len(target_files)} target frames")

        slope = math.tan(math.radians(axes.angle_deg))
        center_x = total.shape[1] / 2
        shifts = -(np.arange(total.shape[1]) - center_x) * slope

        def correct(data: np.ndarray) -> np.ndarray:
            return shift_columns(data, shifts)

        obs.reduction_data["tilt_target_images"] = transform_frames(obs, target_files, "tilt", correct, [])
        obs.reduction_data["tilt_calib_images"] = transform_frames(obs, calib_files, "tilt", correct, [])
        obs.reduction_data["tilt"] = {
            "angle_deg": round(float(axes.angle_deg), 4),
            "center_x": center_x,
            "trace_y_at_center": round(float(axes.cy + (center_x - axes.cx) * slope), 2),
            "trace_sigma_px": round(float(axes.sigma_minor), 2),
        }
        log.info(
            f"[TILT] Tilt {axes.angle_deg:.4f} deg (principal axes), "
            f"{len(target_files)} target + {len(calib_files)} lamp frames corrected"
        )
        return obs
