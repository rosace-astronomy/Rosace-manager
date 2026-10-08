"""
REDUCTION sub-plugin — Smile correction
Measures the line curvature (smile) on the lamp and removes it from lamp and target frames by a horizontal shift per row.

Measured on the sum of the tilt-corrected lamp frames (shared key
tilt_calib_images): each row is cross-correlated (CCF) with the reference --
the rows around the trace row -- which gives, to a fraction of a pixel, how
far that row's emission lines are shifted horizontally. Only the rows well
inside the band lit through the slit are used: rows whose level (99th
percentile of the row) reaches LIT_BAND_LEVEL_FRACTION of the trace row's,
minus LIT_BAND_MARGIN_FRACTION of the band's height at each edge (slit edge
effects); a darker row inside the band (dust on the slit) is simply left
out. A parabola shift(y) = a*y**2 + b*y + c is fitted on these rows,
rejecting rows more than CLIP_SIGMA from it until stable.

The trace row is found again here, by the principal axes on the sum of the
tilt-corrected target frames (tilt_target_images), on the lit band only
(reduction_helpers.find_trace()). Every row of every lamp
and target frame is then shifted by -(shift(y) - shift(trace row)), by
overlap redistribution (reduction_helpers.shift_rows(), each row's total
kept): the lines become straight and vertical, the trace row does not move.
Corrected frames go to the observation's intermediate directory as
"<name>_smile.fits", under the shared keys smile_calib_images /
smile_target_images; the casier reduction_data["smile"] holds the three
coefficients (y in Rosace's 0-indexed pixel convention).
"""

from __future__ import annotations

DISPLAY_NAME = "Smile"

import logging
from pathlib import Path

import numpy as np
from astropy.io import fits

from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import (
    LIT_BAND_LEVEL_FRACTION,
    LIT_BAND_MARGIN_FRACTION,
    MIN_FLAT_FOR_MEASUREMENT,
    find_shared_input,
    find_trace,
    shift_rows,
    transform_frames,
)

log = logging.getLogger("spectro.subplugin.smile")

CLIP_SIGMA = 3.0             # rows further than this from the parabola are rejected
MAX_LAG_PX = 50              # largest shift searched by the cross-correlation
REFERENCE_HALF_ROWS = 2      # reference profile = mean of the trace row +- this many rows
MIN_ROWS = 10                # fewer usable rows is an error


def _sum_frames(session_dir: Path, files: list[str]) -> np.ndarray:
    return sum(fits.getdata(session_dir / name).astype(np.float64) for name in files)


def _used_rows(rows: np.ndarray, trace_row: int) -> np.ndarray:
    """Rows of the lit band used for the measurement (see module docstring)."""
    level = np.percentile(rows, 99, axis=1)
    lit = level >= LIT_BAND_LEVEL_FRACTION * level[trace_row]
    lit_rows = np.nonzero(lit)[0]
    if len(lit_rows) == 0:
        return lit_rows
    margin = LIT_BAND_MARGIN_FRACTION * (lit_rows[-1] - lit_rows[0])
    return lit_rows[(lit_rows >= lit_rows[0] + margin) & (lit_rows <= lit_rows[-1] - margin)]


def _row_shifts(rows: np.ndarray, trace_row: int) -> np.ndarray:
    """Horizontal shift of every row against the reference, to a fraction of
    a pixel: maximum of the cross-correlation, refined by a parabola through
    it and its two neighbours. All rows at once through the Fourier
    transform, zero-padded so the correlation does not wrap around. NaN
    where no clear maximum lies inside +-MAX_LAG_PX."""
    height, width = rows.shape
    reference = rows[trace_row - REFERENCE_HALF_ROWS:trace_row + REFERENCE_HALF_ROWS + 1].mean(axis=0)
    size = 2 * width
    ccf = np.fft.irfft(np.fft.rfft(rows, n=size, axis=1) * np.conj(np.fft.rfft(reference, n=size)), n=size, axis=1)
    # Lags -MAX_LAG_PX..+MAX_LAG_PX: ccf[:, k] = sum over x of row[x + k] * reference[x]
    ccf = np.concatenate([ccf[:, -MAX_LAG_PX:], ccf[:, :MAX_LAG_PX + 1]], axis=1)
    peak = np.argmax(ccf, axis=1)
    inner = np.clip(peak, 1, ccf.shape[1] - 2)
    index = np.arange(height)
    left, centre, right = ccf[index, inner - 1], ccf[index, inner], ccf[index, inner + 1]
    curvature = left - 2 * centre + right
    valid = (peak == inner) & (curvature < 0)
    fraction = np.divide(0.5 * (left - right), curvature, out=np.zeros(height), where=valid)
    return np.where(valid, inner - MAX_LAG_PX + fraction, np.nan)


