"""
Tests du sous-plugin SLEW (SlewPlugin), backend indi.
Vérifie que SlewPlugin appelle bien obs.hardware["mount"].slew_to() avec les
bonnes coordonnées — le mount lui-même est un mock, pas de matériel réel.
Lancement : pytest plugins/sequencer_plugins/slew/test_slew.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation, Target
from rosace.plugins.sequencer_plugins.slew.slew import SlewPlugin


def _make_obs(target=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if target is not None:
        obs.scheduler_data["target"] = target
    obs.hardware["mount"] = AsyncMock()
    return obs


class TestSlewPlugin:

    async def test_calls_mount_slew_to_with_target_coordinates(self):
        obs = _make_obs(target=Target(name="Vega", ra=279.23, dec=38.78))
        result = await SlewPlugin().execute(obs)

        assert result is obs
        obs.hardware["mount"].slew_to.assert_awaited_once_with(279.23, 38.78)

    async def test_raises_on_missing_target(self):
        obs = _make_obs(target=None)

        with pytest.raises(RuntimeError, match="target"):
            await SlewPlugin().execute(obs)
