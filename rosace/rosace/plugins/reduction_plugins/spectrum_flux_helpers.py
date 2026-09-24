"""
Relative flux measurement of a 2D science spectrum (one sub-exposure at a time).

Plain functions, not a sub-plugin: same status as axes_helpers.py, which
provides the trace location and rotation used here.

Every pixel that does not belong to the detected trace is forced to 0 before
anything is summed, so hot pixels and cosmic rays above the threshold but away
from the trace contribute nothing. The price is that the faint wings of the
trace, below the threshold, are dropped too (roughly 7% of the energy on a
bright star). This is deliberate: the measurement is meant to be always the
same computation, compared night to night and frame to frame -- a relative
quantity, not a calibrated absolute flux.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .axes_helpers import DEFAULT_N_SIGMA, find_axes, rotate_to_horizontal


@dataclass
class SpectrumPower:
    """Result of measure_spectrum_power().

    total_power: sum of every retained pixel (background-subtracted, ADU).
    peak_intensity: highest value of the profile obtained by summing all the
        columns of the rotated image (ADU) -- a cut across the spectrum.
    peak_pixel: highest raw value (ADU, background included, not rotated)
        among the pixels retained as part of the trace -- what matters to
        judge how close the exposure is to saturation. Hot pixels away from
        the trace cannot reach it.
    profile_center: intensity-weighted centre of that profile (row index).
    profile_sigma: intensity-weighted standard deviation of that profile (pixels).
    angle_deg: trace orientation that was corrected by the rotation.
    """

    total_power: float
    peak_intensity: float
    peak_pixel: float
    profile_center: float
    profile_sigma: float
    angle_deg: float


def measure_spectrum_power(data: np.ndarray, n_sigma: float = DEFAULT_N_SIGMA) -> SpectrumPower | None:
    """Total power and peak of the column-summed profile of a 2D spectrum.

    Returns None when no trace is found in the frame (see find_axes()).
    """
    axes = find_axes(data, invert=False, n_sigma=n_sigma)
    if axes is None:
        return None

    cleaned = np.where(axes.mask, axes.signal, 0.0)
    rotated = rotate_to_horizontal(cleaned, axes.angle_deg)

    profile = rotated.sum(axis=1)
    rows = np.arange(profile.size)
    total = float(profile.sum())
    center = float((profile * rows).sum() / total)
    sigma = float(np.sqrt((profile * (rows - center) ** 2).sum() / total))

    return SpectrumPower(
        total_power=total,
        peak_intensity=float(profile.max()),
        peak_pixel=float(data[axes.mask].max()),
        profile_center=center,
        profile_sigma=sigma,
        angle_deg=axes.angle_deg,
    )
