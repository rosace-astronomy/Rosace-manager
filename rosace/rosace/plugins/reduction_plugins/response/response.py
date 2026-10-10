"""
REDUCTION sub-plugin — Instrumental response
Divides the wavelength-calibrated target spectrum by the instrumental response.

The instrumental response is an ordinary 1D FITS with a linear wavelength
axis (CRVAL1/CDELT1, CRPIX1 = 1 when absent), named by
subplugin_params.response.file: a file in <base_data_dir>/response/, or an
absolute path. It is interpolated linearly onto the spectrum's own grid
(0 outside its range); where it is at least MIN_RESPONSE_FRACTION of its
maximum the spectrum is divided by it, elsewhere the spectrum is set to 0 --
outside the response's range, or where it is close to zero and the division
would blow up. crop removes these edges later.

Input: lambda_target_spectrum. Output: "<name>_response.fits" in the
intermediate directory, the header unchanged, under the shared key
response_target_spectrum. No casier.
"""

from __future__ import annotations

DISPLAY_NAME = "Response"

import logging
from pathlib import Path

import numpy as np
from astropy.io import fits

from ....config import BASE_DATA_DIR, SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import transform_spectrum, wavelength_axis

log = logging.getLogger("spectro.subplugin.response")

RESPONSE_DIR = "response"
MIN_RESPONSE_FRACTION = 0.01


def _response_path(name: str) -> Path:
    path = Path(name).expanduser()
    return path if path.is_absolute() else BASE_DATA_DIR / RESPONSE_DIR / path


class ResponsePlugin(SubPlugin):
    """Divides the wavelength-calibrated target spectrum by the instrumental response."""

    async def execute(self, obs: Observation) -> Observation:
        name = SUBPLUGIN_PARAMS.get("response", {}).get("file")
        if not name:
            raise RuntimeError("RESPONSE: subplugin_params.response.file is not set")
        path = _response_path(name)
        if not path.exists():
            raise RuntimeError(f"RESPONSE: instrumental response file not found: {path}")
        response, response_header = fits.getdata(path, header=True)
        response = response.astype(np.float64)
        response_wavelengths = wavelength_axis(response_header)
        zeroed = 0

        def divide(data: np.ndarray, header: fits.Header):
            nonlocal zeroed
            curve = np.interp(wavelength_axis(header), response_wavelengths, response, left=0.0, right=0.0)
            usable = curve >= MIN_RESPONSE_FRACTION * response.max()
            zeroed = int(np.count_nonzero(~usable))
            return np.divide(data, curve, out=np.zeros_like(data), where=usable), header

        obs.reduction_data["response_target_spectrum"] = transform_spectrum(
            obs, "lambda_target_spectrum", "response", divide
        )
        log.info(
            f"[RESPONSE] Divided by {path.name} ({response_wavelengths[0]:.1f}-{response_wavelengths[-1]:.1f}), "
            f"{zeroed} point(s) set to 0 (outside its range or below {MIN_RESPONSE_FRACTION:.0%} of its maximum)"
        )
        return obs
