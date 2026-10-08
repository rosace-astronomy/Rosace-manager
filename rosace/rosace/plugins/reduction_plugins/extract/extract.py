"""
REDUCTION sub-plugin — Spectral extraction
Extracts a 1D spectrum from each corrected target and lamp frame: sum of a window around the trace, sky subtracted column by column for the target.

Measured once, on the sum of the smile-corrected target frames (shared key
smile_target_images):
- the trace row, by the principal axes on the lit band
  (reduction_helpers.find_trace());
- its width: a Gaussian fitted on the vertical profile of each of
  N_COLUMN_GROUPS groups of columns (independent of the sensor size), keeping
  the groups whose peak reaches GROUP_MIN_PEAK_FRACTION of the brightest --
  the trace widens towards the blue, so the LARGEST sigma is kept;
- the window: a whole number of rows, the trace row +- ceil(WINDOW_SIGMA *
  sigma);
- the sky zones: inside the band lit through the slit (from the master flat,
  minus LIT_BAND_MARGIN_FRACTION of its height at each edge) and beyond
  SKY_INNER_SIGMA * sigma from the trace, one below and one above (a side
  with no room is dropped, none at all is an error).

Target frames: in each column, the sky level is the mean of that column's
pixels in both zones after iterative rejection of those further than
SKY_CLIP_SIGMA (sky gradient, light-pollution lines and hot pixels are thus
handled column by column), subtracted from every window pixel of the
column; the spectrum is the window's sum. Lamp frames (smile_calib_images):
the same window, no sky subtraction (the lamp lights the whole slit). Dead
columns (master flat below MIN_FLAT_FOR_MEASUREMENT over the window) are 0
in every spectrum, so its length stays the image's width.

Each spectrum is written as a 1D FITS (NAXIS = 1, one value per column) to
the observation's intermediate directory as "<name>_1d.fits", under the
shared keys extract_target_spectra / extract_calib_spectra. The trace row,
sigma, window and sky zones go to the casier reduction_data["extract"] and
to the log; extract_zones.png, in the same directory, shows the window and
the sky zones over the target sum.
"""

from __future__ import annotations

DISPLAY_NAME = "Extract"

import logging
import math
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import (
    LIT_BAND_MARGIN_FRACTION,
    MIN_FLAT_FOR_MEASUREMENT,
    find_shared_input,
    find_trace,
    flat_band,
    intermediate_dir,
    write_float_image,
)
from ..spectrum_flux_helpers import SIGMA_MAX_PX, SIGMA_MIN_PX, _fit_gaussian

log = logging.getLogger("spectro.subplugin.extract")

N_COLUMN_GROUPS = 25
GROUP_MIN_PEAK_FRACTION = 0.10
PROFILE_HALF_HEIGHT_FRACTION = 0.05  # profile fitted over the trace row +- this fraction of the frame height
WINDOW_SIGMA = 3.0
SKY_INNER_SIGMA = 6.0
SKY_CLIP_SIGMA = 3.0

ZONES_PICTURE = "extract_zones.png"


def _sum_frames(session_dir: Path, files: list[str]) -> np.ndarray:
    return sum(fits.getdata(session_dir / name).astype(np.float64) for name in files)


def _largest_sigma(image: np.ndarray, trace_row: int) -> tuple[float, int]:
    """Largest Gaussian sigma of the trace profile among the column groups
    bright enough (see module docstring), and how many groups were kept."""
    height, width = image.shape
    half = max(10, int(PROFILE_HALF_HEIGHT_FRACTION * height))
    rows = np.arange(max(0, trace_row - half), min(height, trace_row + half + 1))
    group_width = width // N_COLUMN_GROUPS
    fits_found = []
    for g in range(N_COLUMN_GROUPS):
        profile = image[rows, g * group_width:(g + 1) * group_width].mean(axis=1)
        fit = _fit_gaussian(rows.astype(np.float64), profile, float(trace_row), 3.0)
        if fit is None:
            continue
        amplitude, centre, sigma, _ = fit[0]
        if amplitude > 0 and SIGMA_MIN_PX <= sigma <= SIGMA_MAX_PX and abs(centre - trace_row) < half:
            fits_found.append((amplitude, sigma))
    if not fits_found:
        return math.nan, 0
    brightest = max(a for a, _ in fits_found)
    kept = [s for a, s in fits_found if a >= GROUP_MIN_PEAK_FRACTION * brightest]
    return max(kept), len(kept)


def _write_spectrum(obs: Observation, session_dir: Path, name: str, spectrum: np.ndarray) -> str:
    header = fits.getheader(session_dir / name)
    header.remove("NAXIS2", ignore_missing=True)
    out_path = intermediate_dir(obs) / f"{Path(name).stem}_1d.fits"
    write_float_image(out_path, spectrum, header, [])
    return str(out_path.relative_to(session_dir))