def _fit_parabola(y: np.ndarray, shift: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Parabola through (y, shift), refitted without the points further than
    CLIP_SIGMA from it until no point changes side. Returns the
    coefficients (a, b, c) and the mask of the points kept."""
    kept = np.ones(len(y), dtype=bool)
    while True:
        coefficients = np.polyfit(y[kept], shift[kept], 2)
        residuals = shift - np.polyval(coefficients, y)
        new_kept = np.abs(residuals) <= CLIP_SIGMA * residuals[kept].std()
        if np.array_equal(new_kept, kept) or new_kept.sum() < MIN_ROWS:
            return coefficients, kept
        kept = new_kept


class SmilePlugin(SubPlugin):
    """Measures the line curvature (smile) on the lamp and removes it from lamp and target frames by a horizontal shift per row."""

    async def execute(self, obs: Observation) -> Observation:
        session_dir = Path(obs.obs_dir or "")
        target_files = find_shared_input(obs, "tilt_target_images")
        calib_files = find_shared_input(obs, "tilt_calib_images")
        flat = fits.getdata(session_dir / find_shared_input(obs, "master_flat_image")).astype(np.float64)
        dead = flat < MIN_FLAT_FOR_MEASUREMENT

        axes = find_trace(_sum_frames(session_dir, target_files), flat)
        if axes is None:
            raise RuntimeError(f"SMILE: no spectrum trace found on the sum of {len(target_files)} target frames")
        lamp = np.where(dead, 0.0, _sum_frames(session_dir, calib_files))
        rows = lamp - np.median(lamp, axis=1, keepdims=True)
        trace_row = int(round(axes.cy))

        used = _used_rows(rows, trace_row)
        shifts = _row_shifts(rows, trace_row)[used]
        used, shifts = used[np.isfinite(shifts)], shifts[np.isfinite(shifts)]
        if len(used) < MIN_ROWS:
            raise RuntimeError(f"SMILE: only {len(used)} lamp rows usable, at least {MIN_ROWS} needed")
        coefficients, kept = _fit_parabola(used.astype(np.float64), shifts)

        def model(y):
            return np.polyval(coefficients, y) - np.polyval(coefficients, trace_row)

        corrections = -model(np.arange(rows.shape[0], dtype=np.float64))

        def correct(data: np.ndarray) -> np.ndarray:
            return shift_rows(data, corrections)

        obs.reduction_data["smile_calib_images"] = transform_frames(obs, calib_files, "smile", correct, [])
        obs.reduction_data["smile_target_images"] = transform_frames(obs, target_files, "smile", correct, [])
        obs.reduction_data["smile"] = {"coefficients": [float(c) for c in coefficients]}

        first, last = int(used[0]), int(used[-1])
        rms = float((shifts[kept] - np.polyval(coefficients, used[kept])).std())
        log.info(
            f"[SMILE] Smile: rows {first}-{last} used ({kept.sum()}, {(~kept).sum()} rejected), "
            f"shift {model(first):+.2f} px at row {first} / {model(last):+.2f} px at row {last}, "
            f"fit rms {rms:.3f} px, {len(target_files)} target + {len(calib_files)} lamp frames corrected"
        )
        return obs
