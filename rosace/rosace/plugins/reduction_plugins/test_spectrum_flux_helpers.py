"""
Tests for spectrum_flux_helpers.py (relative flux measurement of a 2D
spectrum), on synthetic frames.
Run: pytest plugins/reduction_plugins/test_spectrum_flux_helpers.py -v
"""

from __future__ import annotations

import numpy as np
import pytest

from rosace.plugins.reduction_plugins.axes_helpers import find_axes
from rosace.plugins.reduction_plugins.spectrum_flux_helpers import measure_spectrum_power

SHAPE = (200, 400)


def _frame(row=100.0, angle_deg=0.0, amplitude=500.0, width=2.0, background=100.0, noise=5.0, seed=0):
    """Noisy background plus a Gaussian-profile trace through (x=200, y=row)."""
    rng = np.random.default_rng(seed)
    img = rng.normal(background, noise, SHAPE)
    yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    line_y = row + np.tan(np.radians(angle_deg)) * (xx - SHAPE[1] / 2)
    return img + amplitude * np.exp(-0.5 * ((yy - line_y) / width) ** 2)


class TestMeasureSpectrumPower:

    def test_no_trace_returns_none(self):
        noise_only = np.random.default_rng(1).normal(100.0, 5.0, SHAPE)
        assert measure_spectrum_power(noise_only) is None

    def test_total_power_is_the_sum_of_the_retained_signal(self):
        img = _frame(angle_deg=2.0)
        axes = find_axes(img)
        result = measure_spectrum_power(img)
        assert result.total_power == pytest.approx(axes.signal[axes.mask].sum(), rel=1e-9)

    def test_profile_locates_and_sizes_the_trace(self):
        result = measure_spectrum_power(_frame(row=100.0, width=2.0))
        assert result.profile_center == pytest.approx(100.0, abs=0.3)
        assert result.profile_sigma == pytest.approx(2.0, abs=0.4)
        assert result.peak_intensity < result.total_power

    def test_rotation_does_not_change_the_measured_power(self):
        flat = measure_spectrum_power(_frame(angle_deg=0.0, seed=2))
        tilted = measure_spectrum_power(_frame(angle_deg=3.0, seed=2))
        assert tilted.total_power == pytest.approx(flat.total_power, rel=0.03)
        assert tilted.angle_deg == pytest.approx(3.0, abs=0.3)

    def test_hot_pixels_away_from_the_trace_are_ignored(self):
        clean = measure_spectrum_power(_frame(seed=3))
        img = _frame(seed=3)
        rng = np.random.default_rng(4)
        for _ in range(40):
            img[rng.integers(0, 60), rng.integers(0, SHAPE[1])] = 60000.0
        dirty = measure_spectrum_power(img)
        assert dirty.total_power == pytest.approx(clean.total_power, rel=1e-3)
        assert dirty.peak_pixel < 5000.0  # the 60000 hot pixels must not leak in

    def test_peak_pixel_is_the_raw_value_background_included(self):
        result = measure_spectrum_power(_frame(amplitude=500.0, background=100.0))
        assert 550.0 < result.peak_pixel < 700.0  # ~ 100 + 500, plus noise

    def test_power_scales_with_the_signal(self):
        weak = measure_spectrum_power(_frame(amplitude=300.0, seed=5))
        strong = measure_spectrum_power(_frame(amplitude=600.0, seed=5))
        assert strong.total_power / weak.total_power == pytest.approx(2.0, rel=0.1)
