"""
Tests du sous-plugin STOP_PHD2_GUIDING (StopPhd2GuidingPlugin), backend indi.
Lancement : pytest plugins/sequencer_plugins/stop_phd2_guiding/test_stop_phd2_guiding.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

from rosace.models import Observation
from rosace.plugins.sequencer_plugins.stop_phd2_guiding.stop_phd2_guiding import StopPhd2GuidingPlugin


def _make_obs() -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    obs.hardware["phd2"] = AsyncMock()
    return obs


class TestStopPhd2GuidingPlugin:

    async def test_stops_phd2_capture(self):
        obs = _make_obs()

        await StopPhd2GuidingPlugin().execute(obs)

        obs.hardware["phd2"].stop_capture.assert_awaited_once_with()

    async def test_sets_sequencer_data(self):
        obs = _make_obs()

        result = await StopPhd2GuidingPlugin().execute(obs)

        assert result is obs
        assert obs.sequencer_data["stop_phd2_guiding"] == {"guiding_stopped": True}
