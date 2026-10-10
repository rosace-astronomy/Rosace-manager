"""
REDUCTION sub-plugin — Scaling
Divides the target spectrum by its mean level in a wavelength zone, which becomes 1.

Not a normalization to the continuum: a single constant, so that the values
read naturally; no scientific effect. The zone is
subplugin_params.scaling.wavelength_min / wavelength_max, in the spectrum's
wavelength unit, and must lie entirely inside the spectrum; a zone outside
it, or a mean level <= 0, is an error.

Input: response_target_spectrum. Output: "<name>_scaled.fits" in the
intermediate directory, the header unchanged, under the shared key
scaling_target_spectrum. No casier.
"""

from __future__ import annotations

DISPLAY_NAME = "Scaling"

import logging

import numpy as np
from astropy.io import fits

from ....config import SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import transform_spectrum, wavelength_axis

log = logging.getLogger("spectro.subplugin.scaling")


class ScalingPlugin(SubPlugin):
    """Divides the target spectrum by its mean level in a wavelength zone, which becomes 1."""

    async def execute(self, obs: Observation) -> Observation:
        params = SUBPLUGIN_PARAMS.get("scaling", {})
        if params.get("wavelength_min") is None or params.get("wavelength_max") is None:
            raise RuntimeError("SCALING: subplugin_params.scaling.wavelength_min / wavelength_max are not set")
        low, high = float(params["wavelength_min"]), float(params["wavelength_max"])
        level = 0.0

        def scale(data: np.ndarray, header: fits.Header):
            nonlocal level
            wavelengths = wavelength_axis(header)
            if low < wavelengths[0] or high > wavelengths[-1] or low >= high:
                raise RuntimeError(
                    f"SCALING: zone {low:g}-{high:g} not inside the spectrum "
                    f"({wavelengths[0]:.1f}-{wavelengths[-1]:.1f})"
                )
            level = float(data[(wavelengths >= low) & (wavelengths <= high)].mean())
            if level <= 0:
                raise RuntimeError(f"SCALING: mean level {level:g} in {low:g}-{high:g}, cannot scale")
            return data / level, header

        obs.reduction_data["scaling_target_spectrum"] = transform_spectrum(
            obs, "response_target_spectrum", "scaled", scale
        )
        log.info(f"[SCALING] Divided by {level:.6g}, the mean level in {low:g}-{high:g}")
        return obs
