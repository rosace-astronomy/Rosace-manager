"""
Relative flux measurement of a raw 2D science spectrum (one sub-exposure at a time).

Plain functions, not a sub-plugin: same status as axes_helpers.py. Used by the
calculate_flux REDUCTION sub-plugin, which stores the result in the
observation's casier and in rosace.db's spectrum_flux table.

Everything is measured on the raw frame -- no bias, dark or flat, no external
reference at all. The zeroed-background frame only ever exists inside
find_axes(), to locate the trace.

1. Trace location. find_axes() gives a first line (centre, angle); the frame
   is also cut into N_BANDS vertical bands, each band's summed profile fitted
   by a Gaussian, and a line fitted through the centres of the bands that
   pass the acceptance tests. find_axes() can fail silently on a faint star
   (a handful of pixels above its threshold, a width several times too small),
   so its line is kept only when the band line agrees with it; otherwise the
   band line is used. The trace width sigma always comes from a Gaussian fit
   of the whole cross profile along the retained line, never from
   find_axes(), whose sigma is the spread of the thresholded pixels only -- a
   different quantity, so switching between the two would change what the
   window means.
2. A window following the (tilted) trace, |y - yc(x)| <= WINDOW_HALF_SIGMA *
   sigma, no rotation and no interpolation: edge pixels are weighted by the
   fraction of their height inside the window, so every column is measured
   over exactly the same effective width wherever the trace sits in the pixel.
3. Hot pixels and small cosmic rays replaced by a running median along the
   dispersion axis, only where they stand far above it -- compared to the
   local noise, photon noise of the trace included.
4. Background measured in every column on both sides of the trace, then
   smoothed along the dispersion axis.
5. Plain sum, per column, of the window pixels minus that column's
   background: the flux above the background (offset, dark and sky removed
   together, gradients included).
6. Peak raw pixel of the window, background not subtracted (saturation check).

Meant for stars: a continuum across the whole frame with the same cross
profile in every column. An extended object with an emission-line spectrum
(M57) is measured the same way without complaint, but its sum means something
only relative to itself: its lines each have their own shape along the slit,
so the Gaussian width does not describe it (M57 on 2026-09-25: sigma 11.7 px,
against about 3 px for a star).

Every tuning value is a module constant tied to METHOD_VERSION: a change that
alters any measured value must bump it (test_spectrum_flux_helpers.py pins the
results of each version), so measurements stay comparable night to night.

Coordinates follow the app-wide pixel convention (see CLAUDE.md): 0-indexed,
Y upwards -- plain numpy indices, (x, y) = (column, row).
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.optimize import OptimizeWarning, curve_fit

from .axes_helpers import find_axes

METHOD_VERSION = 1

# Trace location
N_BANDS = 25
SEARCH_HALF_HEIGHT_FRACTION = 0.05  # band-fit search zone, fraction of the frame height, each side
BAND_MIN_SNR = 5.0
BAND_MAX_CENTER_ERROR_PX = 1.0
SIGMA_MIN_PX = 0.5
SIGMA_MAX_PX = 20.0
MIN_BANDS = 3
BAND_CLIP_SIGMA = 3.0
CONVERGENCE_ERROR_FACTOR = 3.0
CONVERGENCE_FLOOR_PX = 1.0
PROFILE_HALF_WIDTH_MIN_PX = 12.0
PROFILE_BIN_PX = 0.25

# Measurement
WINDOW_HALF_SIGMA = 4.0
BACKGROUND_INNER_SIGMA = 6.0
BACKGROUND_OUTER_SIGMA = 9.0
HOT_PIXEL_MEDIAN_PX = 5
HOT_PIXEL_THRESHOLD = 6.0
LOCAL_NOISE_COLUMNS = 51
BACKGROUND_SMOOTH_COLUMNS = 21
SATURATION_ADU = 60000.0

MAD_TO_SIGMA = 1.4826


class FluxMeasurementError(Exception):
    """The frame could not be measured (no trace found, width fit failed...)."""


@dataclass
class TraceLine:
    """Centre line of the trace: y(x) = y0 + (x - x0) * slope."""

    x0: float
    y0: float
    slope: float
    method: str  # "axes" or "bands"
    bands_used: int
    sigma_axes_px: float | None
    diverged: bool  # find_axes() found a line but the band line disagreed with it

    @property
    def angle_deg(self) -> float:
        return math.degrees(math.atan(self.slope))

    def y_at(self, x: np.ndarray | float) -> np.ndarray | float:
        return self.y0 + (x - self.x0) * self.slope


@dataclass
class SpectrumFlux:
    """Result of measure_spectrum_flux(). total_adu/column_max_adu are above
    the per-column background; peak_pixel_adu and background_adu are raw.
    notes: non-fatal remarks worth a log warning (not stored)."""

    method_version: int
    center_x: float
    center_y: float
    angle_deg: float
    trace_method: str
    bands_used: int
    trace_sigma_px: float
    sigma_axes_px: float | None
    background_adu: float
    background_noise_adu: float
    total_adu: float
    column_max_adu: float
    column_max_x: int
    peak_pixel_adu: float
    peak_pixel_x: int
    peak_pixel_y: int
    saturated: bool
    hot_pixels_replaced: int
    notes: list[str] = field(default_factory=list)


def _gaussian(t, amplitude, center, sigma, offset):
    return amplitude * np.exp(-0.5 * ((t - center) / sigma) ** 2) + offset


def _fit_gaussian(t: np.ndarray, y: np.ndarray, center_guess: float, sigma_guess: float):
    """(params, errors) of a Gaussian + constant fit, or None if it did not converge."""
    offset = float(np.median(y))
    p0 = [float(y.max() - offset), center_guess, sigma_guess, offset]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OptimizeWarning)
            params, covariance = curve_fit(_gaussian, t, y, p0=p0, maxfev=2000)
    except (RuntimeError, ValueError):
        return None
    errors = np.sqrt(np.abs(np.diag(covariance)))
    params[2] = abs(params[2])
    return params, errors


def _band_centres(frame: np.ndarray, y_guess: float) -> list[tuple[float, float, float]]:
    """(x, centre, centre error) of every band whose Gaussian fit passes the
    acceptance tests; bands with no signal fit noise and are dropped."""
    height, width = frame.shape
    band_width = width // N_BANDS
    half = max(3, int(round(SEARCH_HALF_HEIGHT_FRACTION * height)))
    lo, hi = max(0, int(round(y_guess)) - half), min(height, int(round(y_guess)) + half + 1)
    rows = np.arange(lo, hi, dtype=np.float64)
    accepted = []
    for band in range(N_BANDS):
        columns = slice(band * band_width, (band + 1) * band_width)
        profile = frame[lo:hi, columns].sum(axis=1)
        fit = _fit_gaussian(rows, profile, float(rows[np.argmax(profile)]), 2.0)
        if fit is None:
            continue
        (amplitude, centre, sigma, _), (amplitude_err, centre_err, _, _) = fit
        if not (np.isfinite(amplitude_err) and np.isfinite(centre_err)) or amplitude_err <= 0:
            continue
        if amplitude / amplitude_err < BAND_MIN_SNR or centre_err >= BAND_MAX_CENTER_ERROR_PX:
            continue
        if not (SIGMA_MIN_PX <= sigma <= SIGMA_MAX_PX) or not (lo <= centre <= hi - 1):
            continue
        x_centre = band * band_width + (band_width - 1) / 2.0
        accepted.append((x_centre, float(centre), float(centre_err)))
    return accepted


def _fit_band_line(bands: list[tuple[float, float, float]]):
    """Weighted straight line through the band centres, with iterative
    rejection of outlying bands. Returns (x0, y0, slope, covariance, n) with
    y0 taken at the weighted mean x0 (so y0 and slope are uncorrelated), or
    None with fewer than MIN_BANDS bands left."""
    x = np.array([b[0] for b in bands])
    y = np.array([b[1] for b in bands])
    err = np.array([b[2] for b in bands])
    keep = np.ones(x.size, dtype=bool)
    while keep.sum() >= MIN_BANDS:
        weights = 1.0 / err[keep] ** 2
        x0 = float(np.average(x[keep], weights=weights))
        design = np.column_stack([np.ones(keep.sum()), x[keep] - x0])
        covariance = np.linalg.inv(design.T @ (design * weights[:, None]))
        coef = covariance @ (design.T @ (weights * y[keep]))
        chi = (y - (coef[0] + coef[1] * (x - x0))) / err
        dof = keep.sum() - 2
        if dof > 0:
            covariance = covariance * max(1.0, float(np.sum(chi[keep] ** 2)) / dof)
        scale = max(1.0, MAD_TO_SIGMA * float(np.median(np.abs(chi[keep]))))
        outliers = keep & (np.abs(chi) > BAND_CLIP_SIGMA * scale)
        if not outliers.any():
            return x0, float(coef[0]), float(coef[1]), covariance, int(keep.sum())
        keep &= ~outliers
    return None


def locate_trace(frame: np.ndarray) -> TraceLine | None:
    """Centre line of the trace: find_axes() when the band line agrees with
    it, the band line otherwise (see module docstring). None when neither
    method finds a trace."""
    height, width = frame.shape
    axes = find_axes(frame)
    if axes is not None:
        y_guess = axes.cy
    else:
        # Row median, not row sum: a star or a defect confined to a few columns
        # can outshine the whole trace in a sum, never in a median
        y_guess = float(np.argmax(np.median(frame, axis=1)))

    bands = _band_centres(frame, y_guess)
    band_line = _fit_band_line(bands) if len(bands) >= MIN_BANDS else None
    sigma_axes = float(axes.sigma_minor) if axes is not None else None

    if axes is None and band_line is None:
        return None
    axes_line = None
    if axes is not None:
        axes_line = TraceLine(axes.cx, axes.cy, math.tan(math.radians(axes.angle_deg)), "axes", 0, sigma_axes, False)
    if band_line is None:
        return axes_line

    x0, y0, slope, covariance, used = band_line
    bands_line = TraceLine(x0, y0, slope, "bands", used, sigma_axes, False)
    if axes_line is None:
        return bands_line

    for x in (0.0, (width - 1) / 2.0, float(width - 1)):
        line_error = math.sqrt(covariance[0, 0] + (x - x0) ** 2 * covariance[1, 1])
        tolerance = max(CONVERGENCE_ERROR_FACTOR * line_error, CONVERGENCE_FLOOR_PX)
        if abs(axes_line.y_at(x) - bands_line.y_at(x)) > tolerance:
            bands_line.diverged = True
            return bands_line
    axes_line.bands_used = used
    return axes_line


def _running_nanmedian(values: np.ndarray, size: int) -> np.ndarray:
    smoothed = ndimage.generic_filter(values, np.nanmedian, size=size, mode="nearest")
    if np.isnan(smoothed).any():
        smoothed = np.where(np.isnan(smoothed), np.nanmedian(values), smoothed)
    return smoothed


def fit_trace_sigma(frame: np.ndarray, line: TraceLine, sigma_guess: float) -> float | None:
    """Width (Gaussian sigma, pixels) of the trace profile along `line`:
    pixels binned by their distance to the line (PROFILE_BIN_PX bins, no
    resampling), each column's background removed, Gaussian + constant
    fitted. None if the fit fails or gives an implausible width."""
    height, width = frame.shape
    half = max(PROFILE_HALF_WIDTH_MIN_PX, 4.0 * sigma_guess)
    outer = half + max(5.0, half / 2.0)
    yc = line.y_at(np.arange(width, dtype=np.float64))
    lo, hi = max(0, int(math.floor(yc.min() - outer))), min(height, int(math.ceil(yc.max() + outer)) + 1)
    if hi - lo < 3:
        return None
    dy = np.arange(lo, hi, dtype=np.float64)[:, None] - yc[None, :]
    band = frame[lo:hi]
    background = np.where((np.abs(dy) > half) & (np.abs(dy) <= outer), band, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        column_background = np.nanmedian(background, axis=0)
    column_background = np.where(np.isnan(column_background), np.nanmedian(column_background), column_background)
    signal = band - column_background[None, :]

    inside = np.abs(dy) <= half
    edges = np.arange(-half, half + PROFILE_BIN_PX / 2, PROFILE_BIN_PX)
    sums, _ = np.histogram(dy[inside], bins=edges, weights=signal[inside])
    counts, _ = np.histogram(dy[inside], bins=edges)
    valid = counts > 0
    centres = 0.5 * (edges[1:] + edges[:-1])
    fit = _fit_gaussian(centres[valid], sums[valid] / counts[valid], 0.0, sigma_guess)
    if fit is None:
        return None
    sigma = float(fit[0][2])
    if not (SIGMA_MIN_PX <= sigma <= SIGMA_MAX_PX) or not np.isfinite(fit[1][2]):
        return None
    return sigma


def measure_spectrum_flux(data: np.ndarray) -> SpectrumFlux:
    """Measure one raw 2D spectrum (see module docstring). Raises
    FluxMeasurementError when the frame cannot be measured."""
    frame = np.asarray(data, dtype=np.float64)
    height, width = frame.shape
    notes: list[str] = []

    # 1. Trace line and width
    line = locate_trace(frame)
    if line is None:
        raise FluxMeasurementError("no trace found (find_axes and the band fits both failed)")
    if line.diverged:
        notes.append(f"find_axes line disagrees with the band line -- band line used ({line.bands_used} bands)")
    elif line.method == "bands":
        notes.append(f"find_axes found no trace -- band line used ({line.bands_used} bands)")
    sigma_guess = line.sigma_axes_px if line.sigma_axes_px and line.method == "axes" else 3.0
    sigma = fit_trace_sigma(frame, line, sigma_guess)
    if sigma is None:
        raise FluxMeasurementError("trace width fit failed or implausible")

    # 2. Tilted band around the trace, window weights (edge pixels pro rata)
    x = np.arange(width, dtype=np.float64)
    yc = line.y_at(x)
    outer = BACKGROUND_OUTER_SIGMA * sigma
    lo, hi = max(0, int(math.floor(yc.min() - outer))), min(height, int(math.ceil(yc.max() + outer)) + 1)
    band = frame[lo:hi]
    abs_dy = np.abs(np.arange(lo, hi, dtype=np.float64)[:, None] - yc[None, :])
    in_band = abs_dy <= outer
    background_zone = in_band & (abs_dy >= BACKGROUND_INNER_SIGMA * sigma)
    weights = np.clip(WINDOW_HALF_SIGMA * sigma + 0.5 - abs_dy, 0.0, 1.0)
    window = weights > 0

    # 3. Hot pixels / small cosmic rays: far above the running median along the
    # dispersion axis. The noise they are compared to is local -- neighbour
    # differences along the same row, over LOCAL_NOISE_COLUMNS columns -- so it
    # includes the photon noise of the trace core; a noise taken over a whole
    # row underestimates it there and replaces real signal (only positive
    # excursions are replaced, so that would bias the flux low).
    median_along_x = ndimage.median_filter(band, size=(1, HOT_PIXEL_MEDIAN_PX), mode="nearest")
    residual = band - median_along_x
    neighbour_diff = np.abs(np.diff(band, axis=1, append=band[:, -1:]))
    local_noise = (MAD_TO_SIGMA / math.sqrt(2.0)) * ndimage.median_filter(
        neighbour_diff, size=(1, LOCAL_NOISE_COLUMNS), mode="nearest"
    )
    noise_floor = float(np.median(local_noise[background_zone])) if background_zone.any() else 0.0
    local_noise = np.maximum(local_noise, max(noise_floor, 1e-9))
    replaced = in_band & (residual > HOT_PIXEL_THRESHOLD * local_noise)
    clean = np.where(replaced, median_along_x, band)

    # 4. Background per column, both sides of the trace, smoothed along x
    samples = np.where(background_zone, clean, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        column_background = np.nanmedian(samples, axis=0)
        column_noise = MAD_TO_SIGMA * np.nanmedian(np.abs(samples - column_background[None, :]), axis=0)
    if np.isnan(column_background).all():
        raise FluxMeasurementError("no background pixels around the trace")
    column_background = _running_nanmedian(column_background, BACKGROUND_SMOOTH_COLUMNS)
    column_noise = _running_nanmedian(column_noise, BACKGROUND_SMOOTH_COLUMNS)

    # 5. Plain sum of the window, each pixel minus its column's background
    column_flux = (weights * (clean - column_background[None, :])).sum(axis=0)

    # 6. Peak raw pixel of the window (hot pixels already replaced)
    in_window = np.where(window, clean, -np.inf)
    peak_row, peak_col = np.unravel_index(int(np.argmax(in_window)), in_window.shape)
    peak_value = float(clean[peak_row, peak_col])

    return SpectrumFlux(
        method_version=METHOD_VERSION,
        center_x=float(line.x0),
        center_y=float(line.y0),
        angle_deg=float(line.angle_deg),
        trace_method=line.method,
        bands_used=int(line.bands_used),
        trace_sigma_px=float(sigma),
        sigma_axes_px=line.sigma_axes_px,
        background_adu=float(np.mean(column_background)),
        background_noise_adu=float(np.median(column_noise)),
        total_adu=float(column_flux.sum()),
        column_max_adu=float(column_flux.max()),
        column_max_x=int(np.argmax(column_flux)),
        peak_pixel_adu=peak_value,
        peak_pixel_x=int(peak_col),
        peak_pixel_y=int(lo + peak_row),
        saturated=bool(peak_value >= SATURATION_ADU),
        hot_pixels_replaced=int(replaced.sum()),
        notes=notes,
    )
