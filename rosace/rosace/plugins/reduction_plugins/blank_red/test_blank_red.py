"""
Tests du sous-plugin REDUCTION "blank_red" (BlankRedPlugin) — gabarit minimal.
Lancement : pytest plugins/reduction_plugins/blank_red/test_blank_red.py -v
"""

from datetime import datetime, timezone

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.blank_red.blank_red import BlankRedPlugin


def _make_obs(raw_files=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if raw_files is not None:
        obs.sequencer_data["expose"] = {"raw_files": raw_files}
    return obs


class TestBlankRedPlugin:

    async def test_raises_if_expose_missing(self):
        obs = _make_obs()  # sequencer_data == {}, expose n'a jamais tourné
        with pytest.raises(RuntimeError, match="expose"):
            await BlankRedPlugin().execute(obs)

    async def test_processes_raw_files(self):
        obs = _make_obs(raw_files=["/tmp/spectrum_0001.fits"])

        result = await BlankRedPlugin().execute(obs)

        assert result.reduction_data["blank_red"] == {"processed_files": ["/tmp/spectrum_0001.fits"]}
