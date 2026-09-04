"""
Tests for the FLAT_ACQUISITION sub-plugin (FlatAcquisitionPlugin), indi
backend. Covers _find_usable_flat_series() directly (no staleness check at
all -- neither temperature nor age, unlike dark/bias/calib) and the two
origins (current_session/camera -- no library).
Run: pytest plugins/sequencer_plugins/flat_acquisition/test_flat_acquisition.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.sequencer_plugins import acquisition_helpers
from rosace.plugins.sequencer_plugins.flat_acquisition import flat_acquisition as fa_mod
from rosace.plugins.sequencer_plugins.flat_acquisition.flat_acquisition import (
    FlatAcquisitionPlugin,
    _find_usable_flat_series,
)

FRAME_SIZE = (100, 80)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 27, 22, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def freeze_fits_timestamp(monkeypatch):
    monkeypatch.setattr(acquisition_helpers, "datetime", _FrozenDatetime)


@pytest.fixture(autouse=True)
def small_flat_count(monkeypatch):
    """Real config.yaml default is 5 -- 2 keeps test fixtures small."""
    monkeypatch.setattr(fa_mod, "FLAT_COUNT", 2)


@pytest.fixture(autouse=True)
def fixed_flat_exposure_time(monkeypatch):
    """Pin this rather than trust conf/config.yaml's current value."""
    monkeypatch.setattr(fa_mod, "FLAT_EXPOSURE_TIME_SECONDS", 3.0)


def _write_flat_fits(directory, *, exptime=3.0, naxis=FRAME_SIZE, index=1):
    directory.mkdir(parents=True, exist_ok=True)
    width, height = naxis
    data = np.zeros((height, width), dtype=np.uint16)
    hdu = fits.PrimaryHDU(data)
    hdu.header["EXPTIME"] = exptime
    filename = f"FLAT_2x3s_2026081{index}_120000.fits"
    hdu.writeto(directory / filename, overwrite=True)
    return filename


class TestFindUsableFlatSeries:

    def test_returns_none_if_directory_missing(self, tmp_path):
        result = _find_usable_flat_series(
            tmp_path / "nope", count_required=2, exposure_time_required=3.0, frame_size=FRAME_SIZE,
        )
        assert result is None

    def test_returns_none_if_not_enough_files(self, tmp_path):
        _write_flat_fits(tmp_path, index=1)
        result = _find_usable_flat_series(
            tmp_path, count_required=2, exposure_time_required=3.0, frame_size=FRAME_SIZE,
        )
        assert result is None

    def test_accepts_an_existing_series(self, tmp_path):
        _write_flat_fits(tmp_path, index=1)
        _write_flat_fits(tmp_path, index=2)
        result = _find_usable_flat_series(
            tmp_path, count_required=2, exposure_time_required=3.0, frame_size=FRAME_SIZE,
        )
        assert result is not None
        assert len(result) == 2

    def test_returns_none_if_frame_size_does_not_match(self, tmp_path):
        _write_flat_fits(tmp_path, index=1, naxis=(50, 40))
        _write_flat_fits(tmp_path, index=2, naxis=(50, 40))
        result = _find_usable_flat_series(
            tmp_path, count_required=2, exposure_time_required=3.0, frame_size=FRAME_SIZE,
        )
        assert result is None

    def test_returns_none_if_exposure_time_too_short(self, tmp_path):
        _write_flat_fits(tmp_path, index=1, exptime=1.0)
        _write_flat_fits(tmp_path, index=2, exptime=1.0)
        result = _find_usable_flat_series(
            tmp_path, count_required=2, exposure_time_required=3.0, frame_size=FRAME_SIZE,
        )
        assert result is None

    def test_accepts_a_longer_exposure_time_than_required(self, tmp_path):
        _write_flat_fits(tmp_path, index=1, exptime=5.0)
        _write_flat_fits(tmp_path, index=2, exptime=5.0)
        result = _find_usable_flat_series(
            tmp_path, count_required=2, exposure_time_required=3.0, frame_size=FRAME_SIZE,
        )
        assert result is not None


def _make_obs(obs_dir, *, camera=None, spectroscope=None, stop_check=None):
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir), stop_check=stop_check,
    )
    camera = camera if camera is not None else AsyncMock()
    camera.get_frame_size.return_value = FRAME_SIZE
    obs.hardware["science_camera"] = camera
    obs.hardware["spectroscope"] = spectroscope if spectroscope is not None else AsyncMock()
    return obs


class TestFlatAcquisitionPlugin:

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None)
        obs.obs_dir = None
        with pytest.raises(RuntimeError, match="obs_dir"):
            await FlatAcquisitionPlugin().execute(obs)

    async def test_current_session_origin_when_session_dir_already_has_a_usable_series(self, tmp_path):
        _write_flat_fits(tmp_path, index=1)
        _write_flat_fits(tmp_path, index=2)
        camera = AsyncMock()
        obs = _make_obs(tmp_path, camera=camera)

        result = await FlatAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["flat_acquisition"]["origin"] == "current_session"
        assert len(result.sequencer_data["flat_acquisition"]["flat_files"]) == 2
        camera.expose.assert_not_awaited()

    async def test_camera_origin_when_session_dir_has_no_usable_series(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        camera = AsyncMock()
        fake_image = MagicMock()
        camera.expose.return_value = fake_image
        spectroscope = AsyncMock()
        obs = _make_obs(session_dir, camera=camera, spectroscope=spectroscope)

        result = await FlatAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["flat_acquisition"]["origin"] == "camera"
        assert len(result.sequencer_data["flat_acquisition"]["flat_files"]) == 2
        assert camera.expose.await_count == 2
        # 2 per capture: the real file plus the GUI preview copy (image_to_gui=True).
        assert fake_image.writeto.call_count == 4
        spectroscope.set_light_source.assert_awaited_once()

    async def test_camera_origin_when_existing_series_too_short_a_count(self, tmp_path):
        session_dir = tmp_path / "session"
        _write_flat_fits(session_dir, index=1)  # only 1, FLAT_COUNT is 2
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(session_dir, camera=camera)

        result = await FlatAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["flat_acquisition"]["origin"] == "camera"

    async def test_stop_check_interrupts_camera_capture_loop(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(session_dir, camera=camera, stop_check=lambda: True)

        result = await FlatAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["flat_acquisition"]["origin"] == "camera"
        assert len(result.sequencer_data["flat_acquisition"]["flat_files"]) == 1
        assert camera.expose.await_count == 1
