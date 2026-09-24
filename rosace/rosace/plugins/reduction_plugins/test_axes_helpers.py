"""
Tests for axes_helpers.py (principal axes of inertia of a bright or dark
feature, and rotation making it horizontal), on synthetic frames.
Run: pytest plugins/reduction_plugins/test_axes_helpers.py -v
"""

from __future__ import annotations

import numpy as np
import pytest

from rosace.plugins.reduction_plugins.axes_helpers import find_axes, rotate_to_horizontal

SHAPE = (200, 400)


def _frame(row=100.0, angle_deg=0.0, amplitude=500.0, width=2.0, background=100.0, noise=5.0,
           x_ranges=None, right_amplitude=None, seed=0):
    """Noisy background plus a Gaussian-profile line through (x=200, y=row).

    angle_deg > 0 means Y increases with X. x_ranges restricts the line to some
    column intervals (an emission-line spectrum is several separate segments).
    right_amplitude, when given, replaces amplitude for the columns x >= 200.
    """
    rng = np.random.default_rng(seed)
    img = rng.normal(background, noise, SHAPE)
    yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    line_y = row + np.tan(np.radians(angle_deg)) * (xx - SHAPE[1] / 2)
    line = amplitude * np.exp(-0.5 * ((yy - line_y) / width) ** 2)
    if right_amplitude is not None:
        line[:, SHAPE[1] // 2 :] *= right_amplitude / amplitude
    if x_ranges is not None:
        keep = np.zeros(SHAPE[1], dtype=bool)
        for x0, x1 in x_ranges:
            keep[x0:x1] = True
        line = line * keep[None, :]
    return img + line


class TestFindAxes:

    def test_horizontal_trace(self):
        axes = find_axes(_frame(row=100.0))
        assert axes is not None
        assert axes.cy == pytest.approx(100.0, abs=0.3)
        assert axes.angle_deg == pytest.approx(0.0, abs=0.2)
        assert axes.sigma_minor < axes.sigma_major

    @pytest.mark.parametrize("angle", [3.0, -3.0])
    def test_tilted_trace_angle_keeps_its_sign(self, angle):
        axes = find_axes(_frame(angle_deg=angle))
        assert axes.angle_deg == pytest.approx(angle, abs=0.3)

    def test_angle_is_folded_into_half_open_interval(self):
        # A vertical trace is the worst case: +90 and -90 are the same axis.
        img = _frame().T[:, :200].copy()  # 400 rows x 200 columns, trace now vertical
        axes = find_axes(img)
        assert -90.0 < axes.angle_deg <= 90.0
        assert abs(axes.angle_deg) == pytest.approx(90.0, abs=1.0)

    def test_pure_noise_returns_none(self):
        noise_only = np.random.default_rng(1).normal(100.0, 5.0, SHAPE)
        assert find_axes(noise_only) is None

    def test_isolated_hot_pixels_are_ignored(self):
        clean = find_axes(_frame(seed=2))
        img = _frame(seed=2)
        rng = np.random.default_rng(3)
        for _ in range(40):
            img[rng.integers(0, 60), rng.integers(0, SHAPE[1])] = 60000.0
        dirty = find_axes(img)
        assert dirty.cy == pytest.approx(clean.cy, abs=0.05)
        assert dirty.angle_deg == pytest.approx(clean.angle_deg, abs=0.05)

    def test_emission_line_spectrum_keeps_every_segment(self):
        segments = [(40, 60), (150, 175), (300, 330)]
        axes = find_axes(_frame(x_ranges=segments))
        assert axes is not None
        for x0, x1 in segments:
            assert axes.mask[:, x0:x1].any()
        assert axes.angle_deg == pytest.approx(0.0, abs=0.3)

    def test_centroid_is_weighted_by_intensity(self):
        # Same footprint, but the right half is 4x brighter: the weighted
        # centroid must sit clearly right of the geometric centre (x=200).
        axes = find_axes(_frame(amplitude=200.0, right_amplitude=800.0, seed=4))
        assert axes.cx > 230.0

    def test_invert_finds_a_dark_feature_on_a_bright_background(self):
        dark_slit = 1000.0 - _frame(row=80.0, background=0.0, noise=0.0)
        dark_slit += np.random.default_rng(5).normal(0.0, 5.0, SHAPE)
        axes = find_axes(dark_slit, invert=True)
        assert axes is not None
        assert axes.cy == pytest.approx(80.0, abs=0.3)
        assert axes.signal.max() > 400.0  # sign flipped: the feature is positive


class TestRotateToHorizontal:

    def test_total_energy_is_conserved_exactly(self):
        img = np.random.default_rng(6).uniform(0.0, 100.0, SHAPE)
        rotated = rotate_to_horizontal(img, 2.5)
        assert rotated.sum() == pytest.approx(img.sum(), rel=1e-12)

    def test_rotation_makes_the_trace_horizontal(self):
        img = _frame(angle_deg=3.0, seed=7)
        axes = find_axes(img)
        cleaned = np.where(axes.mask, axes.signal, 0.0)
        rotated = rotate_to_horizontal(cleaned, axes.angle_deg)
        # Rebuild a frame around the rotated trace and measure it again.
        after = find_axes(rotated + 100.0 + np.random.default_rng(8).normal(0.0, 5.0, SHAPE))
        assert abs(after.angle_deg) < 0.3

    def test_all_zero_image_is_returned_without_error(self):
        assert rotate_to_horizontal(np.zeros(SHAPE), 1.0).sum() == 0.0
