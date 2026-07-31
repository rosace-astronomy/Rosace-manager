"""
Tests du sous-plugin SLEW (SlewPlugin), backend internal.
Lancement : pytest plugins/sequencer_plugins/internal/test_slew.py -v
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

    async def test_sets_slew_done_and_target(self):
        obs = _make_obs(target=Target(name="Vega", ra=279.23, dec=38.78))
        result = await SlewPlugin().execute(obs)

        assert result is obs
        assert obs.sequencer_data["slew_done"] is True
        assert obs.sequencer_data["slew_target"] == "Vega"

    async def test_handles_missing_target_gracefully(self):
        obs = _make_obs(target=None)
        result = await SlewPlugin().execute(obs)

        assert result.sequencer_data["slew_done"] is True
        assert result.sequencer_data["slew_target"] == "inconnue"
