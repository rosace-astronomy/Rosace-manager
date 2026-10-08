"""
Tests for the TILT sub-plugin (TiltPlugin), on synthetic frames.
The column shift itself is covered by test_reduction_helpers.py.
Run: pytest plugins/reduction_plugins/tilt/test_tilt.py -v
"""

import math
from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.axes_helpers import find_axes
from rosace.plugins.reduction_plugins.tilt.tilt import TiltPlugin

HEIGHT, WIDTH = 200, 400
ANGLE_DEG = 0.3
SUB = "Vega_0e37017f"


def _trace_frame(angle_deg=ANGLE_DEG, amplitude=1000.0, seed=0):
    """Noisy background plus a Gaussian trace through (x = WIDTH / 2, y = 100)."""
    rng = np.random.default_rng(seed)
    x = np.arange(WIDTH)
    y = np.arange(HEIGHT)[:, None]
    centre = 100.0 + (x - WIDTH / 2) * math.tan(math.radians(angle_deg))
    return amplitude * np.exp(-0.5 * ((y - centre) / 3.0) ** 2) + rng.normal(0, 5, (HEIGHT, WIDTH))


def _setup(tmp_path, target_frames, calib_frames, flat=None):
    (tmp_path / SUB).mkdir()
    fits.writeto(tmp_path / "FLAT_master.fits", (np.ones((HEIGHT, WIDTH)) if flat is None else flat).astype(np.float32))
    obs = Observation(id="0e37017f", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path))
    obs.scheduler_data["queried_name"] = "Vega"
    obs.reduction_data["master_flat_image"] = "FLAT_master.fits"
    for key, prefix, frames in (("preproc_target_images", "STAR", target_frames), ("preproc_calib_images", "CALIB", calib_frames)):
        names = []
        for i, data in enumerate(frames, start=1):
            name = f"{SUB}/{prefix}-{i}_preproc.fits"
            fits.writeto(tmp_path / name, data.astype(np.float32))
            names.append(name)
        obs.reduction_data[key] = names
    return obs


class TestTiltPlugin:

    async def test_measures_and_removes_the_tilt(self, tmp_path):
        obs = _setup(tmp_path, [_trace_frame(seed=s) for s in (1, 2, 3)], [_trace_frame(seed=4)])

        result = await TiltPlugin().execute(obs)

        casier = result.reduction_data["tilt"]
        assert casier["angle_deg"] == pytest.approx(ANGLE_DEG, abs=0.02)
        assert casier["center_x"] == WIDTH / 2
        assert casier["trace_y_at_center"] == pytest.approx(100.0, abs=0.2)
        assert result.reduction_data["tilt_target_images"] == [f"{SUB}/STAR-{i}_preproc_tilt.fits" for i in (1, 2, 3)]
        assert result.reduction_data["tilt_calib_images"] == [f"{SUB}/CALIB-1_preproc_tilt.fits"]
        for rel in result.reduction_data["tilt_target_images"] + result.reduction_data["tilt_calib_images"]:
            axes = find_axes(fits.getdata(tmp_path / rel).astype(float))
            assert axes.angle_deg == pytest.approx(0.0, abs=0.02)
            assert axes.cy == pytest.approx(100.0, abs=0.3)
            assert "COMMENT" not in fits.getheader(tmp_path / rel)
        assert set(casier) == {"angle_deg", "center_x", "trace_y_at_center", "trace_sigma_px"}
        assert casier["trace_sigma_px"] > 0

    async def test_column_totals_are_kept(self, tmp_path):
        frame = _trace_frame()
        obs = _setup(tmp_path, [frame], [frame])

        result = await TiltPlugin().execute(obs)

        corrected = fits.getdata(tmp_path / result.reduction_data["tilt_target_images"][0]).astype(float)
        stored = fits.getdata(tmp_path / f"{SUB}/STAR-1_preproc.fits").astype(float)
        # Away from the edges, where only noise can leave the frame
        assert corrected[20:180].sum(axis=0) == pytest.approx(stored[20:180].sum(axis=0), rel=1e-3, abs=50)

    async def test_dead_flat_column_is_ignored_for_the_measurement(self, tmp_path):
        frames = [_trace_frame(seed=s) for s in (1, 2)]
        for f in frames:
            f[:, 0] = 1e10
        flat = np.ones((HEIGHT, WIDTH))
        flat[:, 0] = 0.002
        obs = _setup(tmp_path, frames, [_trace_frame(seed=3)], flat=flat)

        result = await TiltPlugin().execute(obs)

        assert result.reduction_data["tilt"]["angle_deg"] == pytest.approx(ANGLE_DEG, abs=0.02)

    async def test_no_trace_is_an_error(self, tmp_path):
        rng = np.random.default_rng(0)
        obs = _setup(tmp_path, [rng.normal(0, 5, (HEIGHT, WIDTH))], [rng.normal(0, 5, (HEIGHT, WIDTH))])
        with pytest.raises(RuntimeError, match="no spectrum trace"):
            await TiltPlugin().execute(obs)

    async def test_missing_lamp_frames_is_an_error(self, tmp_path):
        obs = _setup(tmp_path, [_trace_frame()], [])
        del obs.reduction_data["preproc_calib_images"]
        with pytest.raises(RuntimeError, match="preproc_calib_images"):
            await TiltPlugin().execute(obs)
