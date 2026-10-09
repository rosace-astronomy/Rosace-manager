"""
REDUCTION sub-plugin — Wavelength calibration
Applies the dispersion law and resamples the stacked target and lamp spectra on a constant wavelength step.

Inputs: the dispersion law (shared key dispersion_law_file, in the session
directory) and the stacked spectra (stack_target_spectrum,
stack_calib_spectrum). The whole sensor is kept, extrapolated edges included
(crop removes them later): the first point is at CRVAL1 = law(0), and by
default the step is the mean dispersion, CDELT1 = (law(N-1) - law(0)) / (N-1),
so the spectrum keeps one point per pixel. subplugin_params.lambda_calibration
.step imposes another step; the number of points then follows, over the same
range.

Resampling by overlap redistribution (reduction_helpers.resample_linear()):
each pixel's flux is spread over the wavelength interval it covers and shared
among the new intervals it overlaps -- the total flux is kept, nothing
oscillates around sharp lines.

Each spectrum is written to the intermediate directory as
"<stacked spectrum>_lambda.fits", with CRVAL1, CDELT1, CRPIX1 = 1, CTYPE1 =
'Wavelength', CUNIT1 (the law's unit) and COMMENT lines naming the law file
and repeating its header (formula, lines, RMS, valid range), cut between
words to fit the cards. Shared keys
lambda_target_spectrum / lambda_calib_spectrum; casier
reduction_data["lambda_calibration"] = {crval1, cdelt1, points, unit}.
"""

from __future__ import annotations

DISPLAY_NAME = "Calib"

import logging
import textwrap
from pathlib import Path

import numpy as np
from astropy.io import fits
from numpy.polynomial import Polynomial

from ....config import SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import find_shared_input, intermediate_dir, read_law, resample_linear, write_float_image

log = logging.getLogger("spectro.subplugin.lambda_calibration")

FITS_UNITS = {"A": "Angstrom", "nm": "nm"}
COMMENT_WIDTH = 72               # characters of text in one COMMENT card


def _calibrate(obs: Observation, session_dir: Path, name: str, law: Polynomial, grid: tuple[float, float, int],
               unit: str, comments: list[str]) -> str:
    start, step, count = grid
    data, header = fits.getdata(session_dir / name, header=True)
    resampled = resample_linear(data.astype(np.float64), law, start, step, count)
    header = header.copy()
    header["CRVAL1"] = (start, "Wavelength of the first point")
    header["CDELT1"] = (step, "Wavelength step")
    header["CRPIX1"] = (1, "Reference point")
    header["CTYPE1"] = ("Wavelength", "Axis type")
    header["CUNIT1"] = (FITS_UNITS[unit], "Wavelength unit")
    out_path = intermediate_dir(obs) / f"{Path(name).stem}_lambda.fits"
    write_float_image(out_path, resampled, header, comments)
    return str(out_path.relative_to(session_dir))


class LambdaCalibrationPlugin(SubPlugin):
    """Applies the dispersion law and resamples the stacked target and lamp spectra on a constant wavelength step."""

    async def execute(self, obs: Observation) -> Observation:
        session_dir = Path(obs.obs_dir or "")
        law_name = find_shared_input(obs, "dispersion_law_file")
        target_name = find_shared_input(obs, "stack_target_spectrum")
        calib_name = find_shared_input(obs, "stack_calib_spectrum")
        law_file = read_law(session_dir / law_name)
        unit = law_file["unit"]
        if unit not in FITS_UNITS:
            raise RuntimeError(f"LAMBDA_CALIBRATION: unit {unit!r} of {law_name} unknown, expected A or nm")
        law = Polynomial(law_file["coefficients"])

        pixels = fits.getheader(session_dir / target_name)["NAXIS1"]
        first, last = float(law(0)), float(law(pixels - 1))
        step = SUBPLUGIN_PARAMS.get("lambda_calibration", {}).get("step")
        if step:
            step = float(step)
            count = int(np.floor((last - first) / step)) + 1
        else:
            step, count = (last - first) / (pixels - 1), pixels
        comments = [f"Dispersion law: {law_name}", *law_file["comments"],
                    "Resampled to a constant step by overlap redistribution (total flux kept)"]
        # Cut between words, so that no word is split across two cards
        comments = [part for line in comments for part in textwrap.wrap(line, COMMENT_WIDTH)]

        grid = (first, step, count)
        target = _calibrate(obs, session_dir, target_name, law, grid, unit, comments)
        calib = _calibrate(obs, session_dir, calib_name, law, grid, unit, comments)
        obs.reduction_data["lambda_target_spectrum"] = target
        obs.reduction_data["lambda_calib_spectrum"] = calib
        obs.reduction_data["lambda_calibration"] = {
            "crval1": round(first, 4),
            "cdelt1": round(step, 6),
            "points": count,
            "unit": unit,
        }
        log.info(
            f"[LAMBDA_CALIBRATION] Target and lamp resampled: {first:.1f}-{first + (count - 1) * step:.1f} {unit}, "
            f"step {step:.4f} {unit}, {count} points"
        )
        return obs
