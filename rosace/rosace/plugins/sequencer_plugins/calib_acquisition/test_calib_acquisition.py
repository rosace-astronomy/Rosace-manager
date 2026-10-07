"""
Tests for the CALIB_ACQUISITION sub-plugin (CalibAcquisitionPlugin), indi
backend. Covers _find_usable_calib_series() directly (age-based staleness,
most-recent-batch preference, sensor/exposure checks -- same rigor as
test_dark_library_helpers.py's TestFindUsableFrameSeries) and the two
origins (current_session/camera -- no library, no temperature constraint,
unlike dark_acquisition).
Run: pytest plugins/sequencer_plugins/calib_acquisition/test_calib_acquisition.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.sequencer_plugins import acquisition_helpers
from rosace.plugins.sequencer_plugins.acquisition_helpers import build_fits_root_name
from rosace.plugins.sequencer_plugins.calib_acquisition import calib_acquisition as ca_mod
from rosace.plugins.sequencer_plugins.calib_acquisition.calib_acquisition import (
    CalibAcquisitionPlugin,
    _find_usable_calib_series,
)

FRAME_SIZE = (100, 80)
NOW = datetime(2026, 8, 27, 22, 0, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 27, 22, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def freeze_fits_timestamp(monkeypatch):
    monkeypatch.setattr(acquisition_helpers, "datetime", _FrozenDatetime)
    monkeypatch.setattr(ca_mod, "datetime", _FrozenDatetime)


@pytest.fixture(autouse=True)
def small_calib_count(monkeypatch):
    """Real config.yaml default is 3 -- 2 keeps test fixtures small."""
    monkeypatch.setattr(ca_mod, "CALIB_COUNT", 2)


@pytest.fixture(autouse=True)
def fixed_calib_exposure_time(monkeypatch):
    """Pin this rather than trust conf/config.yaml's current value."""
    monkeypatch.setattr(ca_mod, "CALIB_EXPOSURE_TIME_SECONDS", 3.0)


@pytest.fixture(autouse=True)
def fixed_max_age_hours(monkeypatch):
    """Pin this rather than trust conf/config.yaml's current value."""
    monkeypatch.setattr(ca_mod, "CALIB_MAX_AGE_HOURS", 24.0)


def _write_calib_fits(directory, *, timestamp, exptime=3.0, naxis=FRAME_SIZE, index=1, count=2):
    directory.mkdir(parents=True, exist_ok=True)
    root = build_fits_root_name("CALIB", exposure_time=exptime, num_exposures=count, timestamp=timestamp)
    suffix = f"-{index}" if count > 1 else ""
    filename = f"{root}{suffix}.fits"
    width, height = naxis
    data = np.zeros((height, width), dtype=np.uint16)
    hdu = fits.PrimaryHDU(data)
    hdu.header["EXPTIME"] = exptime
    hdu.writeto(directory / filename, overwrite=True)
    return filename


