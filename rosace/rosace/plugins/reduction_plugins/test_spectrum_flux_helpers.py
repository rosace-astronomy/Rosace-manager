"""
Tests for spectrum_flux_helpers.py (relative flux measurement of a raw 2D
spectrum), on synthetic frames whose true flux is known.
Run: pytest plugins/reduction_plugins/test_spectrum_flux_helpers.py -v
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from rosace.plugins.reduction_plugins import spectrum_flux_helpers
from rosace.plugins.reduction_plugins.spectrum_flux_helpers import (
    METHOD_VERSION,
    FluxMeasurementError,
    measure_spectrum_flux,
)

SHAPE = (200, 500)


def _frame(row=100.3, angle_deg=0.15, amplitude=400.0, sigma=3.0, background=300.0, gradient=0.05, noise=8.0, seed=0):
    """Raw-like frame: background with a horizontal gradient and Gaussian noise,
    plus a Gaussian-profile trace through (x=250, y=row) whose peak varies
    slowly along x like a continuum."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    img = background + gradient * xx + rng.normal(0.0, noise, SHAPE)
    peak = amplitude * (1.0 + 0.3 * np.sin(xx / 60.0))
    line_y = row + np.tan(np.radians(angle_deg)) * (xx - SHAPE[1] / 2)
    return img + peak * np.exp(-0.5 * ((yy - line_y) / sigma) ** 2)


def _true_total(amplitude=400.0, sigma=3.0):
    """Exact flux of _frame()'s trace above the background."""
    x = np.arange(SHAPE[1])
    return float((amplitude * (1.0 + 0.3 * np.sin(x / 60.0))).sum() * sigma * math.sqrt(2.0 * math.pi))


class TestMeasureSpectrumFlux:

    def test_recovers_the_true_flux_above_a_background_gradient(self):
        result = measure_spectrum_flux(_frame())
        assert result.total_adu == pytest.approx(_true_total(), rel=0.005)
        assert result.trace_sigma_px == pytest.approx(3.0, rel=0.03)
        assert result.angle_deg == pytest.approx(0.15, abs=0.02)
        assert result.background_adu == pytest.approx(300.0 + 0.05 * (SHAPE[1] - 1) / 2, abs=1.0)

    def test_bright_trace_keeps_find_axes_line(self):
        result = measure_spectrum_flux(_frame())
        assert result.trace_method == "axes"
        assert result.bands_used == spectrum_flux_helpers.N_BANDS
        assert result.notes == []

    def test_column_max_is_the_brightest_column_not_a_row(self):
        result = measure_spectrum_flux(_frame())
        # peak continuum at sin(x/60) = 1 -> x ~ 94; each column holds ~1/500 of the total
        assert 60 <= result.column_max_x <= 130
        assert result.total_adu / result.column_max_adu > 300

    @pytest.mark.parametrize("sub_pixel", [0.25, 0.5, 0.75])
    def test_flux_does_not_depend_on_where_the_trace_sits_in_the_pixel(self, sub_pixel):
        reference = measure_spectrum_flux(_frame(row=100.0)).total_adu
        shifted = measure_spectrum_flux(_frame(row=100.0 + sub_pixel)).total_adu
        assert shifted == pytest.approx(reference, rel=0.002)

    def test_hot_pixel_in_the_trace_is_replaced(self):
        clean_frame = _frame(seed=5)
        hot_frame = clean_frame.copy()
        hot_frame[100, 250] += 20000.0
        clean, hot = measure_spectrum_flux(clean_frame), measure_spectrum_flux(hot_frame)
        assert hot.hot_pixels_replaced == clean.hot_pixels_replaced + 1
        assert hot.total_adu == pytest.approx(clean.total_adu, rel=0.001)
        assert hot.peak_pixel_adu < 5000.0

    def test_real_signal_is_not_taken_for_hot_pixels(self):
        assert measure_spectrum_flux(_frame(amplitude=5000.0)).hot_pixels_replaced == 0

    def test_peak_pixel_is_raw_background_included(self):
        result = measure_spectrum_flux(_frame(amplitude=400.0, background=300.0))
        assert 300.0 + 400.0 < result.peak_pixel_adu < 300.0 + 1.3 * 400.0 + 60.0
        assert result.saturated is False

    def test_saturation_flag(self):
        assert measure_spectrum_flux(_frame(amplitude=65000.0)).saturated is True

    @pytest.mark.parametrize("amplitude", [6.0, 20.0])
    def test_faint_trace_missed_by_find_axes_is_measured_with_the_bands(self, amplitude):
        result = measure_spectrum_flux(_frame(amplitude=amplitude, seed=3))
        assert result.trace_method == "bands"
        assert result.trace_sigma_px == pytest.approx(3.0, rel=0.1)
        assert result.total_adu == pytest.approx(_true_total(amplitude), rel=0.05)
        assert any("band line used" in note for note in result.notes)

    def test_bands_without_signal_are_discarded(self):
        yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
        noise_only = 300.0 + 0.05 * xx + np.random.default_rng(9).normal(0.0, 8.0, SHAPE)
        frame = np.where((xx < 150) | (xx > 350), noise_only, _frame())
        result = measure_spectrum_flux(frame)
        assert result.bands_used < spectrum_flux_helpers.N_BANDS / 2 + 2
        assert result.bands_used >= spectrum_flux_helpers.MIN_BANDS

    def test_no_trace_raises(self):
        noise_only = np.random.default_rng(1).normal(300.0, 8.0, SHAPE)
        with pytest.raises(FluxMeasurementError):
            measure_spectrum_flux(noise_only)


# Results of each method version on _frame() (default arguments). A change that
# alters any of them must bump METHOD_VERSION and add a new entry here -- never
# edit an existing one: stored measurements must stay comparable.
EXPECTED = {
    1: {
        "trace_method": "axes",
        "bands_used": 25,
        "center_y": 100.30102186325054,
        "angle_deg": 0.15196876524049685,
        "trace_sigma_px": 3.004397635216868,
        "background_adu": 312.6499893101399,
        "total_adu": 1579751.6606920834,
        "column_max_adu": 3964.2598503213867,
        "column_max_x": 108,
        "peak_pixel_adu": 860.6434801036235,
        "peak_pixel_x": 487,
        "peak_pixel_y": 101,
        "hot_pixels_replaced": 0,
    },
}


def test_method_version_results_are_frozen():
    result = measure_spectrum_flux(_frame())
    for key, value in EXPECTED[METHOD_VERSION].items():
        measured = getattr(result, key)
        if isinstance(value, float):
            assert measured == pytest.approx(value, rel=1e-6), key
        else:
            assert measured == value, key
