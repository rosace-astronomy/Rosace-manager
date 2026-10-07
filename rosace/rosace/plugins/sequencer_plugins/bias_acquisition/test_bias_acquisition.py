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

FRAME_SIZE = (100, 80)
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


@pytest.fixture(autouse=True)
def fixed_bias_exposure_time(monkeypatch):
    """Pin this rather than trust conf/config.yaml's current value -- these
    tests must not depend on whatever bias_exposure_time_seconds a real
    deployment happens to have configured (e.g. raised for a camera that
    refuses a literal 0s exposure)."""
    monkeypatch.setattr(ba_mod, "BIAS_EXPOSURE_TIME_SECONDS", 0.0)


def _write_bias_fits(directory, *, temp_c=TARGET_TEMP, exptime=0.0, naxis=FRAME_SIZE, index=1):
    directory.mkdir(parents=True, exist_ok=True)
    width, height = naxis
    data = np.zeros((height, width), dtype=np.uint16)
    hdu = fits.PrimaryHDU(data)
    hdu.header["EXPTIME"] = exptime
    filename = f"BIAS_{round(temp_c)}deg_0_00s_20260815_120000-{index}.fits"
    hdu.writeto(directory / filename, overwrite=True)
    return filename


def _make_obs(obs_dir, *, camera=None, spectroscope=None, stop_check=None, target_temp=TARGET_TEMP):
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir),
        session_data={} if target_temp is None else {"camera_target_temperature_deg_c": target_temp},
        stop_check=stop_check,
    )
    camera = camera if camera is not None else AsyncMock()
    camera.get_frame_size.return_value = FRAME_SIZE
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
        assert len(result.sequencer_data["bias_images"]) == 2
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
        assert len(result.sequencer_data["bias_images"]) == 2
        assert camera.expose.await_count == 2
        camera.expose.assert_awaited_with(0.0)
        # 2 per capture: the real file plus the GUI preview copy (image_to_gui=True).
        assert fake_image.writeto.call_count == 4
        spectroscope.set_light_source.assert_awaited_once()

    async def test_stop_check_interrupts_camera_capture_loop(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(session_dir, camera=camera, stop_check=lambda: True)

        result = await BiasAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["bias_acquisition"]["origin"] == "camera"
        assert len(result.sequencer_data["bias_images"]) == 1
        assert camera.expose.await_count == 1
