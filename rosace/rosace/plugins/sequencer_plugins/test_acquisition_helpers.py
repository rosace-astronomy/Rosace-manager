"""
Tests for the shared acquisition helpers (acquisition_helpers.py).
Run: pytest plugins/sequencer_plugins/test_acquisition_helpers.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from astropy.io import fits

from rosace.hardware.spectroscope import LightSource
from rosace.models import Observation
from rosace.plugins.sequencer_plugins import acquisition_helpers
from rosace.plugins.sequencer_plugins.acquisition_helpers import (
    build_fits_root_name,
    capture_guide_image,
    capture_science_image,
    compute_exposure_time_and_count,
)

FIXED_TS = "20260807_143022"


def _make_obs(tmp_path) -> Observation:
    obs = Observation(
        id="obs1", created_at=datetime.now(timezone.utc), session_id="sess1", obs_dir=str(tmp_path),
    )
    obs.hardware["guiding_camera"] = AsyncMock()
    obs.hardware["spectroscope"] = AsyncMock()
    return obs


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
        obs.hardware["guiding_camera"].expose.return_value = MagicMock()

        await capture_guide_image(obs, 0.3)

        obs.hardware["guiding_camera"].expose.assert_awaited_once_with(0.3)

    async def test_switches_spectroscope_to_sky_before_exposing(self, tmp_path):
        """Unconditional -- see the function's own docstring for the
        2026-09-03 incident (a stuck DARK spectroscope position producing a
        black guide image) this guards against."""
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = MagicMock()

        await capture_guide_image(obs, 0.3)

        obs.hardware["spectroscope"].set_light_source.assert_awaited_once_with(LightSource.SKY)

    async def test_writes_fits_and_returns_path(self, tmp_path, freeze_fits_timestamp):
        fake_image = MagicMock()
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        result = await capture_guide_image(obs, 0.3)

        expected_path = tmp_path / f"GUIDING_0_30s_{FIXED_TS}.fits"
        fake_image.writeto.assert_called_once_with(expected_path, overwrite=True)
        assert result == expected_path

    async def test_filename_includes_target_name_when_given(self, tmp_path, freeze_fits_timestamp):
        fake_image = MagicMock()
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        result = await capture_guide_image(obs, 0.3, target_name="Vega")

        assert result == tmp_path / f"GUIDING_Vega_0_30s_{FIXED_TS}.fits"

    async def test_uses_custom_filename(self, tmp_path):
        fake_image = MagicMock()
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        result = await capture_guide_image(obs, 0.3, filename="check.fits")

        assert result == tmp_path / "check.fits"

    async def test_stamps_ra_dec_equinox_when_given(self, tmp_path):
        fake_image = MagicMock()
        fake_header = fits.Header()
        fake_image.__getitem__.return_value.header = fake_header
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        await capture_guide_image(obs, 0.3, ra_deg=279.23, dec_deg=38.78)

        assert fake_header["RA"] == 279.23
        assert fake_header["DEC"] == 38.78
        assert fake_header["EQUINOX"] == 2000
        # Every stamped card must credit Rosace, so a reader can tell it
        # wasn't left over from the camera driver's own snoop.
        assert "Rosace" in fake_header.comments["RA"]
        assert "Rosace" in fake_header.comments["DEC"]
        assert "Rosace" in fake_header.comments["EQUINOX"]

    async def test_stamps_object_when_target_name_given(self, tmp_path):
        fake_image = MagicMock()
        fake_header = fits.Header()
        fake_image.__getitem__.return_value.header = fake_header
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        await capture_guide_image(obs, 0.3, target_name="Vega")

        assert fake_header["OBJECT"] == "Vega"
        assert "Rosace" in fake_header.comments["OBJECT"]

    async def test_no_equinox_without_ra_dec(self, tmp_path):
        fake_image = MagicMock()
        fake_header = fits.Header()
        fake_image.__getitem__.return_value.header = fake_header
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        await capture_guide_image(obs, 0.3, target_name="Vega")

        assert "EQUINOX" not in fake_header

    async def test_no_header_stamps_by_default(self, tmp_path):
        fake_image = MagicMock()
        fake_header = fits.Header()
        fake_image.__getitem__.return_value.header = fake_header
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        await capture_guide_image(obs, 0.3)

        assert len(fake_header) == 0

    async def test_rejects_ra_deg_without_dec_deg(self, tmp_path):
        obs = _make_obs(tmp_path)
        with pytest.raises(ValueError, match="ra_deg and dec_deg"):
            await capture_guide_image(obs, 0.3, ra_deg=279.23)

    async def test_image_to_gui_false_by_default_no_extra_write_no_broadcast(self, tmp_path, monkeypatch):
        gui_dir = tmp_path / "gui_preview"
        monkeypatch.setattr(acquisition_helpers, "GUI_PREVIEW_DIR", gui_dir)
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(acquisition_helpers.ws_image_broadcaster, "broadcast", broadcast_mock)
        fake_image = MagicMock()
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        await capture_guide_image(obs, 0.3)

        assert fake_image.writeto.call_count == 1
        broadcast_mock.assert_not_awaited()
        assert not gui_dir.exists()

    async def test_image_to_gui_true_writes_fixed_name_copy_and_broadcasts(self, tmp_path, monkeypatch):
        gui_dir = tmp_path / "gui_preview"
        monkeypatch.setattr(acquisition_helpers, "GUI_PREVIEW_DIR", gui_dir)
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(acquisition_helpers.ws_image_broadcaster, "broadcast", broadcast_mock)
        fake_image = MagicMock()
        obs = _make_obs(tmp_path)
        obs.hardware["guiding_camera"].expose.return_value = fake_image

        await capture_guide_image(obs, 0.3, image_to_gui=True)

        assert fake_image.writeto.call_count == 2
        fake_image.writeto.assert_any_call(gui_dir / "guidage.fits", overwrite=True)
        broadcast_mock.assert_awaited_once_with("guidage")


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
        broadcast_mock.assert_awaited_once_with("spectre")


class TestBuildFitsRootName:

    FIXED_TS = datetime(2026, 8, 7, 14, 30, 22, tzinfo=timezone.utc)

    def test_star_with_target_name_and_single_exposure(self):
        result = build_fits_root_name("STAR", target_name="V831 Cas", exposure_time=30, timestamp=self.FIXED_TS)

        assert result == "V831Cas_30s_20260807_143022"

    def test_star_without_target_name_falls_back_to_literal_star(self):
        result = build_fits_root_name("STAR", exposure_time=30, timestamp=self.FIXED_TS)

        assert result == "STAR_30s_20260807_143022"

    def test_star_target_series_of_exposures(self):
        result = build_fits_root_name(
            "STAR", target_name="V831 Cas", exposure_time=30, num_exposures=7, timestamp=self.FIXED_TS
        )

        assert result == "V831Cas_7x30s_20260807_143022"

    def test_sub_second_exposure_uses_underscore_decimal(self):
        result = build_fits_root_name("guiding", exposure_time=0.3, timestamp=self.FIXED_TS)

        assert result == "GUIDING_0_30s_20260807_143022"

    def test_strips_unsafe_characters_from_target_name(self):
        result = build_fits_root_name("STAR", target_name="HD 12345*", exposure_time=30, timestamp=self.FIXED_TS)

        assert result == "HD12345_30s_20260807_143022"

    def test_guiding_with_target_name_inserted_after_prefix(self):
        result = build_fits_root_name("GUIDING", target_name="Vega", exposure_time=0.3, timestamp=self.FIXED_TS)

        assert result == "GUIDING_Vega_0_30s_20260807_143022"

    def test_guiding_without_target_name_keeps_literal_prefix_alone(self):
        result = build_fits_root_name("GUIDING", exposure_time=0.3, timestamp=self.FIXED_TS)

        assert result == "GUIDING_0_30s_20260807_143022"

    def test_guiding_strips_unsafe_characters_from_target_name(self):
        result = build_fits_root_name("GUIDING", target_name="HD 12345*", exposure_time=0.3, timestamp=self.FIXED_TS)

        assert result == "GUIDING_HD12345_0_30s_20260807_143022"

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
