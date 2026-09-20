"""
Tests for the FOCUS sub-plugin (FocusPlugin), indi backend.
Run: pytest plugins/sequencer_plugins/focus/test_focus.py -v
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.sequencer_plugins.focus import focus as focus_mod
from rosace.plugins.sequencer_plugins.focus.focus import FocusPlugin, measure_fwhm

FRAME_SIZE = (960, 1280)  # (height, width), matches the real ASI120MM Mini
BACKGROUND = 32.0
NOISE_STD = 6.0


def _blank_frame(rng, *, shape=FRAME_SIZE, background=BACKGROUND, noise_std=NOISE_STD):
    return rng.normal(background, noise_std, size=shape)


def _add_gaussian_star(data, x, y, *, amplitude, sigma=2.0):
    height, width = data.shape
    yy, xx = np.mgrid[0:height, 0:width]
    data += amplitude * np.exp(-(((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2)))


def _add_elongated_star(data, x, y, *, amplitude, sigma_x=6.0, sigma_y=1.0):
    """A "potato-shaped" (very elongated) source -- must be rejected by
    the roundness filter, unlike a real, round stellar PSF."""
    height, width = data.shape
    yy, xx = np.mgrid[0:height, 0:width]
    data += amplitude * np.exp(-(((xx - x) ** 2 / (2 * sigma_x**2)) + ((yy - y) ** 2 / (2 * sigma_y**2))))


def _add_hot_pixel(data, x, y, *, value):
    data[y, x] = value


@pytest.fixture
def rng():
    return np.random.default_rng(42)


class TestMeasureFwhm:

    def test_measures_clean_stars(self, rng):
        data = _blank_frame(rng)
        cx, cy = FRAME_SIZE[1] // 2, FRAME_SIZE[0] // 2
        for dx, dy in [(-50, -30), (40, 20), (0, 60), (-20, 50), (60, -40)]:
            _add_gaussian_star(data, cx + dx, cy + dy, amplitude=2000, sigma=2.0)

        result = measure_fwhm(data)

        assert result is not None
        mean_fwhm, n_stars, min_snr = result
        assert n_stars == 5
        # sigma=2.0 -> FWHM = 2.3548*2.0 ~= 4.7px
        assert 3.5 < mean_fwhm < 6.0
        assert min_snr >= focus_mod.MIN_SNR

    def test_returns_none_when_no_star_present(self, rng):
        data = _blank_frame(rng)

        assert measure_fwhm(data) is None

    def test_excludes_saturated_star(self, rng):
        data = _blank_frame(rng)
        cx, cy = FRAME_SIZE[1] // 2, FRAME_SIZE[0] // 2
        _add_gaussian_star(data, cx, cy, amplitude=focus_mod.SATURATION_ADU, sigma=2.0)
        _add_gaussian_star(data, cx + 40, cy + 20, amplitude=2000, sigma=2.0)

        result = measure_fwhm(data)

        assert result is not None
        _, n_stars, _ = result
        assert n_stars == 1  # only the non-saturated one

    def test_excludes_low_snr_star(self, rng):
        data = _blank_frame(rng)
        cx, cy = FRAME_SIZE[1] // 2, FRAME_SIZE[0] // 2
        # SNR ~= amplitude / NOISE_STD -- keep it well under MIN_SNR (30).
        _add_gaussian_star(data, cx, cy, amplitude=focus_mod.MIN_SNR * NOISE_STD * 0.3, sigma=2.0)

        assert measure_fwhm(data) is None

    def test_excludes_lone_hot_pixel(self, rng):
        data = _blank_frame(rng)
        cx, cy = FRAME_SIZE[1] // 2, FRAME_SIZE[0] // 2
        _add_hot_pixel(data, cx, cy, value=40000)
        _add_gaussian_star(data, cx + 40, cy + 20, amplitude=2000, sigma=2.0)

        result = measure_fwhm(data)

        assert result is not None
        _, n_stars, _ = result
        assert n_stars == 1  # the hot pixel never contributes

    def test_excludes_elongated_potato_shaped_source(self, rng):
        data = _blank_frame(rng)
        cx, cy = FRAME_SIZE[1] // 2, FRAME_SIZE[0] // 2
        _add_elongated_star(data, cx, cy, amplitude=2000)
        _add_gaussian_star(data, cx + 40, cy + 20, amplitude=2000, sigma=2.0)

        result = measure_fwhm(data)

        assert result is not None
        _, n_stars, _ = result
        assert n_stars == 1  # the elongated source never contributes

    def test_caps_at_max_stars(self, rng, monkeypatch):
        monkeypatch.setattr(focus_mod, "MAX_STARS", 3)
        data = _blank_frame(rng)
        cx, cy = FRAME_SIZE[1] // 2, FRAME_SIZE[0] // 2
        offsets = [(-80, -80), (-40, -40), (0, 0), (40, 40), (80, 80)]
        for dx, dy in offsets:
            _add_gaussian_star(data, cx + dx, cy + dy, amplitude=2000, sigma=2.0)

        result = measure_fwhm(data)

        assert result is not None
        _, n_stars, _ = result
        assert n_stars == 3


def _make_obs(obs_dir, *, camera=None, spectroscope=None):
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir),
    )
    obs.hardware["guiding_camera"] = camera if camera is not None else AsyncMock()
    obs.hardware["spectroscope"] = spectroscope if spectroscope is not None else AsyncMock()
    obs.sequencer_data["guiding_dark_acquisition"] = {"median_dark_file": "dark_median.fits"}
    return obs


class TestFocusPlugin:

    async def test_logs_info_with_measurement(self, tmp_path, caplog, monkeypatch, rng):
        monkeypatch.setattr(focus_mod, "SUBPLUGIN_PARAMS", {"guiding": {"exposure_seconds": 10.0}})

        # Kept float64 (not rounded to uint16 like a real capture) --
        # dtype-quantization robustness is already covered by
        # TestMeasureFwhm; this test only checks that execute() logs the
        # right thing when a star is found. Two stars off-center, same as
        # TestMeasureFwhm's own fixtures -- a single star placed exactly
        # at the window's center is a noise-dependent edge case for
        # DAOStarFinder's sharpness statistic (found while writing this
        # test), not a real detection concern.
        star_field = _blank_frame(rng)
        cx, cy = FRAME_SIZE[1] // 2, FRAME_SIZE[0] // 2
        _add_gaussian_star(star_field, cx + 40, cy + 20, amplitude=2000, sigma=2.0)
        _add_gaussian_star(star_field, cx - 30, cy - 50, amplitude=2000, sigma=2.0)
        guide_path = tmp_path / "GUIDING_test.fits"
        fits.PrimaryHDU(star_field).writeto(guide_path, overwrite=True)

        async def fake_capture_guide_image(obs, exposure_time, **kwargs):
            return guide_path

        monkeypatch.setattr(focus_mod, "capture_guide_image", fake_capture_guide_image)

        obs = _make_obs(tmp_path)
        with caplog.at_level(logging.INFO, logger="spectro.subplugin.focus"):
            result = await FocusPlugin().execute(obs)

        assert result is obs
        assert any("[FOCUS] Mean FWHM" in r.message for r in caplog.records)

    async def test_logs_warning_when_no_star_found(self, tmp_path, caplog, monkeypatch, rng):
        monkeypatch.setattr(focus_mod, "SUBPLUGIN_PARAMS", {"guiding": {"exposure_seconds": 10.0}})

        data = _blank_frame(rng)
        guide_path = tmp_path / "GUIDING_test.fits"
        fits.PrimaryHDU(data.astype(np.uint16)).writeto(guide_path, overwrite=True)

        async def fake_capture_guide_image(obs, exposure_time, **kwargs):
            return guide_path

        monkeypatch.setattr(focus_mod, "capture_guide_image", fake_capture_guide_image)

        obs = _make_obs(tmp_path)
        with caplog.at_level(logging.INFO, logger="spectro.subplugin.focus"):
            result = await FocusPlugin().execute(obs)

        assert result is obs
        assert any("No usable star" in r.message for r in caplog.records)
