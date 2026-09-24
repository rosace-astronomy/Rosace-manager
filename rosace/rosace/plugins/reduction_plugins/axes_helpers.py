"""
Principal axes of inertia of a bright (or dark) feature in a 2D image, and
rotation of an image so that this feature becomes horizontal.

Plain functions, not a sub-plugin: same status as
plugins/sequencer_plugins/acquisition_helpers.py -- not resolved by
pipeline_loader.py's dynamic importlib convention, no program YAML ever
declares this module as a step.

Deliberately generic: the same find_axes() serves a science spectrum (a bright
trace on a dark background) and, with invert=True, the spectrograph slit seen
by the guiding camera (a dark feature on a brighter background). Anything
specific to spectra (band isolation, flux) lives in spectrum_flux_helpers.py.

Coordinates follow the app-wide pixel convention (see CLAUDE.md): 0-indexed,
origin at the centre of the bottom-left pixel, X to the right, Y upwards --
i.e. plain numpy indices, (x, y) = (column, row) of the FITS data array.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from astropy.stats import sigma_clipped_stats
from scipy import ndimage

DEFAULT_N_SIGMA = 5.0
MIN_COMPONENT_PIXELS = 20


@dataclass(eq=False)
class FeatureAxes:
    """Result of find_axes().

    cx, cy: intensity-weighted centroid of the retained pixels (pixels).
    angle_deg: orientation of the major axis, folded into (-90, 90] -- the
        eigenvector sign is arbitrary, so 179.9 and -0.1 are the same axis.
    sigma_major, sigma_minor: standard deviations of the retained pixels along
        the major and minor axes (pixels).
    background, sigma: sigma-clipped background level and noise of the frame.
    mask: boolean array, True for the retained pixels (same shape as the frame).
    signal: float array, the background-subtracted frame, sign-flipped when
        invert=True so that the feature is always positive.
    """

    cx: float
    cy: float
    angle_deg: float
    sigma_major: float
    sigma_minor: float
    background: float
    sigma: float
    mask: np.ndarray
    signal: np.ndarray


def find_axes(
    data: np.ndarray,
    invert: bool = False,
    n_sigma: float = DEFAULT_N_SIGMA,
    min_component_pixels: int = MIN_COMPONENT_PIXELS,
) -> FeatureAxes | None:
    """Locate the dominant feature of a frame and its principal axes of inertia.

    Steps: sigma-clipped background/noise of the whole frame; keep only pixels
    more than n_sigma above the background (below it when invert=True); drop
    every connected group of fewer than min_component_pixels pixels (hot pixels,
    cosmic rays) while keeping every larger one -- an emission-line spectrum is
    several separate segments, not one blob; then the intensity-weighted
    centroid and covariance of the retained pixels, diagonalised with eigh().

    Returns None when nothing survives (no feature in the frame) -- never an
    axis fabricated from noise.
    """
    frame = data.astype(np.float64)
    background, _, sigma = sigma_clipped_stats(frame, sigma=3.0)
    signal = (background - frame) if invert else (frame - background)

    candidates = signal > n_sigma * sigma
    labels, count = ndimage.label(candidates)
    if count == 0:
        return None
    sizes = ndimage.sum(candidates, labels, index=np.arange(1, count + 1))
    mask = np.isin(labels, 1 + np.flatnonzero(sizes >= min_component_pixels))
    if not mask.any():
        return None

    ys, xs = np.nonzero(mask)
    weights = signal[mask]
    cx = float(np.average(xs, weights=weights))
    cy = float(np.average(ys, weights=weights))
    dx, dy = xs - cx, ys - cy
    covariance = np.array(
        [
            [np.average(dx * dx, weights=weights), np.average(dx * dy, weights=weights)],
            [np.average(dx * dy, weights=weights), np.average(dy * dy, weights=weights)],
        ]
    )
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)  # ascending order
    major = eigenvectors[:, 1]

    angle_deg = float(np.degrees(np.arctan2(major[1], major[0])))
    if angle_deg > 90.0:
        angle_deg -= 180.0
    elif angle_deg <= -90.0:
        angle_deg += 180.0

    return FeatureAxes(
        cx=cx,
        cy=cy,
        angle_deg=angle_deg,
        sigma_major=float(np.sqrt(eigenvalues[1])),
        sigma_minor=float(np.sqrt(eigenvalues[0])),
        background=float(background),
        sigma=float(sigma),
        mask=mask,
        signal=signal,
    )


def rotate_to_horizontal(image: np.ndarray, angle_deg: float) -> np.ndarray:
    """Rotate an image about its centre so a feature at angle_deg becomes horizontal.

    The image must already be background-subtracted: the corners uncovered by
    the rotation are filled with 0, which then adds no energy. The interpolation
    itself does not conserve the total exactly, so the result is rescaled to
    have the same sum as the input -- the total power is therefore identical
    before and after the rotation (unless the input sums to 0, returned as is).
    """
    rotated = ndimage.rotate(
        image.astype(np.float64), angle_deg, reshape=False, order=1, mode="constant", cval=0.0
    )
    total_after = rotated.sum()
    if total_after != 0.0:
        rotated *= image.sum() / total_after
    return rotated
