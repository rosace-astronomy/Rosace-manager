"""
Tests du sous-plugin EXPOSE (ExposePlugin), backend indi — placeholder.
Vérifie uniquement la structure (clés posées), pas de comportement matériel réel.
Lancement : pytest plugins/sequencer_plugins/indi/test_expose.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation, ExposureConfig
from rosace.plugins.sequencer_plugins.expose.expose import ExposePlugin


def _make_obs(obs_dir=None, slew_done=True, exposure=None) -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir) if obs_dir else None,
    )
    if slew_done:
        obs.sequencer_data["slew_done"] = True
    if exposure is not None:
        obs.scheduler_data["exposure"] = exposure
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.sequencer_plugins.expose.expose.asyncio.sleep", AsyncMock(return_value=None)
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

    async def test_sets_expose_done_placeholder_values(self, tmp_path):
        exposure = ExposureConfig(exposure_time=1.0, num_exposures=3)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure)

        result = await ExposePlugin().execute(obs)

        assert result.sequencer_data["raw_files"] == []
        assert result.sequencer_data["expose_done"] is True
        assert result.sequencer_data["expose_count"] == 0
