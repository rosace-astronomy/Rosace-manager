"""
SEQUENCER sub-plugin -- guiding-camera focus quality measurement, "indi"
backend only. Runs on every observation (unlike guiding_dark_acquisition/
home_sync_am3, which run once per session), right after pointing_72ed --
measures the actual guide star PHD2 is locked onto for this observation.
Captures its own guide image, measures the mean FWHM of a handful of
clean stars in a window centered on the frame, and logs the result at
INFO. Diagnostic only for now, no threshold-based rejection/alert -- a
real autofocus loop is a separate, future chantier. Every parameter here
is a hardcoded module constant on purpose for this first iteration: the
goal right now is only to confirm the measurement itself is reliable,
not to tune it.

Detection (photutils.detection.DAOStarFinder) is filtered three ways
before a candidate is trusted enough to contribute to the measurement:
- shape (sharpness/roundness) rejects hot pixels and non-round ("potato
  shaped") sources that don't look like a real stellar PSF;
- saturation (peak ADU) rejects blown-out stars, which bias a Gaussian
  fit's width upward;
- a minimum SNR rejects anything too faint to fit reliably.
At most MAX_STARS survivors (the brightest) are kept and each fitted with
a 2D Gaussian (astropy.modeling) to get its own FWHM; the reported value
is the mean across the kept sample (not the median).
"""

from __future__ import annotations

DISPLAY_NAME = "Focus"

import logging

import numpy as np
from astropy.io import fits
from astropy.modeling import fitting, models
from astropy.stats import sigma_clipped_stats
from photutils.detection import DAOStarFinder

from ....config import SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import capture_guide_image

log = logging.getLogger("spectro.subplugin.focus")

# Central window (pixels): PSF quality away from the optical axis (coma,
# field curvature) isn't representative of focus itself.
WINDOW_SIZE = 400

# Saturation floor: the guiding camera's sensor is a 12-bit ADC stored
# left-shifted by 4 bits (see the 2026-09-20 gain/offset investigation) --
# 4095 * 16.
SATURATION_ADU = 65520

MIN_SNR = 30.0
MAX_STARS = 25
SHARPNESS_RANGE = (0.4, 0.9)
ROUNDNESS_MAX_ABS = 0.35
DETECTION_SIGMA = 6.0
FWHM_KERNEL_PX = 4.0
CUTOUT_HALF = 9


def _central_window(data: np.ndarray) -> np.ndarray:
    height, width = data.shape
    half = WINDOW_SIZE // 2
    cx, cy = width // 2, height // 2
    y0, y1 = max(0, cy - half), min(height, cy + half)
    x0, x1 = max(0, cx - half), min(width, cx + half)
    return data[y0:y1, x0:x1]


def _fit_fwhm(window: np.ndarray, x: float, y: float) -> float | None:
    xi, yi = int(round(x)), int(round(y))
    y0, y1 = max(0, yi - CUTOUT_HALF), yi + CUTOUT_HALF + 1
    x0, x1 = max(0, xi - CUTOUT_HALF), xi + CUTOUT_HALF + 1
    cutout = window[y0:y1, x0:x1].astype(np.float64)
    if cutout.shape[0] < 5 or cutout.shape[1] < 5:
        return None

    yy, xx = np.mgrid[0 : cutout.shape[0], 0 : cutout.shape[1]]
    bkg = np.median(cutout)
    amplitude = cutout.max() - bkg
    if amplitude <= 0:
        return None

    g_init = models.Gaussian2D(
        amplitude=amplitude,
        x_mean=cutout.shape[1] / 2,
        y_mean=cutout.shape[0] / 2,
        x_stddev=2.0,
        y_stddev=2.0,
    ) + models.Const2D(amplitude=bkg)
    fitted = fitting.LevMarLSQFitter()(g_init, xx, yy, cutout)

    sx, sy = abs(fitted.x_stddev_0.value), abs(fitted.y_stddev_0.value)
    if sx <= 0 or sy <= 0 or sx > CUTOUT_HALF or sy > CUTOUT_HALF:
        return None
    return 2.3548 * (sx + sy) / 2


def measure_fwhm(data: np.ndarray) -> tuple[float, int, float] | None:
    """Returns (mean_fwhm_px, n_stars, min_snr) over the kept sample, or
    None if no star passed every filter. A pure function of pixel data,
    kept separate from execute() so it's testable without any hardware."""
    window = _central_window(data)
    _, median, std = sigma_clipped_stats(window, sigma=3.0)
    if std <= 0:
        return None

    finder = DAOStarFinder(
        fwhm=FWHM_KERNEL_PX,
        threshold=DETECTION_SIGMA * std,
        sharpness_range=SHARPNESS_RANGE,
        roundness_range=(-ROUNDNESS_MAX_ABS, ROUNDNESS_MAX_ABS),
    )
    sources = finder(window - median)
    if sources is None:
        return None
    sources.sort("flux", reverse=True)

    fwhms: list[float] = []
    snrs: list[float] = []
    for row in sources:
        if len(fwhms) >= MAX_STARS:
            break
        x, y = row["x_centroid"], row["y_centroid"]
        xi, yi = int(round(x)), int(round(y))
        y0, y1 = max(0, yi - 2), yi + 3
        x0, x1 = max(0, xi - 2), xi + 3
        peak = window[y0:y1, x0:x1].max()
        if peak >= SATURATION_ADU:
            continue
        snr = (float(peak) - median) / std
        if snr < MIN_SNR:
            continue
        fwhm = _fit_fwhm(window, x, y)
        if fwhm is None:
            continue
        fwhms.append(fwhm)
        snrs.append(snr)

    if not fwhms:
        return None
    return float(np.mean(fwhms)), len(fwhms), float(min(snrs))


class FocusPlugin(SubPlugin):
    """Captures a guide image and logs its measured sharpness (mean FWHM
    over a handful of clean stars in a central window) -- diagnostic
    only, no session-blocking behavior yet."""

    async def execute(self, obs: Observation) -> Observation:
        exposure_seconds = SUBPLUGIN_PARAMS.get("guiding", {})["exposure_seconds"]
        guide_image = await capture_guide_image(obs, exposure_seconds)
        data = fits.getdata(guide_image)

        result = measure_fwhm(data)
        if result is None:
            log.warning("[FOCUS] No usable star to measure focus sharpness")
            return obs

        mean_fwhm, n_stars, min_snr = result
        log.info(
            f"[FOCUS] Mean FWHM = {mean_fwhm:.2f}px over {n_stars} "
            f"star(s), weakest SNR = {min_snr:.0f}"
        )
        return obs
