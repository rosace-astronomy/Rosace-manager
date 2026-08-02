"""
Tests du sous-plugin EXPOSE (ExposePlugin), backend indi.
Vérifie que ExposePlugin appelle bien obs.hardware["science_camera"].expose()
et écrit le FITS reçu sur disque — la caméra elle-même est un mock, pas de
matériel réel.
Lancement : pytest plugins/sequencer_plugins/expose/test_expose.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from rosace.models import Observation, ExposureConfig
from rosace.plugins.sequencer_plugins.expose.expose import ExposePlugin


def _make_obs(obs_dir=None, slew_done=True, exposure=None, camera=None) -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir) if obs_dir else None,
    )
    if slew_done:
        obs.sequencer_data["slew"] = {"target": "Vega"}
    if exposure is not None:
        obs.scheduler_data["exposure"] = exposure
    obs.hardware["science_camera"] = camera if camera is not None else AsyncMock()
    return obs


class TestExposePlugin:

    async def test_raises_if_slew_not_done(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, slew_done=False, exposure=ExposureConfig(exposure_time=1.0))
        with pytest.raises(RuntimeError, match="slew"):
            await ExposePlugin().execute(obs)

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None, exposure=ExposureConfig(exposure_time=1.0))
        with pytest.raises(RuntimeError, match="obs_dir"):
            await ExposePlugin().execute(obs)

    async def test_raises_if_exposure_missing(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, exposure=None)
        with pytest.raises(RuntimeError, match="exposure"):
            await ExposePlugin().execute(obs)

    async def test_raises_if_exposure_time_none(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None))
        with pytest.raises(RuntimeError, match="exposure_time"):
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
