"""
Tests for the SMILE sub-plugin (SmilePlugin), on synthetic frames.
The row shift itself is covered by test_reduction_helpers.py.
Run: pytest plugins/reduction_plugins/smile/test_smile.py -v
"""

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.smile import smile as smile_module
from rosace.plugins.reduction_plugins.smile.smile import SmilePlugin

HEIGHT, WIDTH = 200, 400
TRACE_ROW = 100
BAND = (40, 160)          # rows lit by the lamp
LINES_X = (60, 130, 210, 280, 350)
A, B = 5e-4, 0.02         # line shift(y) = A (y - TRACE_ROW)^2 + B (y - TRACE_ROW)
SUB = "Vega_0e37017f"


def _line_shift(y):
    return A * (y - TRACE_ROW) ** 2 + B * (y - TRACE_ROW)


def _lamp_frame(seed=0, dark_rows=None):
    rng = np.random.default_rng(seed)
    x = np.arange(WIDTH)
    frame = rng.normal(0, 5, (HEIGHT, WIDTH))
    for y in range(*BAND):
        if dark_rows and dark_rows[0] <= y < dark_rows[1]:
            continue
        for i, x0 in enumerate(LINES_X):
            frame[y] += (800 + 300 * i) * np.exp(-0.5 * ((x - x0 - _line_shift(y)) / 1.5) ** 2)
    return frame


def _target_frame(seed=0, with_trace=True):
    rng = np.random.default_rng(seed)
    y = np.arange(HEIGHT)[:, None]
    trace = 1000 * np.exp(-0.5 * ((y - TRACE_ROW) / 3.0) ** 2) * np.ones(WIDTH) if with_trace else 0
    return trace + rng.normal(0, 5, (HEIGHT, WIDTH))


def _setup(tmp_path, lamp_frames, target_frames):
    (tmp_path / SUB).mkdir()
    fits.writeto(tmp_path / "FLAT_master.fits", np.ones((HEIGHT, WIDTH), dtype=np.float32))
    obs = Observation(id="0e37017f", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path))
    obs.scheduler_data["queried_name"] = "Vega"
    obs.reduction_data["master_flat_image"] = "FLAT_master.fits"
    for key, prefix, frames in (("tilt_calib_images", "CALIB", lamp_frames), ("tilt_target_images", "STAR", target_frames)):
        names = []
        for i, data in enumerate(frames, start=1):
            name = f"{SUB}/{prefix}-{i}_preproc_tilt.fits"
            fits.writeto(tmp_path / name, data.astype(np.float32))
            names.append(name)
        obs.reduction_data[key] = names
    return obs


def _measured_shifts(tmp_path, files):
    lamp = sum(fits.getdata(tmp_path / name).astype(float) for name in files)
    rows = lamp - np.median(lamp, axis=1, keepdims=True)
    return smile_module._row_shifts(rows, TRACE_ROW)


class TestSmilePlugin:

    async def test_measures_and_removes_the_smile(self, tmp_path):
        obs = _setup(tmp_path, [_lamp_frame(s) for s in (1, 2, 3)], [_target_frame(s) for s in (4, 5)])

        result = await SmilePlugin().execute(obs)

        a, b, c = result.reduction_data["smile"]["coefficients"]
        assert set(result.reduction_data["smile"]) == {"coefficients"}
        assert a == pytest.approx(A, rel=0.05)
        assert b == pytest.approx(B - 2 * A * TRACE_ROW, abs=0.003)
        assert result.reduction_data["smile_calib_images"] == [f"{SUB}/CALIB-{i}_preproc_tilt_smile.fits" for i in (1, 2, 3)]
        assert result.reduction_data["smile_target_images"] == [f"{SUB}/STAR-{i}_preproc_tilt_smile.fits" for i in (1, 2)]
        before = _measured_shifts(tmp_path, result.reduction_data["tilt_calib_images"])
        after = _measured_shifts(tmp_path, result.reduction_data["smile_calib_images"])
        rows = np.arange(50, 151)
        assert np.abs(before[rows]).max() > 1.5
        assert np.abs(after[rows]).max() < 0.1
        assert "COMMENT" not in fits.getheader(tmp_path / result.reduction_data["smile_calib_images"][0])

    async def test_trace_row_and_row_totals_are_unchanged(self, tmp_path):
        obs = _setup(tmp_path, [_lamp_frame(1)], [_target_frame(4)])

        result = await SmilePlugin().execute(obs)

        before = fits.getdata(tmp_path / result.reduction_data["tilt_calib_images"][0]).astype(float)
        after = fits.getdata(tmp_path / result.reduction_data["smile_calib_images"][0]).astype(float)
        assert after[TRACE_ROW] == pytest.approx(before[TRACE_ROW], abs=1e-3)
        # Away from the left/right edges, where only noise can leave the frame
        inner = slice(40, 160)
        assert after[inner, 20:-20].sum(axis=1) == pytest.approx(before[inner, 20:-20].sum(axis=1), abs=200)

    async def test_dark_band_from_dust_does_not_prevent_the_measurement(self, tmp_path):
        obs = _setup(tmp_path, [_lamp_frame(s, dark_rows=(70, 76)) for s in (1, 2)], [_target_frame(4)])

        result = await SmilePlugin().execute(obs)

        assert result.reduction_data["smile"]["coefficients"][0] == pytest.approx(A, rel=0.05)

    async def test_no_trace_is_an_error(self, tmp_path):
        obs = _setup(tmp_path, [_lamp_frame(1)], [_target_frame(4, with_trace=False)])
        with pytest.raises(RuntimeError, match="no spectrum trace"):
            await SmilePlugin().execute(obs)

    async def test_missing_lamp_frames_is_an_error(self, tmp_path):
        obs = _setup(tmp_path, [_lamp_frame(1)], [_target_frame(4)])
        del obs.reduction_data["tilt_calib_images"]
        with pytest.raises(RuntimeError, match="tilt_calib_images"):
            await SmilePlugin().execute(obs)


class TestFitParabola:

    def test_outlier_row_is_rejected(self):
        y = np.arange(50, 150, dtype=float)
        # Measurement noise like on the real lamp (~0.02 px)
        shift = _line_shift(y) + np.random.default_rng(0).normal(0, 0.02, len(y))
        shift[30] += 5.0
        coefficients, kept = smile_module._fit_parabola(y, shift)
        assert not kept[30]
        assert kept.sum() >= len(y) - 3
        assert coefficients[0] == pytest.approx(A, rel=0.05)
