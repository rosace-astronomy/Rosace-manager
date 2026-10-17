"""
Tests for the shared acquisition helpers (acquisition_helpers.py).
Run: pytest plugins/sequencer_plugins/test_acquisition_helpers.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from astropy.io import fits

from rosace.errors import SessionFatalError
from rosace.hardware.errors import DeviceCommandError
from rosace.hardware.spectroscope import LightSource
from rosace.models import Observation
from rosace.plugins.sequencer_plugins import acquisition_helpers, guiding_chain_recovery
from rosace.plugins.sequencer_plugins.acquisition_helpers import (
    build_fits_root_name,
    capture_guide_image,
    capture_science_image,
    compute_exposure_time_and_count,
    frame_kind,
    publish_guide_preview,
    slit_position,
)

FIXED_TS = "20260807_143022"

_DEFAULT_FRAME = (4, 4)


def _make_obs(tmp_path, dark_value: int | None = 0) -> Observation:
    """dark_value=None registers no guiding-dark median at all -- for
    testing the "missing dark raises" error and subtract_dark=False, which
    needs none. Otherwise writes a small constant-valued median dark FITS
    and registers it in sequencer_data, so every other test's correction is
    a predictable, simple subtraction (0 by default -- a no-op, exercising
    the real arithmetic without needing each test to account for it)."""
    obs = Observation(
        id="obs1", created_at=datetime.now(timezone.utc), session_id="sess1", obs_dir=str(tmp_path),
    )
    obs.hardware["guiding_camera"] = AsyncMock()
    obs.hardware["spectroscope"] = AsyncMock()
    if dark_value is not None:
        dark_path = tmp_path / "GUIDINGDARK_median.fits"
        fits.PrimaryHDU(np.full(_DEFAULT_FRAME, dark_value, dtype=np.uint16)).writeto(dark_path, overwrite=True)
        obs.sequencer_data["guiding_dark_acquisition"] = {"median_dark_file": dark_path.name}
    return obs


def _fake_image(data: np.ndarray | None = None) -> MagicMock:
    """A MagicMock standing in for an astropy HDUList, with a *real* header
    and pixel array on [0] (capture_guide_image() reads and mutates
    image[0].data for dark subtraction, which a bare MagicMock can't
    support) and a writeto() that actually writes to disk -- so tests can
    check real file contents, not just call counts."""
    if data is None:
        data = np.full(_DEFAULT_FRAME, 100, dtype=np.uint16)
    hdu0 = MagicMock()
    hdu0.header = fits.Header()
    hdu0.data = data
    image = MagicMock()
    image.__getitem__.return_value = hdu0

    def _writeto(path, overwrite=True):
        fits.PrimaryHDU(hdu0.data.copy(), header=hdu0.header.copy()).writeto(path, overwrite=overwrite)

    image.writeto.side_effect = _writeto
    return image


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 7, 14, 30, 22, tzinfo=timezone.utc)


@pytest.fixture
def freeze_fits_timestamp(monkeypatch):
    """build_fits_root_name() stamps every FITS filename with the current
    UTC time — freeze it so tests can assert on exact filenames. Not
    autouse: test_defaults_timestamp_to_now_utc below specifically needs
    the real clock, not this frozen one."""
    monkeypatch.setattr(acquisition_helpers, "datetime", _FrozenDatetime)


class TestCaptureGuideImage:

    async def test_exposes_camera_for_given_duration(self, tmp_path):
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = _fake_image()

        await capture_guide_image(obs, 0.3)

        obs.hardware["guiding_camera"].expose.assert_awaited_once_with(0.3)

    async def test_switches_spectroscope_to_sky_before_exposing(self, tmp_path):
        """Unconditional -- see the function's own docstring for the
        2026-09-03 incident (a stuck DARK spectroscope position producing a
        black guide image) this guards against."""
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = _fake_image()

        await capture_guide_image(obs, 0.3)

        obs.hardware["spectroscope"].set_light_source.assert_awaited_once_with(LightSource.SKY)

    async def test_writes_raw_and_corrected_fits_and_returns_corrected_path(self, tmp_path, freeze_fits_timestamp):
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = _fake_image()

        result = await capture_guide_image(obs, 0.3)

        expected_raw = tmp_path / f"GUIDING_RAW_0_30s_{FIXED_TS}.fits"
        expected_corrected = tmp_path / f"GUIDING_0_30s_{FIXED_TS}.fits"
        assert expected_raw.exists()
        assert expected_corrected.exists()
        assert result == expected_corrected

    async def test_filename_includes_target_name_when_given(self, tmp_path, freeze_fits_timestamp):
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = _fake_image()

        result = await capture_guide_image(obs, 0.3, target_name="Vega")

        assert result == tmp_path / f"GUIDING_Vega_0_30s_{FIXED_TS}.fits"
        assert (tmp_path / f"GUIDING_RAW_Vega_0_30s_{FIXED_TS}.fits").exists()

    async def test_uses_custom_filename(self, tmp_path):
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = _fake_image()

        result = await capture_guide_image(obs, 0.3, filename="check.fits")

        assert result == tmp_path / "check.fits"
        # "check.fits" doesn't start with "GUIDING_" -- _raw_variant_filename()
        # falls back to a plain "RAW_" prefix (see its own docstring).
        assert (tmp_path / "RAW_check.fits").exists()

    async def test_stamps_ra_dec_equinox_when_given(self, tmp_path):
        fake_image = _fake_image()
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        await capture_guide_image(obs, 0.3, ra_deg=279.23, dec_deg=38.78)

        header = fake_image[0].header
        assert header["RA"] == 279.23
        assert header["DEC"] == 38.78
        assert header["EQUINOX"] == 2000
        # Every stamped card must credit Rosace, so a reader can tell it
        # wasn't left over from the camera driver's own snoop.
        assert "Rosace" in header.comments["RA"]
        assert "Rosace" in header.comments["DEC"]
        assert "Rosace" in header.comments["EQUINOX"]

    async def test_stamps_object_when_target_name_given(self, tmp_path):
        fake_image = _fake_image()
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        await capture_guide_image(obs, 0.3, target_name="Vega")

        header = fake_image[0].header
        assert header["OBJECT"] == "Vega"
        assert "Rosace" in header.comments["OBJECT"]

    async def test_no_equinox_without_ra_dec(self, tmp_path):
        fake_image = _fake_image()
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        await capture_guide_image(obs, 0.3, target_name="Vega")

        assert "EQUINOX" not in fake_image[0].header

    async def test_no_header_stamps_by_default(self, tmp_path):
        fake_image = _fake_image()
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        await capture_guide_image(obs, 0.3)

        assert len(fake_image[0].header) == 0

    async def test_rejects_ra_deg_without_dec_deg(self, tmp_path):
        obs = _make_obs(tmp_path)
        with pytest.raises(ValueError, match="ra_deg and dec_deg"):
            await capture_guide_image(obs, 0.3, ra_deg=279.23)

    async def test_image_to_gui_false_by_default_no_extra_write_no_broadcast(self, tmp_path, monkeypatch):
        gui_dir = tmp_path / "gui_preview"
        monkeypatch.setattr(acquisition_helpers, "GUI_PREVIEW_DIR", gui_dir)
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(acquisition_helpers.ws_image_broadcaster, "broadcast", broadcast_mock)
        fake_image = _fake_image()
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        await capture_guide_image(obs, 0.3)

        assert fake_image.writeto.call_count == 2  # raw + corrected
        broadcast_mock.assert_not_awaited()
        assert not gui_dir.exists()

    async def test_image_to_gui_true_writes_fixed_name_copy_and_broadcasts(self, tmp_path, monkeypatch):
        gui_dir = tmp_path / "gui_preview"
        monkeypatch.setattr(acquisition_helpers, "GUI_PREVIEW_DIR", gui_dir)
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(acquisition_helpers.ws_image_broadcaster, "broadcast", broadcast_mock)
        fake_image = _fake_image()
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        await capture_guide_image(obs, 0.3, image_to_gui=True)

        assert fake_image.writeto.call_count == 3  # raw + corrected + gui preview
        fake_image.writeto.assert_any_call(gui_dir / "guidage.fits", overwrite=True)
        broadcast_mock.assert_awaited_once_with("guidage", kind="guide")

    async def test_lamp_lit_image_is_sent_as_slit_kind(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acquisition_helpers, "GUI_PREVIEW_DIR", tmp_path / "gui_preview")
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(acquisition_helpers.ws_image_broadcaster, "broadcast", broadcast_mock)
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = _fake_image()

        await capture_guide_image(obs, 0.01, light_source=LightSource.CALIB, subtract_dark=False, image_to_gui=True)

        broadcast_mock.assert_awaited_once_with("guidage", kind="slit")

    async def test_switches_spectroscope_to_requested_light_source(self, tmp_path):
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = _fake_image()

        await capture_guide_image(obs, 0.1, light_source=LightSource.CALIB, subtract_dark=False)

        obs.hardware["spectroscope"].set_light_source.assert_awaited_once_with(LightSource.CALIB)

    async def test_waits_light_settle_seconds_before_exposing(self, tmp_path, monkeypatch):
        calls = []
        obs = _make_obs(tmp_path)
        obs.hardware["spectroscope"].set_light_source.side_effect = lambda *_: calls.append("light")

        async def _sleep(seconds):
            calls.append(("sleep", seconds))

        async def _expose(_):
            calls.append("expose")
            return _fake_image()

        monkeypatch.setattr(acquisition_helpers.asyncio, "sleep", _sleep)
        obs.hardware["guiding_camera"].expose.side_effect = _expose

        await capture_guide_image(obs, 0.1, light_settle_seconds=1.0)

        assert calls == ["light", ("sleep", 1.0), "expose"]

    async def test_no_settle_pause_by_default(self, tmp_path, monkeypatch):
        sleep_mock = AsyncMock()
        monkeypatch.setattr(acquisition_helpers.asyncio, "sleep", sleep_mock)
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = _fake_image()

        await capture_guide_image(obs, 0.3)

        sleep_mock.assert_not_awaited()


class TestPublishGuidePreview:

    async def test_copies_image_and_broadcasts_source_and_overlay(self, tmp_path, monkeypatch):
        gui_dir = tmp_path / "gui_preview"
        monkeypatch.setattr(acquisition_helpers, "GUI_PREVIEW_DIR", gui_dir)
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(acquisition_helpers.ws_image_broadcaster, "broadcast", broadcast_mock)
        image_path = tmp_path / "GUIDING_RAW_x.fits"
        fits.PrimaryHDU(np.full(_DEFAULT_FRAME, 7, dtype=np.uint16)).writeto(image_path)
        overlay = {"zone": [0, 0, 3, 3], "line": None, "point": None}

        await publish_guide_preview(image_path, source="slit_xy", overlay=overlay, kind="slit")

        assert (fits.getdata(gui_dir / "guidage.fits") == 7).all()
        broadcast_mock.assert_awaited_once_with("guidage", source="slit_xy", overlay=overlay, kind="slit")

    async def test_preview_labelled_with_object_and_observation_id(self, tmp_path, monkeypatch):
        gui_dir = tmp_path / "gui_preview"
        monkeypatch.setattr(acquisition_helpers, "GUI_PREVIEW_DIR", gui_dir)
        monkeypatch.setattr(acquisition_helpers.ws_image_broadcaster, "broadcast", AsyncMock())
        image_path = tmp_path / "GUIDING_x.fits"
        fits.PrimaryHDU(np.zeros(_DEFAULT_FRAME, dtype=np.uint16)).writeto(image_path)
        obs = _make_obs(tmp_path)
        obs.scheduler_data["queried_name"] = "HD 7636"

        await publish_guide_preview(image_path, source="focus", obs=obs)

        header = fits.getheader(gui_dir / "guidage.fits")
        assert (header["OBJECT"], header["OBS_ID"]) == ("HD 7636", obs.id)
        assert "OBS_ID" not in fits.getheader(image_path)   # the observation's own file is untouched

    async def test_kind_defaults_to_guide(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acquisition_helpers, "GUI_PREVIEW_DIR", tmp_path / "gui_preview")
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(acquisition_helpers.ws_image_broadcaster, "broadcast", broadcast_mock)
        image_path = tmp_path / "GUIDING_x.fits"
        fits.PrimaryHDU(np.zeros(_DEFAULT_FRAME, dtype=np.uint16)).writeto(image_path)

        await publish_guide_preview(image_path, source="focus")

        broadcast_mock.assert_awaited_once_with("guidage", source="focus", overlay=None, kind="guide")


class TestSlitPosition:

    @pytest.fixture(autouse=True)
    def _default_slit(self, monkeypatch):
        monkeypatch.setattr(
            acquisition_helpers, "SUBPLUGIN_PARAMS", {"guiding": {"slit_x": 613, "slit_y": 614}}
        )

    def test_default_when_slit_xy_did_not_run(self, tmp_path):
        obs = _make_obs(tmp_path)

        assert slit_position(obs) == (613.0, 614.0)

    def test_calculated_position_when_slit_xy_succeeded(self, tmp_path):
        obs = _make_obs(tmp_path)
        obs.sequencer_data["slit_xy"] = {"status": "calculated", "slit_x": 613.0, "slit_y": 617.23, "image": "a.fits"}

        assert slit_position(obs) == (613.0, 617.23)

    def test_default_when_slit_xy_failed(self, tmp_path):
        obs = _make_obs(tmp_path)
        obs.sequencer_data["slit_xy"] = {"status": "default", "image": None}

        assert slit_position(obs) == (613.0, 614.0)


class TestCaptureGuideImageRecovery:
    """The guide exposure goes through run_with_guiding_recovery() -- the
    recovery logic itself is tested in test_guiding_chain_recovery.py, these
    only check it is actually wired in."""

    @pytest.fixture(autouse=True)
    def no_pause(self, monkeypatch):
        monkeypatch.setattr(guiding_chain_recovery, "PAUSE_BETWEEN_ATTEMPTS_SECONDS", 0.0)

    async def test_dead_camera_is_recovered_and_the_exposure_replayed(self, tmp_path):
        obs = _make_obs(tmp_path)
        camera = obs.hardware["guiding_camera"]
        # failed exposure, failed probe, successful probe after the reconnect, replayed exposure
        camera.expose.side_effect = [DeviceCommandError("dead"), DeviceCommandError("dead"), None, _fake_image()]

        path = await capture_guide_image(obs, 0.3, filename="GUIDING_test.fits")

        assert path.exists()
        camera.connect.assert_awaited_once()

    async def test_healthy_camera_failure_propagates_untouched(self, tmp_path):
        obs = _make_obs(tmp_path)
        camera = obs.hardware["guiding_camera"]
        camera.expose.side_effect = [DeviceCommandError("no frame"), None]  # failure, then a good probe

        with pytest.raises(DeviceCommandError, match="no frame"):
            await capture_guide_image(obs, 0.3)

        camera.disconnect.assert_not_awaited()

    async def test_unrecoverable_camera_raises_session_fatal_error(self, tmp_path):
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.side_effect = DeviceCommandError("CAMERA_REMOVED")

        with pytest.raises(SessionFatalError):
            await capture_guide_image(obs, 0.3)


class TestCaptureGuideImageDarkSubtraction:

    async def test_raises_if_no_guiding_dark_available(self, tmp_path):
        obs = _make_obs(tmp_path, dark_value=None)
        obs.hardware["guiding_camera"].expose.return_value = _fake_image()

        with pytest.raises(RuntimeError, match="guiding_dark_acquisition"):
            await capture_guide_image(obs, 0.3)

    async def test_corrected_file_has_dark_subtracted(self, tmp_path):
        obs = _make_obs(tmp_path, dark_value=30)
        raw_data = np.full(_DEFAULT_FRAME, 100, dtype=np.uint16)
        obs.hardware["guiding_camera"].expose.return_value = _fake_image(raw_data)

        result = await capture_guide_image(obs, 0.3)

        assert (fits.getdata(result) == 70).all()

    async def test_raw_file_keeps_original_uncorrected_data(self, tmp_path, freeze_fits_timestamp):
        obs = _make_obs(tmp_path, dark_value=30)
        raw_data = np.full(_DEFAULT_FRAME, 100, dtype=np.uint16)
        obs.hardware["guiding_camera"].expose.return_value = _fake_image(raw_data)

        await capture_guide_image(obs, 0.3)

        raw_written = fits.getdata(tmp_path / f"GUIDING_RAW_0_30s_{FIXED_TS}.fits")
        assert (raw_written == 100).all()

    async def test_clips_negative_values_to_zero(self, tmp_path):
        obs = _make_obs(tmp_path, dark_value=200)
        raw_data = np.full(_DEFAULT_FRAME, 50, dtype=np.uint16)
        obs.hardware["guiding_camera"].expose.return_value = _fake_image(raw_data)

        result = await capture_guide_image(obs, 0.3)

        assert (fits.getdata(result) == 0).all()

    async def test_subtract_dark_false_needs_no_guiding_dark_and_only_writes_raw(self, tmp_path, freeze_fits_timestamp):
        obs = _make_obs(tmp_path, dark_value=None)  # no guiding dark registered at all
        fake_image = _fake_image()
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        result = await capture_guide_image(obs, 0.3, subtract_dark=False)

        expected_raw = tmp_path / f"GUIDING_RAW_0_30s_{FIXED_TS}.fits"
        expected_corrected = tmp_path / f"GUIDING_0_30s_{FIXED_TS}.fits"
        assert result == expected_raw
        assert expected_raw.exists()
        assert not expected_corrected.exists()
        assert fake_image.writeto.call_count == 1


class TestComputeExposureTimeAndCount:

    def test_magnitude_zero_returns_mag0_exposure_time(self):
        exposure_time, count = compute_exposure_time_and_count(magnitude=0.0, exposure_time_mag0=1.0, max_exposure_time=300)

        assert exposure_time == pytest.approx(1.0)
        assert count == 1

    def test_matches_user_provided_example(self):
        # mag 2.5 -> 1.0 * 10**(2.5/2.5) = 10s ; mag 5 -> 1.0 * 10**(5/2.5) = 100s
        assert compute_exposure_time_and_count(magnitude=2.5, exposure_time_mag0=1.0, max_exposure_time=300) == (pytest.approx(10.0), 1)
        assert compute_exposure_time_and_count(magnitude=5.0, exposure_time_mag0=1.0, max_exposure_time=300) == (pytest.approx(100.0), 1)

    def test_splits_into_several_max_duration_exposures_once_over_max(self):
        # raw = 1.0 * 10**(7.5/2.5) = 1000s ; 1000/300 -> ceil = 4
        exposure_time, count = compute_exposure_time_and_count(magnitude=7.5, exposure_time_mag0=1.0, max_exposure_time=300)

        assert exposure_time == pytest.approx(300.0)
        assert count == 4

    def test_exactly_at_max_is_not_split(self):
        exposure_time, count = compute_exposure_time_and_count(magnitude=0.0, exposure_time_mag0=300.0, max_exposure_time=300)

        assert exposure_time == pytest.approx(300.0)
        assert count == 1

    def test_explicit_exposure_time_below_max_is_not_split(self):
        exposure_time, count = compute_exposure_time_and_count(exposure_time=180, max_exposure_time=300)

        assert exposure_time == pytest.approx(180.0)
        assert count == 1

    def test_explicit_exposure_time_matches_user_provided_example(self):
        # 780s at a 300s max -> ceil(780/300) = 3 sub-exposures of 300s each
        exposure_time, count = compute_exposure_time_and_count(exposure_time=780, max_exposure_time=300)

        assert exposure_time == pytest.approx(300.0)
        assert count == 3

    def test_explicit_exposure_time_ignores_exposure_time_mag0(self):
        exposure_time, count = compute_exposure_time_and_count(
            exposure_time=180, exposure_time_mag0=999.0, max_exposure_time=300
        )

        assert exposure_time == pytest.approx(180.0)
        assert count == 1

    def test_rejects_neither_magnitude_nor_exposure_time(self):
        with pytest.raises(ValueError, match="exactly one"):
            compute_exposure_time_and_count(max_exposure_time=300)

    def test_rejects_both_magnitude_and_exposure_time(self):
        with pytest.raises(ValueError, match="exactly one"):
            compute_exposure_time_and_count(magnitude=2.5, exposure_time=180, max_exposure_time=300)

    def test_rejects_magnitude_without_exposure_time_mag0(self):
        with pytest.raises(ValueError, match="exposure_time_mag0"):
            compute_exposure_time_and_count(magnitude=2.5, max_exposure_time=300)

    def test_min_exposure_count_repeats_short_raw_duration_unchanged(self):
        # raw = 1.0 * 10**(2.5/2.5) = 10s, well under max -- with a min count,
        # every sub-exposure still lasts the full 10s, just repeated 5x.
        exposure_time, count = compute_exposure_time_and_count(
            magnitude=2.5, exposure_time_mag0=1.0, max_exposure_time=300, min_exposure_count=5
        )

        assert exposure_time == pytest.approx(10.0)
        assert count == 5

    def test_min_exposure_count_applies_to_explicit_exposure_time_too(self):
        exposure_time, count = compute_exposure_time_and_count(
            exposure_time=8.0, max_exposure_time=300, min_exposure_count=5
        )

        assert exposure_time == pytest.approx(8.0)
        assert count == 5

    def test_min_exposure_count_is_a_floor_not_added_on_top_of_ceil(self):
        # raw 1000s at a 300s max -> ceil=4, already >= min_exposure_count=3.
        exposure_time, count = compute_exposure_time_and_count(
            magnitude=7.5, exposure_time_mag0=1.0, max_exposure_time=300, min_exposure_count=3
        )

        assert exposure_time == pytest.approx(300.0)
        assert count == 4

    def test_min_exposure_count_raises_ceil_count_when_higher(self):
        # raw 1000s at a 300s max -> ceil=4, but min_exposure_count=6 wins.
        exposure_time, count = compute_exposure_time_and_count(
            magnitude=7.5, exposure_time_mag0=1.0, max_exposure_time=300, min_exposure_count=6
        )

        assert exposure_time == pytest.approx(300.0)
        assert count == 6

    def test_min_exposure_count_defaults_to_one(self):
        exposure_time, count = compute_exposure_time_and_count(exposure_time=10.0, max_exposure_time=300)

        assert count == 1


class TestCaptureScienceImage:

    async def test_exposes_camera_for_given_duration(self, tmp_path):
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()

        await capture_science_image(camera, 30.0, str(tmp_path), "STAR_30s.fits")

        camera.expose.assert_awaited_once_with(30.0)

    async def test_writes_fits_and_returns_path(self, tmp_path):
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image

        result = await capture_science_image(camera, 30.0, str(tmp_path), "STAR_30s.fits")

        expected_path = tmp_path / "STAR_30s.fits"
        fake_image.writeto.assert_called_once_with(expected_path, overwrite=True)
        assert result == expected_path

    async def test_image_to_gui_false_by_default_no_extra_write_no_broadcast(self, tmp_path, monkeypatch):
        gui_dir = tmp_path / "gui_preview"
        monkeypatch.setattr(acquisition_helpers, "GUI_PREVIEW_DIR", gui_dir)
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(acquisition_helpers.ws_image_broadcaster, "broadcast", broadcast_mock)
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image

        await capture_science_image(camera, 30.0, str(tmp_path), "STAR_30s.fits")

        assert fake_image.writeto.call_count == 1
        broadcast_mock.assert_not_awaited()
        assert not gui_dir.exists()

    async def test_image_to_gui_true_writes_fixed_name_copy_and_broadcasts(self, tmp_path, monkeypatch):
        gui_dir = tmp_path / "gui_preview"
        monkeypatch.setattr(acquisition_helpers, "GUI_PREVIEW_DIR", gui_dir)
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(acquisition_helpers.ws_image_broadcaster, "broadcast", broadcast_mock)
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image

        await capture_science_image(camera, 30.0, str(tmp_path), "STAR_30s.fits", image_to_gui=True)

        assert fake_image.writeto.call_count == 2
        fake_image.writeto.assert_any_call(gui_dir / "spectre.fits", overwrite=True)
        broadcast_mock.assert_awaited_once_with("spectre", kind="science")

    async def test_preview_labelled_only_after_the_file_is_written(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acquisition_helpers, "GUI_PREVIEW_DIR", tmp_path / "gui_preview")
        monkeypatch.setattr(acquisition_helpers.ws_image_broadcaster, "broadcast", AsyncMock())
        image = fits.HDUList([fits.PrimaryHDU(np.zeros(_DEFAULT_FRAME, dtype=np.uint16))])
        camera = AsyncMock()
        camera.expose.return_value = image
        obs = _make_obs(tmp_path)
        obs.scheduler_data["queried_name"] = "psi Per"

        path = await capture_science_image(camera, 5.0, str(tmp_path), "psi_Per-1.fits", image_to_gui=True, obs=obs)

        assert "OBS_ID" not in fits.getheader(path)
        preview = fits.getheader(tmp_path / "gui_preview" / "spectre.fits")
        assert (preview["OBJECT"], preview["OBS_ID"]) == ("psi Per", obs.id)


class TestFrameKind:

    @pytest.mark.parametrize("filename, kind", [
        ("DARK_-5deg_5x180s_20261012_183653-1.fits", "dark"),
        ("BIAS_-5deg_5x0_00s_20261015_003402-2.fits", "bias"),
        ("FLAT_5x3s_20261014_181516-1.fits", "flat"),
        ("CALIB_3x3s_20261014_181500-3.fits", "calib"),
        ("HD_7636_13x180s_20261014_235411-1.fits", "science"),
        ("Vega_5x4s_20261016_181700-1.fits", "science"),
        ("/some/dir/FLAT_5x3s_x-1.fits", "flat"),
    ])
    def test_kind_from_file_name(self, filename, kind):
        assert frame_kind(filename) == kind


class TestBuildFitsRootName:

    FIXED_TS = datetime(2026, 8, 7, 14, 30, 22, tzinfo=timezone.utc)

    def test_star_with_target_name_and_single_exposure(self):
        result = build_fits_root_name("STAR", target_name="V831 Cas", exposure_time=30, timestamp=self.FIXED_TS)

        assert result == "V831_Cas_30s_20260807_143022"

    def test_star_without_target_name_falls_back_to_literal_star(self):
        result = build_fits_root_name("STAR", exposure_time=30, timestamp=self.FIXED_TS)

        assert result == "STAR_30s_20260807_143022"

    def test_star_target_series_of_exposures(self):
        result = build_fits_root_name(
            "STAR", target_name="V831 Cas", exposure_time=30, num_exposures=7, timestamp=self.FIXED_TS
        )

        assert result == "V831_Cas_7x30s_20260807_143022"

    def test_sub_second_exposure_uses_underscore_decimal(self):
        result = build_fits_root_name("guiding", exposure_time=0.3, timestamp=self.FIXED_TS)

        assert result == "GUIDING_0_30s_20260807_143022"

    def test_guidingdark_is_a_plain_prefix_like_dark_bias(self):
        """Deliberately a separate literal prefix from "DARK" (not a nested
        variant of it) -- so find_usable_frame_series()'s "DARK_*.fits" glob
        (used by the science-camera dark_acquisition) can never accidentally
        match a guiding-camera dark file, or vice versa."""
        result = build_fits_root_name("GUIDINGDARK", exposure_time=10, num_exposures=7, timestamp=self.FIXED_TS)

        assert result == "GUIDINGDARK_7x10s_20260807_143022"

    def test_strips_unsafe_characters_from_target_name(self):
        result = build_fits_root_name("STAR", target_name="HD 12345*", exposure_time=30, timestamp=self.FIXED_TS)

        assert result == "HD_12345_30s_20260807_143022"

    def test_guiding_with_target_name_inserted_after_prefix(self):
        result = build_fits_root_name("GUIDING", target_name="Vega", exposure_time=0.3, timestamp=self.FIXED_TS)

        assert result == "GUIDING_Vega_0_30s_20260807_143022"

    def test_guiding_without_target_name_keeps_literal_prefix_alone(self):
        result = build_fits_root_name("GUIDING", exposure_time=0.3, timestamp=self.FIXED_TS)

        assert result == "GUIDING_0_30s_20260807_143022"

    def test_guiding_strips_unsafe_characters_from_target_name(self):
        result = build_fits_root_name("GUIDING", target_name="HD 12345*", exposure_time=0.3, timestamp=self.FIXED_TS)

        assert result == "GUIDING_HD_12345_0_30s_20260807_143022"

    def test_guiding_target_name_with_no_safe_characters_left_raises(self):
        with pytest.raises(ValueError, match="no safe characters"):
            build_fits_root_name("GUIDING", target_name="***", exposure_time=0.3)

    def test_image_type_is_case_insensitive_and_normalized_to_upper(self):
        result = build_fits_root_name("dark", exposure_time=30, timestamp=self.FIXED_TS)

        assert result.startswith("DARK_")

    def test_dark_with_known_sensor_temperature(self):
        result = build_fits_root_name("DARK", exposure_time=30, sensor_temp_c=-30.4, timestamp=self.FIXED_TS)

        assert result == "DARK_-30deg_30s_20260807_143022"

    def test_only_image_type_is_required(self):
        result = build_fits_root_name("DARK", timestamp=self.FIXED_TS)

        assert result == "DARK_20260807_143022"

    def test_missing_exposure_time_omits_exposure_field_even_with_temp(self):
        result = build_fits_root_name("BIAS", sensor_temp_c=-20.0, timestamp=self.FIXED_TS)

        assert result == "BIAS_-20deg_20260807_143022"

    def test_missing_sensor_temp_omits_temp_field_no_error(self):
        result = build_fits_root_name("DARK", exposure_time=30, timestamp=self.FIXED_TS)

        assert result == "DARK_30s_20260807_143022"

    def test_rejects_unknown_image_type(self):
        with pytest.raises(ValueError, match="image_type"):
            build_fits_root_name("NOTATYPE", exposure_time=30)

    def test_rejects_target_name_with_non_star_type(self):
        with pytest.raises(ValueError, match="target_name"):
            build_fits_root_name("DARK", target_name="V831 Cas", exposure_time=30)

    def test_rejects_sensor_temp_for_non_dark_bias_type(self):
        with pytest.raises(ValueError, match="sensor_temp_c"):
            build_fits_root_name("FLAT", exposure_time=30, sensor_temp_c=-30.0)

    def test_rejects_target_name_with_no_safe_characters_left(self):
        with pytest.raises(ValueError, match="no safe characters"):
            build_fits_root_name("STAR", target_name="***", exposure_time=30)

    def test_defaults_timestamp_to_now_utc(self):
        before = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        result = build_fits_root_name("BIAS", exposure_time=1)

        assert result.startswith(f"BIAS_1s_{before[:8]}")
