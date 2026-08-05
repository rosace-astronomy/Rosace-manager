"""
Tests du sous-plugin SEQUENCER "blank_seq" (BlankSeqPlugin) — gabarit minimal.
Lancement : pytest plugins/sequencer_plugins/blank_seq/test_blank_seq.py -v
"""

from datetime import datetime, timezone

from rosace.models import Observation, Target
from rosace.plugins.sequencer_plugins.blank_seq.blank_seq import BlankSeqPlugin


def _make_obs(target=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if target is not None:
        obs.scheduler_data["target"] = target
    return obs


class TestBlankSeqPlugin:

    async def test_sets_blank_target(self):
        obs = _make_obs(target=Target(name="Vega", ra=279.23, dec=38.78))
        result = await BlankSeqPlugin().execute(obs)

        assert result is obs
        assert obs.sequencer_data["blank_seq"] == {"target": "Vega"}

    async def test_handles_missing_target_gracefully(self):
        obs = _make_obs(target=None)
        result = await BlankSeqPlugin().execute(obs)

        assert result.sequencer_data["blank_seq"] == {"target": "inconnue"}
