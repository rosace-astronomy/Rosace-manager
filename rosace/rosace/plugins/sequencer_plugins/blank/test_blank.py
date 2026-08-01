"""
Tests du sous-plugin SEQUENCER "blank" (BlankPlugin) — gabarit minimal.
Lancement : pytest plugins/sequencer_plugins/blank/test_blank.py -v
"""

from datetime import datetime, timezone

from rosace.models import Observation, Target
from rosace.plugins.sequencer_plugins.blank.blank import BlankPlugin


def _make_obs(target=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if target is not None:
        obs.scheduler_data["target"] = target
    return obs


class TestBlankPlugin:

    async def test_sets_blank_target(self):
        obs = _make_obs(target=Target(name="Vega", ra=279.23, dec=38.78))
        result = await BlankPlugin().execute(obs)

        assert result is obs
        assert obs.sequencer_data["blank"] == {"target": "Vega"}

    async def test_handles_missing_target_gracefully(self):
        obs = _make_obs(target=None)
        result = await BlankPlugin().execute(obs)

        assert result.sequencer_data["blank"] == {"target": "inconnue"}
