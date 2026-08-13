"""
Tests du sous-plugin SPECTRO_LIGHT_SOURCE_TO_SKY (SpectroLightSourceToSkyPlugin), backend indi.
Lancement : pytest plugins/sequencer_plugins/spectro_light_source_to_sky/test_spectro_light_source_to_sky.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

from rosace.hardware.spectroscope import LightSource
from rosace.models import Observation
from rosace.plugins.sequencer_plugins.spectro_light_source_to_sky.spectro_light_source_to_sky import (
    SpectroLightSourceToSkyPlugin,
)


def _make_obs() -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    obs.hardware["spectroscope"] = AsyncMock()
    return obs


class TestSpectroLightSourceToSkyPlugin:

    async def test_sets_light_source_to_sky(self):
        obs = _make_obs()

        await SpectroLightSourceToSkyPlugin().execute(obs)

        obs.hardware["spectroscope"].set_light_source.assert_awaited_once_with(LightSource.SKY)

    async def test_returns_the_same_observation(self):
        obs = _make_obs()

        result = await SpectroLightSourceToSkyPlugin().execute(obs)

        assert result is obs
