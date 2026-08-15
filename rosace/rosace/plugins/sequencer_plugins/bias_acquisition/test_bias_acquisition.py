"""
Tests for the BIAS_ACQUISITION sub-plugin (BiasAcquisitionPlugin), indi
backend. Covers the two origins (current_session/camera -- no library for
BIAS, unlike dark_acquisition).
Run: pytest plugins/sequencer_plugins/bias_acquisition/test_bias_acquisition.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.sequencer_plugins import acquisition_helpers
from rosace.plugins.sequencer_plugins.bias_acquisition import bias_acquisition as ba_mod
from rosace.plugins.sequencer_plugins.bias_acquisition.bias_acquisition import BiasAcquisitionPlugin

SENSOR_SIZE = (100, 80)
TARGET_TEMP = -5


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 15, 22, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def freeze_fits_timestamp(monkeypatch):
    monkeypatch.setattr(acquisition_helpers, "datetime", _FrozenDatetime)


@pytest.fixture(autouse=True)
def small_bias_count(monkeypatch):
    """Real config.yaml default is 5 -- 2 keeps test fixtures small."""
    monkeypatch.setattr(ba_mod, "BIAS_COUNT", 2)


def _write_bias_fits(directory, *, temp_c=TARGET_TEMP, exptime=0.0, naxis=SENSOR_SIZE, index=1):
    directory.mkdir(parents=True, exist_ok=True)
    width, height = naxis
    data = np.zeros((height, width), dtype=np.uint16)
    hdu = fits.PrimaryHDU(data)
    hdu.header["EXPTIME"] = exptime
    filename = f"BIAS_{round(temp_c)}deg_0_00s_2026081{index}_120000.fits"
    hdu.writeto(directory / filename, overwrite=True)
    return filename


def _make_obs(obs_dir, *, camera=None, spectroscope=None, stop_check=None, target_temp=TARGET_TEMP):
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir), camera_target_temperature_deg_c=target_temp,
        stop_check=stop_check,
    )
    camera = camera if camera is not None else AsyncMock()
    camera.get_sensor_size.return_value = SENSOR_SIZE
    obs.hardware["science_camera"] = camera
    obs.hardware["spectroscope"] = spectroscope if spectroscope is not None else AsyncMock()
    return obs


class TestBiasAcquisitionPlugin:

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None)
        obs.obs_dir = None
        with pytest.raises(RuntimeError, match="obs_dir"):
            await BiasAcquisitionPlugin().execute(obs)

    async def test_raises_if_camera_target_temperature_missing(self, tmp_path):
        obs = _make_obs(tmp_path, target_temp=None)
        with pytest.raises(RuntimeError, match="camera_target_temperature_deg_c"):
            await BiasAcquisitionPlugin().execute(obs)

    async def test_current_session_origin_when_session_dir_already_has_a_usable_series(self, tmp_path):
        _write_bias_fits(tmp_path, index=1)
        _write_bias_fits(tmp_path, index=2)
        camera = AsyncMock()
        obs = _make_obs(tmp_path, camera=camera)

        result = await BiasAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["bias_acquisition"]["origin"] == "current_session"
        assert len(result.sequencer_data["bias_acquisition"]["bias_files"]) == 2
        camera.expose.assert_not_awaited()

    async def test_camera_origin_when_session_dir_has_no_usable_series(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        camera = AsyncMock()
        fake_image = MagicMock()
        camera.expose.return_value = fake_image
        spectroscope = AsyncMock()
        obs = _make_obs(session_dir, camera=camera, spectroscope=spectroscope)

        result = await BiasAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["bias_acquisition"]["origin"] == "camera"
        assert len(result.sequencer_data["bias_acquisition"]["bias_files"]) == 2
        assert camera.expose.await_count == 2
        camera.expose.assert_awaited_with(0.0)
        assert fake_image.writeto.call_count == 2
        spectroscope.set_light_source.assert_awaited_once()

    async def test_stop_check_interrupts_camera_capture_loop(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(session_dir, camera=camera, stop_check=lambda: True)

        result = await BiasAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["bias_acquisition"]["origin"] == "camera"
        assert len(result.sequencer_data["bias_acquisition"]["bias_files"]) == 1
        assert camera.expose.await_count == 1