class TestFindUsableCalibSeries:

    def test_returns_none_if_directory_missing(self, tmp_path):
        result = _find_usable_calib_series(
            tmp_path / "nope", count_required=2, exposure_time_required=3.0,
            max_age_hours=24.0, frame_size=FRAME_SIZE, now=NOW,
        )
        assert result is None

    def test_returns_none_if_not_enough_files(self, tmp_path):
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=1)
        result = _find_usable_calib_series(
            tmp_path, count_required=2, exposure_time_required=3.0,
            max_age_hours=24.0, frame_size=FRAME_SIZE, now=NOW,
        )
        assert result is None

    def test_accepts_a_series_within_the_age_limit(self, tmp_path):
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=1)
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=2)
        result = _find_usable_calib_series(
            tmp_path, count_required=2, exposure_time_required=3.0,
            max_age_hours=24.0, frame_size=FRAME_SIZE, now=NOW,
        )
        assert result is not None
        assert len(result) == 2

    def test_rejects_a_series_beyond_the_age_limit(self, tmp_path):
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=25), index=1)
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=25), index=2)
        result = _find_usable_calib_series(
            tmp_path, count_required=2, exposure_time_required=3.0,
            max_age_hours=24.0, frame_size=FRAME_SIZE, now=NOW,
        )
        assert result is None

    def test_prefers_the_most_recent_batch_over_a_stale_one(self, tmp_path):
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=30), index=1)
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=30), index=2)
        fresh_1 = _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=1)
        fresh_2 = _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=2)

        result = _find_usable_calib_series(
            tmp_path, count_required=2, exposure_time_required=3.0,
            max_age_hours=24.0, frame_size=FRAME_SIZE, now=NOW,
        )

        assert sorted(result) == sorted([fresh_1, fresh_2])

    def test_returns_none_if_frame_size_does_not_match(self, tmp_path):
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=1, naxis=(50, 40))
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=2, naxis=(50, 40))
        result = _find_usable_calib_series(
            tmp_path, count_required=2, exposure_time_required=3.0,
            max_age_hours=24.0, frame_size=FRAME_SIZE, now=NOW,
        )
        assert result is None

    def test_returns_none_if_exposure_time_too_short(self, tmp_path):
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=1, exptime=1.0)
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=2, exptime=1.0)
        result = _find_usable_calib_series(
            tmp_path, count_required=2, exposure_time_required=3.0,
            max_age_hours=24.0, frame_size=FRAME_SIZE, now=NOW,
        )
        assert result is None

    def test_accepts_a_longer_exposure_time_than_required(self, tmp_path):
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=1, exptime=5.0)
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=2, exptime=5.0)
        result = _find_usable_calib_series(
            tmp_path, count_required=2, exposure_time_required=3.0,
            max_age_hours=24.0, frame_size=FRAME_SIZE, now=NOW,
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


class TestCalibAcquisitionPlugin:

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None)
        obs.obs_dir = None
        with pytest.raises(RuntimeError, match="obs_dir"):
            await CalibAcquisitionPlugin().execute(obs)

    async def test_current_session_origin_when_a_fresh_series_already_exists(self, tmp_path):
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=1)
        _write_calib_fits(tmp_path, timestamp=NOW - timedelta(hours=1), index=2)
        camera = AsyncMock()
        obs = _make_obs(tmp_path, camera=camera)

        result = await CalibAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["calib_acquisition"]["origin"] == "current_session"
        assert len(result.sequencer_data["calib_images"]) == 2
        camera.expose.assert_not_awaited()

    async def test_camera_origin_when_session_dir_has_no_series(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        camera = AsyncMock()
        fake_image = MagicMock()
        camera.expose.return_value = fake_image
        spectroscope = AsyncMock()
        obs = _make_obs(session_dir, camera=camera, spectroscope=spectroscope)

        result = await CalibAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["calib_acquisition"]["origin"] == "camera"
        assert len(result.sequencer_data["calib_images"]) == 2
        assert camera.expose.await_count == 2
        # 2 per capture: the real file plus the GUI preview copy (image_to_gui=True).
        assert fake_image.writeto.call_count == 4
        spectroscope.set_light_source.assert_awaited_once()

    async def test_camera_origin_when_existing_series_too_old(self, tmp_path):
        session_dir = tmp_path / "session"
        _write_calib_fits(session_dir, timestamp=NOW - timedelta(hours=25), index=1)
        _write_calib_fits(session_dir, timestamp=NOW - timedelta(hours=25), index=2)
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(session_dir, camera=camera)

        result = await CalibAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["calib_acquisition"]["origin"] == "camera"

    async def test_camera_origin_when_existing_series_too_short_a_count(self, tmp_path):
        session_dir = tmp_path / "session"
        _write_calib_fits(session_dir, timestamp=NOW - timedelta(hours=1), index=1)  # only 1, CALIB_COUNT is 2
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(session_dir, camera=camera)

        result = await CalibAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["calib_acquisition"]["origin"] == "camera"

    async def test_stop_check_interrupts_camera_capture_loop(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(session_dir, camera=camera, stop_check=lambda: True)

        result = await CalibAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["calib_acquisition"]["origin"] == "camera"
        assert len(result.sequencer_data["calib_images"]) == 1
        assert camera.expose.await_count == 1
