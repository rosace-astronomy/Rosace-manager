"""
Tests for the SLEW sub-plugin (SlewPlugin), internal backend.
Run: pytest plugins/sequencer_plugins/slew/test_slew_internal.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation, Target
from rosace.plugins.sequencer_plugins.slew.slew_internal import SlewPlugin


def _make_obs(target=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if target is not None:
        obs.scheduler_data["target"] = target
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.sequencer_plugins.slew.slew_internal.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestSlewPlugin:

    async def test_returns_the_same_observation(self):
        obs = _make_obs(target=Target(name="Vega", ra=279.23, dec=38.78))
        result = await SlewPlugin().execute(obs)

        assert result is obs

    async def test_handles_missing_target_gracefully(self):
        obs = _make_obs(target=None)
        result = await SlewPlugin().execute(obs)

        assert result is obs
