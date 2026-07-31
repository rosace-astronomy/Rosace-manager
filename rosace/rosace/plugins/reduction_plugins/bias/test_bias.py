"""
Tests du sous-plugin BIAS (BiasPlugin).
Lancement : pytest plugins/reduction_plugins/bias/test_bias.py -v
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.bias.bias import BiasPlugin


def _make_obs(raw_files=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if raw_files is not None:
        obs.sequencer_data["expose"] = {"raw_files": raw_files}
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.bias.bias.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestBiasPlugin:

    async def test_raises_if_expose_missing(self):
        obs = _make_obs()  # sequencer_data == {}, expose n'a jamais tourné
        with pytest.raises(RuntimeError, match="expose"):
            await BiasPlugin().execute(obs)

    async def test_raises_if_no_raw_files(self):
        obs = _make_obs(raw_files=[])
        with pytest.raises(RuntimeError, match="raw_files"):
            await BiasPlugin().execute(obs)

    async def test_produces_bias_suffixed_files(self, tmp_path):
        raw = str(tmp_path / "spectrum_0001.fits")
        obs = _make_obs(raw_files=[raw])

        result = await BiasPlugin().execute(obs)

        corrected = result.reduction_data["bias"]["corrected_files"]
        assert corrected == [str(tmp_path / "spectrum_0001_b.fits")]
        assert Path(corrected[0]).is_file()
