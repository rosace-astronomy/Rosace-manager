"""
Tests du sous-plugin REDUCTION "blank" (BlankPlugin) — gabarit minimal.
Lancement : pytest plugins/reduction_plugins/blank/test_blank.py -v
"""

from datetime import datetime, timezone

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.blank.blank import BlankPlugin


def _make_obs(raw_files=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if raw_files is not None:
        obs.sequencer_data["expose"] = {"raw_files": raw_files}
    return obs


class TestBlankPlugin:

    async def test_raises_if_expose_missing(self):
        obs = _make_obs()  # sequencer_data == {}, expose n'a jamais tourné
        with pytest.raises(RuntimeError, match="expose"):
            await BlankPlugin().execute(obs)

    async def test_processes_raw_files(self):
        obs = _make_obs(raw_files=["/tmp/spectrum_0001.fits"])

        result = await BlankPlugin().execute(obs)

        assert result.reduction_data["blank"] == {"processed_files": ["/tmp/spectrum_0001.fits"]}
