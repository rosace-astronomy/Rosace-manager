"""
SEQUENCER sub-plugin -- guiding-camera focus quality measurement, "indi"
backend only. Runs on every observation (unlike guiding_dark_acquisition/
home_sync_am3, which run once per session), right after pointing_72ed --
measures the actual guide star PHD2 is locked onto for this observation.
Captures its own guide image, measures the mean FWHM of a handful of
clean stars in a window centered on the frame, and logs the result at
INFO. Diagnostic only for now, no threshold-based rejection/alert -- a
real autofocus loop is a separate, future chantier.

Detection (photutils.detection.DAOStarFinder) is filtered three ways
before a candidate is trusted enough to contribute to the measurement:
- shape (sharpness/roundness) rejects hot pixels and non-round ("potato
  shaped") sources that don't look like a real stellar PSF. This is a
  check on shape, not position -- it can't substitute for the central
  window below: a star blurred by off-axis field curvature is still
  round, just soft, which is exactly what this sub-plugin needs to
  measure, not reject;
- saturation (peak ADU) rejects blown-out stars, which bias a Gaussian
  fit's width upward;
- a minimum SNR (a raw peak-pixel statistic on the uncombined image,
  deliberately kept distinct from -- and looser than --
  DAOStarFinder's own convolved-domain detection threshold, so the two
  don't duplicate the same rejection at two different, uncoordinated
  strictness levels) rejects anything too faint to fit reliably.
At most MAX_STARS survivors (the brightest) are kept and each fitted with
a 2D Gaussian (astropy.modeling) to get its own FWHM; the reported value
is the mean across the kept sample (not the median).

Retuned 2026-09-22 after a full night (2026-09-20/21) came back with zero
usable measurements out of eleven observations: the shape/SNR filters,
tuned against clean simulator data, turned out to reject exactly the
broadened, less-peaked PSFs a real defocus produces -- the one thing this
sub-plugin exists to measure (see HISTORY.md). window_fraction is now the
one parameter read from subplugin_params -- a per-instrument fact, like
backlash_offset_deg, since how far off-axis the guiding optics stay clean
before coma/field curvature degrades the PSF varies with the actual
optics. Everything else (SATURATION_ADU, MIN_SNR, MAX_STARS,
SHARPNESS_RANGE, ROUNDNESS_MAX_ABS, DETECTION_SIGMA, FWHM_KERNEL_PX,
CUTOUT_HALF) stays a hardcoded module constant -- still diagnostic-stage
tuning knobs rather than confirmed values. SATURATION_ADU and
FWHM_KERNEL_PX are the most likely future candidates for promotion to a
per-instrument parameter too (both describe the physical sensor/optics,
same reasoning as window_fraction) -- not done yet since neither has been
shown to actually need to vary.
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

# Central window: PSF quality away from the optical axis (coma, field
# curvature) isn't representative of focus itself. Side length of the
# square window, as a fraction of the guiding camera's SMALLER frame
# dimension (not just the height) -- so the window still fits a
# portrait-oriented sensor, should one ever be used. Read from
# subplugin_params.focus.window_fraction (per-instrument); this is only
# the fallback for a config that omits it.
DEFAULT_WINDOW_FRACTION = 0.9

# Saturation floor: the guiding camera's sensor is a 12-bit ADC stored
# left-shifted by 4 bits (see the 2026-09-20 gain/offset investigation) --
# 4095 * 16.
SATURATION_ADU = 65520

MIN_SNR = 7.0
MAX_STARS = 25
SHARPNESS_RANGE = (0.2, 1.0)
ROUNDNESS_MAX_ABS = 0.35
DETECTION_SIGMA = 5.0
FWHM_KERNEL_PX = 4.0
CUTOUT_HALF = 9


def _central_window(data: np.ndarray, fraction: float) -> np.ndarray:
    height, width = data.shape
    size = int(min(height, width) * fraction)
    half = size // 2
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


def measure_fwhm(
    data: np.ndarray, window_fraction: float = DEFAULT_WINDOW_FRACTION
) -> tuple[float, int, float] | None:
    """Returns (mean_fwhm_px, n_stars, min_snr) over the kept sample, or
    None if no star passed every filter. A pure function of pixel data,
    kept separate from execute() so it's testable without any hardware."""
    window = _central_window(data, window_fraction)
    _, median, std = sigma_clipped_stats(window, sigma=3.0)
    if std <= 0:
        return None

    # Extra DEBUG-only detection pass, without the shape filters, purely to
    # report how many raw candidates existed before sharpness/roundness
    # rejected any of them. Gated behind isEnabledFor() since -- unlike
    # every other debug line below -- this one is a real second
    # DAOStarFinder run, not just string formatting of numbers already
    # computed.
    if log.isEnabledFor(logging.DEBUG):
        raw_finder = DAOStarFinder(fwhm=FWHM_KERNEL_PX, threshold=DETECTION_SIGMA * std)
        raw_sources = raw_finder(window - median)
        log.debug(
            f"[FOCUS] {0 if raw_sources is None else len(raw_sources)} candidate(s) "
            f"above {DETECTION_SIGMA}-sigma threshold, before shape filtering"
        )

    finder = DAOStarFinder(
        fwhm=FWHM_KERNEL_PX,
        threshold=DETECTION_SIGMA * std,
        sharpness_range=SHARPNESS_RANGE,
        roundness_range=(-ROUNDNESS_MAX_ABS, ROUNDNESS_MAX_ABS),
    )
    sources = finder(window - median)
    if sources is None:
        log.debug("[FOCUS] 0 candidate(s) left after shape filtering (sharpness/roundness)")
        return None
    sources.sort("flux", reverse=True)
    log.debug(f"[FOCUS] {len(sources)} candidate(s) left after shape filtering (sharpness/roundness)")

    fwhms: list[float] = []
    snrs: list[float] = []
    n_saturated = 0
    n_low_snr = 0
    n_fit_failed = 0
    rows_examined = 0
    for row in sources:
        if len(fwhms) >= MAX_STARS:
            break
        rows_examined += 1
        x, y = row["x_centroid"], row["y_centroid"]
        xi, yi = int(round(x)), int(round(y))
        y0, y1 = max(0, yi - 2), yi + 3
        x0, x1 = max(0, xi - 2), xi + 3
        peak = window[y0:y1, x0:x1].max()
        if peak >= SATURATION_ADU:
            n_saturated += 1
            continue
        snr = (float(peak) - median) / std
        if snr < MIN_SNR:
            n_low_snr += 1
            continue
        fwhm = _fit_fwhm(window, x, y)
        if fwhm is None:
            n_fit_failed += 1
            continue
        fwhms.append(fwhm)
        snrs.append(snr)

    n_not_examined = len(sources) - rows_examined
    log.debug(
        f"[FOCUS] Per-star checks on {rows_examined} candidate(s): {len(fwhms)} kept, "
        f"{n_saturated} saturated, {n_low_snr} below SNR {MIN_SNR}, "
        f"{n_fit_failed} failed the Gaussian fit"
        + (f", {n_not_examined} not examined (cap of {MAX_STARS} already reached)" if n_not_examined else "")
    )

    if not fwhms:
        return None
    return float(np.mean(fwhms)), len(fwhms), float(min(snrs))


class FocusPlugin(SubPlugin):
    """Captures a guide image and logs its measured sharpness (mean FWHM
    over a handful of clean stars in a central window) -- diagnostic
    only, no session-blocking behavior yet."""

    async def execute(self, obs: Observation) -> Observation:
        exposure_seconds = SUBPLUGIN_PARAMS.get("guiding", {})["exposure_seconds"]
        guide_image = await capture_guide_image(obs, exposure_seconds, image_to_gui=True, image_source="focus")
        data = fits.getdata(guide_image)

        window_fraction = SUBPLUGIN_PARAMS.get("focus", {}).get("window_fraction", DEFAULT_WINDOW_FRACTION)
        result = measure_fwhm(data, window_fraction)
        if result is None:
            log.warning("[FOCUS] No usable star to measure focus sharpness")
            return obs

        mean_fwhm, n_stars, min_snr = result
        log.info(
            f"[FOCUS] Mean FWHM = {mean_fwhm:.2f}px over {n_stars} "
            f"star(s), weakest SNR = {min_snr:.0f}"
        )
        return obs
