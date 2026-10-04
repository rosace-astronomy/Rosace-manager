"""
Tests for the DARK_ACQUISITION sub-plugin (DarkAcquisitionPlugin), indi
backend. Covers the three origins (current_session/library/camera) and the
edge cases around a library series that doesn't qualify (not enough files,
exposure time too short).
Run: pytest plugins/sequencer_plugins/dark_acquisition/test_dark_acquisition.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.sequencer_plugins import acquisition_helpers
from rosace.plugins.sequencer_plugins.dark_acquisition import dark_acquisition as da_mod
from rosace.plugins.sequencer_plugins.dark_acquisition.dark_acquisition import DarkAcquisitionPlugin

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
def small_dark_count(monkeypatch):
    """Real config.yaml default is 5 -- 2 keeps test fixtures small."""
    monkeypatch.setattr(da_mod, "DARK_COUNT", 2)


@pytest.fixture(autouse=True)
def fixed_max_exposure_time(monkeypatch):
    """Pin this rather than trust conf/config.yaml's current value -- these
    tests must not depend on whatever max_exposure_time_seconds a real
    deployment happens to have configured (e.g. lowered for manual testing)."""
    monkeypatch.setattr(da_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)


@pytest.fixture(autouse=True)
def library_under_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(da_mod, "DARK_LIBRARY_DIR", tmp_path / "dark_library")


def _write_dark_fits(directory, *, temp_c=TARGET_TEMP, exptime=300, naxis=FRAME_SIZE, index=1):
    directory.mkdir(parents=True, exist_ok=True)
    width, height = naxis
    data = np.zeros((height, width), dtype=np.uint16)
    hdu = fits.PrimaryHDU(data)
    hdu.header["EXPTIME"] = exptime
    filename = f"DARK_{round(temp_c)}deg_{exptime}s_20260815_120000-{index}.fits"
    hdu.writeto(directory / filename, overwrite=True)
    return filename


def _make_obs(obs_dir, *, camera=None, spectroscope=None, stop_check=None, target_temp=TARGET_TEMP):
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir), camera_target_temperature_deg_c=target_temp,
        stop_check=stop_check,
    )
    camera = camera if camera is not None else AsyncMock()
    camera.get_frame_size.return_value = FRAME_SIZE
    obs.hardware["science_camera"] = camera
    obs.hardware["spectroscope"] = spectroscope if spectroscope is not None else AsyncMock()
    return obs


class TestDarkAcquisitionPlugin:

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None)
        obs.obs_dir = None
        with pytest.raises(RuntimeError, match="obs_dir"):
            await DarkAcquisitionPlugin().execute(obs)

    async def test_raises_if_camera_target_temperature_missing(self, tmp_path):
        obs = _make_obs(tmp_path, target_temp=None)
        with pytest.raises(RuntimeError, match="camera_target_temperature_deg_c"):
            await DarkAcquisitionPlugin().execute(obs)

    async def test_current_session_origin_when_session_dir_already_has_a_usable_series(self, tmp_path):
        _write_dark_fits(tmp_path, index=1)
        _write_dark_fits(tmp_path, index=2)
        camera = AsyncMock()
        obs = _make_obs(tmp_path, camera=camera)

        result = await DarkAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["dark_acquisition"]["origin"] == "current_session"
        assert len(result.sequencer_data["dark_acquisition"]["dark_files"]) == 2
        camera.expose.assert_not_awaited()

    async def test_creates_the_library_bucket_directories(self, tmp_path):
        # library_under_tmp points DARK_LIBRARY_DIR at tmp_path / "dark_library".
        _write_dark_fits(tmp_path, index=1)
        _write_dark_fits(tmp_path, index=2)

        await DarkAcquisitionPlugin().execute(_make_obs(tmp_path))

        assert {p.name for p in (tmp_path / "dark_library").iterdir()} == {
            "TempMinus20degrees", "TempMinus15degrees", "TempMinus10degrees",
            "TempMinus5degrees", "Temp0degrees",
        }

    async def test_library_origin_when_session_dir_empty_but_library_has_a_usable_series(self, tmp_path, monkeypatch):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        library_bucket = tmp_path / "dark_library" / "TempMinus5degrees"
        _write_dark_fits(library_bucket, index=1)
        _write_dark_fits(library_bucket, index=2)
        camera = AsyncMock()
        obs = _make_obs(session_dir, camera=camera)

        result = await DarkAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["dark_acquisition"]["origin"] == "library"
        files = result.sequencer_data["dark_acquisition"]["dark_files"]
        assert len(files) == 2
        for name in files:
            assert (session_dir / name).exists()  # copied into the session dir
            assert (library_bucket / name).exists()  # library itself untouched
        camera.expose.assert_not_awaited()

    async def test_camera_origin_when_neither_session_nor_library_has_a_usable_series(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        camera = AsyncMock()
        fake_image = MagicMock()
        camera.expose.return_value = fake_image
        spectroscope = AsyncMock()
        obs = _make_obs(session_dir, camera=camera, spectroscope=spectroscope)

        result = await DarkAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["dark_acquisition"]["origin"] == "camera"
        assert len(result.sequencer_data["dark_acquisition"]["dark_files"]) == 2
        assert camera.expose.await_count == 2
        # 2 per capture: the real file plus the GUI preview copy (image_to_gui=True).
        assert fake_image.writeto.call_count == 4
        spectroscope.set_light_source.assert_awaited_once()

    async def test_camera_origin_when_library_series_is_too_short_a_count(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        library_bucket = tmp_path / "dark_library" / "TempMinus5degrees"
        _write_dark_fits(library_bucket, index=1)  # only 1, DARK_COUNT is 2
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(session_dir, camera=camera)

        result = await DarkAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["dark_acquisition"]["origin"] == "camera"

    async def test_camera_origin_when_library_exposure_time_too_short(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        library_bucket = tmp_path / "dark_library" / "TempMinus5degrees"
        _write_dark_fits(library_bucket, exptime=100, index=1)  # shorter than MAX_EXPOSURE_TIME_SECONDS
        _write_dark_fits(library_bucket, exptime=100, index=2)
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(session_dir, camera=camera)

        result = await DarkAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["dark_acquisition"]["origin"] == "camera"

    async def test_stop_check_interrupts_camera_capture_loop(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(session_dir, camera=camera, stop_check=lambda: True)

        result = await DarkAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["dark_acquisition"]["origin"] == "camera"
        assert len(result.sequencer_data["dark_acquisition"]["dark_files"]) == 1
        assert camera.expose.await_count == 1
