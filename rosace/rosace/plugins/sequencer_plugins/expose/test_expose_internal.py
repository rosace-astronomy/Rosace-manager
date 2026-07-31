"""
Tests du sous-plugin EXPOSE (ExposePlugin), backend internal.
Lancement : pytest plugins/sequencer_plugins/internal/test_expose.py -v
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation, Target, ExposureConfig
from rosace.plugins.sequencer_plugins.expose.expose_internal import ExposePlugin


def _make_obs(obs_dir=None, slew_done=True, exposure=None, target=None) -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir) if obs_dir else None,
    )
    if slew_done:
        obs.sequencer_data["slew_done"] = True
    if exposure is not None:
        obs.scheduler_data["exposure"] = exposure
    if target is not None:
        obs.scheduler_data["target"] = target
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.sequencer_plugins.expose.expose_internal.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestExposePlugin:

    async def test_raises_if_slew_not_done(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, slew_done=False, exposure=ExposureConfig(exposure_time=1.0))
        with pytest.raises(RuntimeError, match="slew_done"):
            await ExposePlugin().execute(obs)

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None, exposure=ExposureConfig(exposure_time=1.0))
        with pytest.raises(RuntimeError, match="obs_dir"):
            await ExposePlugin().execute(obs)

    async def test_raises_if_exposure_missing(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, exposure=None)
        with pytest.raises(RuntimeError, match="exposure"):
            await ExposePlugin().execute(obs)

    async def test_produces_expected_number_of_files(self, tmp_path):
        exposure = ExposureConfig(exposure_time=1.0, num_exposures=3)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, target=Target(name="Vega", ra=1.0, dec=1.0))

        result = await ExposePlugin().execute(obs)

        raw_files = result.sequencer_data["raw_files"]
        assert len(raw_files) == 3
        assert result.sequencer_data["expose_done"] is True
        assert result.sequencer_data["expose_count"] == 3
        for f in raw_files:
            assert Path(f).is_file()  # vraiment créés sur disque (mode simulate)

    async def test_appends_to_existing_raw_files(self, tmp_path):
        exposure = ExposureConfig(exposure_time=1.0, num_exposures=1)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure)
        obs.sequencer_data["raw_files"] = ["/tmp/previous.fits"]

        result = await ExposePlugin().execute(obs)

        assert result.sequencer_data["raw_files"][0] == "/tmp/previous.fits"
        assert len(result.sequencer_data["raw_files"]) == 2
