"""
Tests for the STAR_SPECTRA_ACQUISITION sub-plugin (StarSpectraAcquisitionPlugin), indi backend.
Verifies it switches the spectroscope to SKY mode, calls
obs.hardware["science_camera"].expose() and writes the received FITS file(s)
to disk -- both spectroscope and camera are mocks, no real hardware.
Run: pytest plugins/sequencer_plugins/star_spectra_acquisition/test_star_spectra_acquisition.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

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

        expected_path = str(tmp_path / f"STAR_1s_{FIXED_TS}.fits")
        fake_image.writeto.assert_called_once_with(tmp_path / f"STAR_1s_{FIXED_TS}.fits", overwrite=True)
        assert result.sequencer_data["star_spectra_images"] == [expected_path]

    async def test_uses_queried_name_as_fits_prefix(self, tmp_path):
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        exposure = ExposureConfig(exposure_time=1.0)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, camera=camera, queried_name="V831 Cas")

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        expected_path = str(tmp_path / f"V831Cas_1s_{FIXED_TS}.fits")
        assert result.sequencer_data["star_spectra_images"] == [expected_path]

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
        expected_files = [str(tmp_path / f"{root_name}-{i}.fits") for i in range(1, 5)]
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

        expected_path = str(tmp_path / f"STAR_1s_{FIXED_TS}.fits")
        assert result.sequencer_data["star_spectra_images"] == ["earlier_image.fits", expected_path]

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
        expected_files = [str(tmp_path / f"{root_name}-{i}.fits") for i in range(1, 3)]
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
