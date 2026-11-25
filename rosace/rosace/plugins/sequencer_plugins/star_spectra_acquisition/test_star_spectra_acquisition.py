"""
Tests for the STAR_SPECTRA_ACQUISITION sub-plugin (StarSpectraAcquisitionPlugin), indi backend.
Verifies it switches the spectroscope to SKY mode, calls
obs.hardware["science_camera"].expose() and writes the received FITS file(s)
to disk -- both spectroscope and camera are mocks, no real hardware.
Run: pytest plugins/sequencer_plugins/star_spectra_acquisition/test_star_spectra_acquisition.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import logging

import pytest

from rosace.hardware.spectroscope import LightSource
from rosace.models import Observation, ExposureConfig, Target
from rosace.plugins.sequencer_plugins import acquisition_helpers
from rosace.plugins.sequencer_plugins.star_spectra_acquisition import star_spectra_acquisition as ssa_mod
from rosace.plugins.sequencer_plugins.star_spectra_acquisition.star_spectra_acquisition import (
    StarSpectraAcquisitionPlugin,
)

FIXED_TS = "20260807_143022"


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 7, 14, 30, 22, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def freeze_fits_timestamp(monkeypatch):
    monkeypatch.setattr(acquisition_helpers, "datetime", _FrozenDatetime)


@pytest.fixture(autouse=True)
def default_min_exposure_count(monkeypatch):
    """Most tests below are about something other than min_exposure_count
    itself -- pin it to 1 (today's pre-min_exposure_count behavior) so their
    counts stay meaningful; TestMinExposureCount overrides it explicitly."""
    monkeypatch.setattr(ssa_mod, "MIN_EXPOSURE_COUNT", 1)


def _make_obs(
    obs_dir=None, exposure=None, target=None, camera=None, spectroscope=None,
    stop_check=None, queried_name=None,
) -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir) if obs_dir else None,
        stop_check=stop_check,
    )
    if exposure is not None:
        obs.scheduler_data["exposure"] = exposure
    if target is not None:
        obs.scheduler_data["target"] = target
    if queried_name is not None:
        obs.scheduler_data["queried_name"] = queried_name
    obs.hardware["science_camera"] = camera if camera is not None else AsyncMock()
    obs.hardware["spectroscope"] = spectroscope if spectroscope is not None else AsyncMock()
    return obs


class TestStarSpectraAcquisitionPlugin:

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None, exposure=ExposureConfig(exposure_time=1.0))
        with pytest.raises(RuntimeError, match="obs_dir"):
            await StarSpectraAcquisitionPlugin().execute(obs)

    async def test_raises_if_exposure_missing(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, exposure=None)
        with pytest.raises(RuntimeError, match="exposure"):
            await StarSpectraAcquisitionPlugin().execute(obs)

    async def test_raises_if_exposure_time_none_and_no_magnitude(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=None)
        with pytest.raises(RuntimeError, match="magnitude"):
            await StarSpectraAcquisitionPlugin().execute(obs)

    async def test_raises_if_exposure_time_none_and_target_magnitude_none(self, tmp_path):
        target = Target(name="NoMagStar", ra=1.0, dec=1.0, magnitude_v=None)
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target)
        with pytest.raises(RuntimeError, match="magnitude"):
            await StarSpectraAcquisitionPlugin().execute(obs)

    async def test_switches_spectroscope_to_sky_mode(self, tmp_path):
        spectroscope = AsyncMock()
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        exposure = ExposureConfig(exposure_time=1.0)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, camera=camera, spectroscope=spectroscope)

        await StarSpectraAcquisitionPlugin().execute(obs)

        spectroscope.set_light_source.assert_awaited_once_with(LightSource.SKY)

    async def test_calls_camera_expose_with_exposure_time(self, tmp_path):
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()  # a plain (non-mock-async) FITS-like object
        exposure = ExposureConfig(exposure_time=2.5)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, camera=camera)

        await StarSpectraAcquisitionPlugin().execute(obs)

        camera.expose.assert_awaited_once_with(2.5)

    async def test_writes_fits_and_sets_raw_files(self, tmp_path):
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        exposure = ExposureConfig(exposure_time=1.0)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, camera=camera)

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        expected_name = f"STAR_1s_{FIXED_TS}.fits"
        # Also written to the GUI preview copy (image_to_gui=True) -- assert_any_call
        # rather than assert_called_once_with since writeto is now called twice.
        fake_image.writeto.assert_any_call(tmp_path / expected_name, overwrite=True)
        assert result.sequencer_data["star_spectra_images"] == [expected_name]

    async def test_uses_queried_name_as_fits_prefix(self, tmp_path):
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        exposure = ExposureConfig(exposure_time=1.0)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, camera=camera, queried_name="V831 Cas")

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        expected_name = f"V831_Cas_1s_{FIXED_TS}.fits"
        assert result.sequencer_data["star_spectra_images"] == [expected_name]

    async def test_computes_exposure_time_from_magnitude_when_not_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssa_mod, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
        monkeypatch.setattr(ssa_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        target = Target(name="MagStar", ra=1.0, dec=1.0, magnitude_v=2.5)  # -> 10s, count=1
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target, camera=camera)

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        camera.expose.assert_awaited_once_with(pytest.approx(10.0))
        assert len(result.sequencer_data["star_spectra_images"]) == 1

    async def test_splits_into_several_exposures_when_computed_time_exceeds_max(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssa_mod, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
        monkeypatch.setattr(ssa_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        target = Target(name="FaintStar", ra=1.0, dec=1.0, magnitude_v=7.5)  # raw 1000s -> 4x300s
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target, camera=camera)

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        assert camera.expose.await_count == 4
        camera.expose.assert_awaited_with(pytest.approx(300.0))
        root_name = f"STAR_4x300s_{FIXED_TS}"
        expected_files = [f"{root_name}-{i}.fits" for i in range(1, 5)]
        assert result.sequencer_data["star_spectra_images"] == expected_files

    async def test_appends_to_star_spectra_images_instead_of_overwriting(self, tmp_path):
        """star_spectra_images is a shared key -- a value already present (e.g.
        from an earlier acquisition sub-plugin in the same program) must be
        kept, not clobbered."""
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        exposure = ExposureConfig(exposure_time=1.0)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, camera=camera)
        obs.sequencer_data["star_spectra_images"] = ["earlier_image.fits"]

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        expected_name = f"STAR_1s_{FIXED_TS}.fits"
        assert result.sequencer_data["star_spectra_images"] == ["earlier_image.fits", expected_name]

    async def test_splits_explicit_exposure_time_when_it_exceeds_max(self, tmp_path, monkeypatch):
        """exposure_time known in advance (program_stars.exposure_time), no magnitude needed."""
        monkeypatch.setattr(ssa_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        exposure = ExposureConfig(exposure_time=780)  # -> ceil(780/300) = 3x300s
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, camera=camera)

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        assert camera.expose.await_count == 3
        camera.expose.assert_awaited_with(pytest.approx(300.0))
        assert len(result.sequencer_data["star_spectra_images"]) == 3

    async def test_stops_early_when_stop_requested_between_sub_exposures(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssa_mod, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
        monkeypatch.setattr(ssa_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        target = Target(name="FaintStar", ra=1.0, dec=1.0, magnitude_v=7.5)  # raw 1000s -> 4x300s
        stop_after_second = [False, True]  # checked after sub-exposure 1 (continue), then 2 (stop)

        def stop_check():
            return stop_after_second.pop(0)

        obs = _make_obs(
            obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target,
            camera=camera, stop_check=stop_check,
        )

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        assert camera.expose.await_count == 2  # stopped after the 2nd sub-exposure, not all 4
        root_name = f"STAR_4x300s_{FIXED_TS}"  # reflects the planned series of 4, not how many actually ran
        expected_files = [f"{root_name}-{i}.fits" for i in range(1, 3)]
        assert result.sequencer_data["star_spectra_images"] == expected_files

    async def test_stop_check_ignored_on_the_last_sub_exposure(self, tmp_path):
        """No need to check stop_check() after the very last sub-exposure -- nothing left to interrupt anyway."""
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        exposure = ExposureConfig(exposure_time=1.0)  # count=1
        checks = []

        def stop_check():
            checks.append(1)
            return True

        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, camera=camera, stop_check=stop_check)

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        assert checks == []
        assert len(result.sequencer_data["star_spectra_images"]) == 1

    async def test_no_stop_check_behaves_like_before(self, tmp_path, monkeypatch):
        """obs.stop_check absent (None) -- unchanged behavior, no interruption."""
        monkeypatch.setattr(ssa_mod, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
        monkeypatch.setattr(ssa_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        target = Target(name="FaintStar", ra=1.0, dec=1.0, magnitude_v=7.5)  # raw 1000s -> 4x300s
        obs = _make_obs(
            obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target,
            camera=camera, stop_check=None,
        )

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        assert camera.expose.await_count == 4
        assert len(result.sequencer_data["star_spectra_images"]) == 4


class TestTemperatures:

    async def test_records_ambient_and_camera_temperatures(self, tmp_path):
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        camera.get_temperature.return_value = -10.04
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=1.0), camera=camera)
        obs.hardware["focuser"] = AsyncMock()
        obs.hardware["focuser"].get_temperature.return_value = 12.345

        await StarSpectraAcquisitionPlugin().execute(obs)

        assert obs.measurements["ambient_temp_c"] == 12.35
        assert obs.measurements["camera_temp_c"] == -10.04

    async def test_records_the_moon(self, tmp_path, monkeypatch):
        from rosace.astro_math import MoonState
        seen = []
        monkeypatch.setattr(ssa_mod, "moon_state", lambda ra, dec, lat, lon: seen.append((ra, dec)) or
                            MoonState(distance_deg=42.123, illumination=0.876, altitude_deg=-3.21))
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=1.0), camera=camera,
                        target=Target(name="Vega", ra=279.23, dec=38.78))

        await StarSpectraAcquisitionPlugin().execute(obs)

        assert seen == [(279.23, 38.78)]
        assert (obs.measurements["moon_distance_deg"], obs.measurements["moon_illumination"],
                obs.measurements["moon_altitude_deg"]) == (42.1, 0.88, -3.2)

    async def test_unreadable_temperature_never_blocks(self, tmp_path, caplog):
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        camera.get_temperature.side_effect = RuntimeError("no reading")
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=1.0), camera=camera)

        with caplog.at_level(logging.WARNING):
            await StarSpectraAcquisitionPlugin().execute(obs)

        assert "camera_temp_c" not in obs.measurements and "ambient_temp_c" not in obs.measurements
        assert obs.sequencer_data["star_spectra_images"]          # the exposures were still taken
        assert any("Could not read camera_temp_c" in r.getMessage() for r in caplog.records)


class TestScienceExposureTime:

    async def test_records_the_shutter_open_time(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssa_mod, "MIN_EXPOSURE_COUNT", 5)
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=8.0), camera=camera)

        await StarSpectraAcquisitionPlugin().execute(obs)

        assert obs.measurements["science_exposure_s"] == pytest.approx(40.0)  # 5 x 8 s


class TestMinExposureCount:

    async def test_bright_star_gets_at_least_min_exposure_count_shots(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssa_mod, "MIN_EXPOSURE_COUNT", 5)
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        exposure = ExposureConfig(exposure_time=8.0)  # well under max -- would be 1 shot without the floor
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, camera=camera)

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        assert camera.expose.await_count == 5
        camera.expose.assert_awaited_with(pytest.approx(8.0))  # each sub-exposure keeps the full duration
        assert len(result.sequencer_data["star_spectra_images"]) == 5

    async def test_min_exposure_count_does_not_reduce_a_higher_ceil_count(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssa_mod, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
        monkeypatch.setattr(ssa_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        monkeypatch.setattr(ssa_mod, "MIN_EXPOSURE_COUNT", 3)
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        target = Target(name="FaintStar", ra=1.0, dec=1.0, magnitude_v=7.5)  # raw 1000s -> 4x300s, already > 3
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target, camera=camera)

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        assert camera.expose.await_count == 4
