"""
Tests du sous-plugin EXPOSE (ExposePlugin), backend indi.
Vérifie que ExposePlugin appelle bien obs.hardware["science_camera"].expose()
et écrit le(s) FITS reçu(s) sur disque — la caméra elle-même est un mock, pas
de matériel réel.
Lancement : pytest plugins/sequencer_plugins/expose/test_expose.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from rosace.models import Observation, ExposureConfig, Target
from rosace.plugins.sequencer_plugins.expose import expose as expose_mod
from rosace.plugins.sequencer_plugins.expose.expose import ExposePlugin


def _make_obs(obs_dir=None, exposure=None, target=None, camera=None, stop_check=None) -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir) if obs_dir else None,
        stop_check=stop_check,
    )
    if exposure is not None:
        obs.scheduler_data["exposure"] = exposure
    if target is not None:
        obs.scheduler_data["target"] = target
    obs.hardware["science_camera"] = camera if camera is not None else AsyncMock()
    return obs


class TestExposePlugin:

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None, exposure=ExposureConfig(exposure_time=1.0))
        with pytest.raises(RuntimeError, match="obs_dir"):
            await ExposePlugin().execute(obs)

    async def test_raises_if_exposure_missing(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, exposure=None)
        with pytest.raises(RuntimeError, match="exposure"):
            await ExposePlugin().execute(obs)

    async def test_raises_if_exposure_time_none_and_no_magnitude(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=None)
        with pytest.raises(RuntimeError, match="magnitude"):
            await ExposePlugin().execute(obs)

    async def test_raises_if_exposure_time_none_and_target_magnitude_none(self, tmp_path):
        target = Target(name="NoMagStar", ra=1.0, dec=1.0, magnitude_v=None)
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target)
        with pytest.raises(RuntimeError, match="magnitude"):
            await ExposePlugin().execute(obs)

    async def test_calls_camera_expose_with_exposure_time(self, tmp_path):
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()  # a plain (non-mock-async) FITS-like object
        exposure = ExposureConfig(exposure_time=2.5)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, camera=camera)

        await ExposePlugin().execute(obs)

        camera.expose.assert_awaited_once_with(2.5)

    async def test_writes_fits_and_sets_raw_files(self, tmp_path):
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        exposure = ExposureConfig(exposure_time=1.0)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, camera=camera)

        result = await ExposePlugin().execute(obs)

        expected_path = str(tmp_path / "spectrum_0001.fits")
        fake_image.writeto.assert_called_once_with(tmp_path / "spectrum_0001.fits", overwrite=True)
        assert result.sequencer_data["expose"] == {"raw_files": [expected_path], "count": 1}

    async def test_computes_exposure_time_from_magnitude_when_not_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(expose_mod, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
        monkeypatch.setattr(expose_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        target = Target(name="MagStar", ra=1.0, dec=1.0, magnitude_v=2.5)  # -> 10s, count=1
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target, camera=camera)

        result = await ExposePlugin().execute(obs)

        camera.expose.assert_awaited_once_with(pytest.approx(10.0))
        assert result.sequencer_data["expose"]["count"] == 1

    async def test_splits_into_several_exposures_when_computed_time_exceeds_max(self, tmp_path, monkeypatch):
        monkeypatch.setattr(expose_mod, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
        monkeypatch.setattr(expose_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        target = Target(name="FaintStar", ra=1.0, dec=1.0, magnitude_v=7.5)  # raw 1000s -> 4x300s
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target, camera=camera)

        result = await ExposePlugin().execute(obs)

        assert camera.expose.await_count == 4
        camera.expose.assert_awaited_with(pytest.approx(300.0))
        expected_files = [str(tmp_path / f"spectrum_{i:04d}.fits") for i in range(1, 5)]
        assert result.sequencer_data["expose"] == {"raw_files": expected_files, "count": 4}

    async def test_stops_early_when_stop_requested_between_sub_exposures(self, tmp_path, monkeypatch):
        monkeypatch.setattr(expose_mod, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
        monkeypatch.setattr(expose_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
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

        result = await ExposePlugin().execute(obs)

        assert camera.expose.await_count == 2  # stopped after the 2nd sub-exposure, not all 4
        expected_files = [str(tmp_path / f"spectrum_{i:04d}.fits") for i in range(1, 3)]
        assert result.sequencer_data["expose"] == {"raw_files": expected_files, "count": 2}

    async def test_stop_check_ignored_on_the_last_sub_exposure(self, tmp_path):
        """Pas besoin de vérifier stop_check() après la toute dernière sous-pose —
        il n'y a de toute façon rien à interrompre après."""
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        exposure = ExposureConfig(exposure_time=1.0)  # count=1
        checks = []

        def stop_check():
            checks.append(1)
            return True

        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, camera=camera, stop_check=stop_check)

        result = await ExposePlugin().execute(obs)

        assert checks == []
        assert result.sequencer_data["expose"]["count"] == 1

    async def test_no_stop_check_behaves_like_before(self, tmp_path, monkeypatch):
        """obs.stop_check absent (None) — comportement inchangé, aucune interruption."""
        monkeypatch.setattr(expose_mod, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
        monkeypatch.setattr(expose_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        target = Target(name="FaintStar", ra=1.0, dec=1.0, magnitude_v=7.5)  # raw 1000s -> 4x300s
        obs = _make_obs(
            obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target,
            camera=camera, stop_check=None,
        )

        result = await ExposePlugin().execute(obs)

        assert camera.expose.await_count == 4
        assert result.sequencer_data["expose"]["count"] == 4