def _draw_zones(path: Path, image: np.ndarray, band: tuple[int, int], window: tuple[int, int], zones: list[list[int]]) -> None:
    """PNG of the target sum with the spectrum window (cyan) and the sky zones
    (magenta), origin at the bottom like the GUI. Built without pyplot, which
    keeps global state and is not meant for a worker thread."""
    height, width = image.shape
    lit = image[band[0]:band[1] + 1]
    low, high = np.percentile(lit, [1, 99])
    figure = Figure(figsize=(width / 100, height / 100), dpi=100)
    FigureCanvasAgg(figure)
    axes = figure.add_axes((0, 0, 1, 1))
    axes.imshow(image, origin="lower", cmap="gray", vmin=low, vmax=high, aspect="auto",
                extent=(-0.5, width - 0.5, -0.5, height - 0.5))
    axes.axhspan(window[0] - 0.5, window[1] + 0.5, color="cyan", alpha=0.25)
    for zone in zones:
        axes.axhspan(zone[0] - 0.5, zone[1] + 0.5, color="magenta", alpha=0.2)
    axes.set_axis_off()
    figure.savefig(path)


class ExtractPlugin(SubPlugin):
    """Extracts a 1D spectrum from each corrected target and lamp frame (window around the trace, sky subtracted column by column for the target)."""

    async def execute(self, obs: Observation) -> Observation:
        session_dir = Path(obs.obs_dir or "")
        target_files = find_shared_input(obs, "smile_target_images")
        calib_files = find_shared_input(obs, "smile_calib_images")
        flat = fits.getdata(session_dir / find_shared_input(obs, "master_flat_image")).astype(np.float64)

        target_sum = _sum_frames(session_dir, target_files)
        axes = find_trace(target_sum, flat)
        if axes is None:
            raise RuntimeError(f"EXTRACT: no spectrum trace found on the sum of {len(target_files)} target frames")
        trace_row = int(round(axes.cy))
        sigma, groups = _largest_sigma(target_sum, trace_row)
        if groups == 0:
            raise RuntimeError("EXTRACT: the trace width could not be measured in any column group")
        half = math.ceil(WINDOW_SIGMA * sigma)
        window = (trace_row - half, trace_row + half)

        band_low, band_high = flat_band(flat)
        margin = LIT_BAND_MARGIN_FRACTION * (band_high - band_low)
        band_low, band_high = math.ceil(band_low + margin), math.floor(band_high - margin)
        gap = math.ceil(SKY_INNER_SIGMA * sigma)
        zones = [z for z in ([band_low, trace_row - gap], [trace_row + gap, band_high]) if z[0] <= z[1]]
        if not zones:
            raise RuntimeError(
                f"EXTRACT: no room for a sky zone between the lit band {band_low}-{band_high} "
                f"and {SKY_INNER_SIGMA:g} sigma ({gap} px) around the trace row {trace_row}"
            )
        sky_rows = np.concatenate([np.arange(z[0], z[1] + 1) for z in zones])
        window_rows = slice(window[0], window[1] + 1)
        # Dead columns (master flat below MIN_FLAT_FOR_MEASUREMENT over the
        # window) are 0 in every spectrum, which keeps the image's length
        dead_columns = np.median(flat[window_rows], axis=0) < MIN_FLAT_FOR_MEASUREMENT

        target_spectra = []
        for name in target_files:
            data = fits.getdata(session_dir / name).astype(np.float64)
            sky, _, _ = sigma_clipped_stats(data[sky_rows], sigma=SKY_CLIP_SIGMA, maxiters=None, axis=0)
            spectrum = data[window_rows].sum(axis=0) - (2 * half + 1) * sky
            spectrum[dead_columns] = 0.0
            target_spectra.append(_write_spectrum(obs, session_dir, name, spectrum))
        calib_spectra = []
        for name in calib_files:
            data = fits.getdata(session_dir / name).astype(np.float64)
            spectrum = data[window_rows].sum(axis=0)
            spectrum[dead_columns] = 0.0
            calib_spectra.append(_write_spectrum(obs, session_dir, name, spectrum))

        _draw_zones(intermediate_dir(obs) / ZONES_PICTURE, target_sum, (band_low, band_high), window, zones)
        obs.reduction_data["extract_target_spectra"] = target_spectra
        obs.reduction_data["extract_calib_spectra"] = calib_spectra
        obs.reduction_data["extract"] = {
            "trace_y": trace_row,
            "trace_sigma_px": round(float(sigma), 2),
            "window": list(window),
            "sky_zones": zones,
        }
        sky_text = " / ".join(f"{z[0]}-{z[1]}" for z in zones)
        log.info(
            f"[EXTRACT] Extract: trace y={trace_row}, sigma {sigma:.2f} px ({groups} groups), "
            f"window {window[0]}-{window[1]}, sky {sky_text}, "
            f"{len(target_spectra)} target + {len(calib_spectra)} lamp spectra"
        )
        return obs
