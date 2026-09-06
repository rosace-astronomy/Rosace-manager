"""
Tests for the GUIDING_DARK_ACQUISITION sub-plugin (GuidingDarkAcquisitionPlugin),
indi backend. Covers _find_usable_guiding_dark_median() (no staleness check
at all, like flat_acquisition -- the guiding camera is uncooled), the two
origins (current_session/camera -- no library), and the median-combine math.
Run: pytest plugins/sequencer_plugins/guiding_dark_acquisition/test_guiding_dark_acquisition.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.sequencer_plugins import acquisition_helpers
from rosace.plugins.sequencer_plugins.guiding_dark_acquisition import (
    guiding_dark_acquisition as gda_mod,
)
from rosace.plugins.sequencer_plugins.guiding_dark_acquisition.guiding_dark_acquisition import (
    GuidingDarkAcquisitionPlugin,
    _find_usable_guiding_dark_median,
    _median_combine,
)

FRAME_SIZE = (100, 80)
EXPOSURE_SECONDS = 10.0


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 6, 22, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def freeze_fits_timestamp(monkeypatch):
    monkeypatch.setattr(acquisition_helpers, "datetime", _FrozenDatetime)


@pytest.fixture(autouse=True)
def small_guiding_dark_count(monkeypatch):
    """Real config default is 7 -- 2 keeps test fixtures small."""
    monkeypatch.setattr(gda_mod, "GUIDING_DARK_COUNT", 2)


def _write_raw_frame(directory, *, exptime=EXPOSURE_SECONDS, naxis=FRAME_SIZE, value=100, index=1):
    directory.mkdir(parents=True, exist_ok=True)
    width, height = naxis
    data = np.full((height, width), value, dtype=np.uint16)
    hdu = fits.PrimaryHDU(data)
    hdu.header["EXPTIME"] = exptime
    filename = f"GUIDINGDARK_2x10s_2026090{index}_220000-{index}.fits"
    hdu.writeto(directory / filename, overwrite=True)
    return filename


def _write_median_fits(directory, *, exptime=EXPOSURE_SECONDS, naxis=FRAME_SIZE):
    directory.mkdir(parents=True, exist_ok=True)
    width, height = naxis
    data = np.full((height, width), 42, dtype=np.uint16)
    hdu = fits.PrimaryHDU(data)
    hdu.header["EXPTIME"] = exptime
    filename = "GUIDINGDARK_2x10s_20260901_220000_median.fits"
    hdu.writeto(directory / filename, overwrite=True)
    return filename


class TestFindUsableGuidingDarkMedian:

    def test_returns_none_if_directory_missing(self, tmp_path):
        result = _find_usable_guiding_dark_median(
            tmp_path / "nope", exposure_time_required=EXPOSURE_SECONDS, frame_size=FRAME_SIZE,
        )
        assert result is None

    def test_returns_none_if_no_median_file(self, tmp_path):
        _write_raw_frame(tmp_path, index=1)
        result = _find_usable_guiding_dark_median(
            tmp_path, exposure_time_required=EXPOSURE_SECONDS, frame_size=FRAME_SIZE,
        )
        assert result is None

    def test_accepts_an_existing_median(self, tmp_path):
        filename = _write_median_fits(tmp_path)
        result = _find_usable_guiding_dark_median(
            tmp_path, exposure_time_required=EXPOSURE_SECONDS, frame_size=FRAME_SIZE,
        )
        assert result == filename

    def test_returns_none_if_frame_size_does_not_match(self, tmp_path):
        _write_median_fits(tmp_path, naxis=(50, 40))
        result = _find_usable_guiding_dark_median(
            tmp_path, exposure_time_required=EXPOSURE_SECONDS, frame_size=FRAME_SIZE,
        )
        assert result is None

    def test_returns_none_if_exposure_time_too_short(self, tmp_path):
        _write_median_fits(tmp_path, exptime=1.0)
        result = _find_usable_guiding_dark_median(
            tmp_path, exposure_time_required=EXPOSURE_SECONDS, frame_size=FRAME_SIZE,
        )
        assert result is None

    def test_accepts_a_longer_exposure_time_than_required(self, tmp_path):
        _write_median_fits(tmp_path, exptime=20.0)
        result = _find_usable_guiding_dark_median(
            tmp_path, exposure_time_required=EXPOSURE_SECONDS, frame_size=FRAME_SIZE,
        )
        assert result is not None


class TestMedianCombine:

    def test_computes_per_pixel_median_and_preserves_dtype(self, tmp_path):
        width, height = FRAME_SIZE
        data1 = np.full((height, width), 10, dtype=np.uint16)
        data2 = np.full((height, width), 30, dtype=np.uint16)
        data3 = np.full((height, width), 20, dtype=np.uint16)
        names = []
        for i, data in enumerate([data1, data2, data3], start=1):
            hdu = fits.PrimaryHDU(data)
            hdu.header["EXPTIME"] = EXPOSURE_SECONDS
            name = f"frame-{i}.fits"
            hdu.writeto(tmp_path / name, overwrite=True)
            names.append(name)

        median_data, header = _median_combine(tmp_path, names)

        assert median_data.dtype == np.uint16
        assert (median_data == 20).all()
        assert header["EXPTIME"] == EXPOSURE_SECONDS


def _make_obs(obs_dir, *, camera=None, spectroscope=None, stop_check=None):
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir), stop_check=stop_check,
    )
    camera = camera if camera is not None else AsyncMock()
    camera.get_frame_size.return_value = FRAME_SIZE
    obs.hardware["guiding_camera"] = camera
    obs.hardware["spectroscope"] = spectroscope if spectroscope is not None else AsyncMock()
    obs.hardware_config["guiding_camera"] = {"exposure_seconds": EXPOSURE_SECONDS}
    return obs


class TestGuidingDarkAcquisitionPlugin:

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None)
        obs.obs_dir = None
        with pytest.raises(RuntimeError, match="obs_dir"):
            await GuidingDarkAcquisitionPlugin().execute(obs)

    async def test_current_session_origin_when_session_dir_already_has_a_usable_median(self, tmp_path):
        median_file = _write_median_fits(tmp_path)
        camera = AsyncMock()
        obs = _make_obs(tmp_path, camera=camera)

        result = await GuidingDarkAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["guiding_dark_acquisition"]["origin"] == "current_session"
        assert result.sequencer_data["guiding_dark_acquisition"]["median_dark_file"] == median_file
        assert result.sequencer_data["guiding_dark_acquisition"]["dark_files"] == []
        camera.expose.assert_not_awaited()

    async def test_camera_origin_when_session_dir_has_no_usable_median(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        camera = AsyncMock()
        width, height = FRAME_SIZE
        fake_image = MagicMock()
        fake_image.__getitem__.return_value.data = np.full((height, width), 100, dtype=np.uint16)
        camera.expose.return_value = fake_image

        def _writeto(path, overwrite=True):
            fits.PrimaryHDU(
                fake_image.__getitem__.return_value.data,
                header=fits.Header({"EXPTIME": EXPOSURE_SECONDS}),
            ).writeto(path, overwrite=overwrite)

        fake_image.writeto.side_effect = _writeto
        spectroscope = AsyncMock()
        obs = _make_obs(session_dir, camera=camera, spectroscope=spectroscope)

        result = await GuidingDarkAcquisitionPlugin().execute(obs)

        casier = result.sequencer_data["guiding_dark_acquisition"]
        assert casier["origin"] == "camera"
        assert len(casier["dark_files"]) == 2
        assert casier["median_dark_file"].endswith("_median.fits")
        assert (session_dir / casier["median_dark_file"]).exists()
        assert camera.expose.await_count == 2
        spectroscope.set_light_source.assert_awaited_once()

        median_data = fits.getdata(session_dir / casier["median_dark_file"])
        assert (median_data == 100).all()

    async def test_stop_check_interrupts_camera_capture_loop(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        camera = AsyncMock()
        width, height = FRAME_SIZE
        fake_image = MagicMock()
        fake_image.__getitem__.return_value.data = np.full((height, width), 50, dtype=np.uint16)

        def _writeto(path, overwrite=True):
            fits.PrimaryHDU(
                fake_image.__getitem__.return_value.data,
                header=fits.Header({"EXPTIME": EXPOSURE_SECONDS}),
            ).writeto(path, overwrite=overwrite)

        fake_image.writeto.side_effect = _writeto
        camera.expose.return_value = fake_image
        obs = _make_obs(session_dir, camera=camera, stop_check=lambda: True)

        result = await GuidingDarkAcquisitionPlugin().execute(obs)

        casier = result.sequencer_data["guiding_dark_acquisition"]
        assert casier["origin"] == "camera"
        assert len(casier["dark_files"]) == 1
        assert camera.expose.await_count == 1
        # A one-frame "median" is just that frame -- still written, still usable.
        assert (session_dir / casier["median_dark_file"]).exists()
