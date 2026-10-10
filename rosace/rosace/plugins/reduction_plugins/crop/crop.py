"""
REDUCTION sub-plugin — Crop
Keeps the part of the target spectrum between two wavelengths, outside of which it is too faint or noisy.

The bounds are subplugin_params.crop.wavelength_min / wavelength_max, in the
spectrum's wavelength unit. No resampling: the points between them are kept
as they are, CRVAL1 becomes the first kept point's wavelength, CDELT1 does
not change. A bound beyond the spectrum gives a warning and what exists is
kept; no point kept is an error.

Input: scaling_target_spectrum. Output: "<name>_crop.fits" in the
intermediate directory, under the shared key crop_target_spectrum. No
casier.
"""

from __future__ import annotations

DISPLAY_NAME = "Crop"

import logging

import numpy as np
from astropy.io import fits

from ....config import SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import transform_spectrum, wavelength_axis

log = logging.getLogger("spectro.subplugin.crop")


class CropPlugin(SubPlugin):
    """Keeps the part of the target spectrum between two wavelengths, outside of which it is too faint or noisy."""

    async def execute(self, obs: Observation) -> Observation:
        params = SUBPLUGIN_PARAMS.get("crop", {})
        if params.get("wavelength_min") is None or params.get("wavelength_max") is None:
            raise RuntimeError("CROP: subplugin_params.crop.wavelength_min / wavelength_max are not set")
        low, high = float(params["wavelength_min"]), float(params["wavelength_max"])
        kept = {}

        def crop(data: np.ndarray, header: fits.Header):
            wavelengths = wavelength_axis(header)
            if low < wavelengths[0] or high > wavelengths[-1]:
                log.warning(
                    f"[CROP] Bounds {low:g}-{high:g} go beyond the spectrum "
                    f"({wavelengths[0]:.1f}-{wavelengths[-1]:.1f}), what exists is kept"
                )
            inside = np.nonzero((wavelengths >= low) & (wavelengths <= high))[0]
            if inside.size == 0:
                raise RuntimeError(
                    f"CROP: no point between {low:g} and {high:g} "
                    f"(spectrum {wavelengths[0]:.1f}-{wavelengths[-1]:.1f})"
                )
            first, last = int(inside[0]), int(inside[-1])
            header["CRVAL1"] = float(wavelengths[first])
            header["CRPIX1"] = 1
            kept.update(first=float(wavelengths[first]), last=float(wavelengths[last]), points=last - first + 1)
            return data[first:last + 1], header

        obs.reduction_data["crop_target_spectrum"] = transform_spectrum(obs, "scaling_target_spectrum", "crop", crop)
        log.info(f"[CROP] Kept {kept['first']:.1f}-{kept['last']:.1f}, {kept['points']} points")
        return obs
