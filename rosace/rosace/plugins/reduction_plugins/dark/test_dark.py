"""
Tests for the DARK sub-plugin (DarkPlugin).
Run: pytest plugins/reduction_plugins/dark/test_dark.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.dark.dark import DarkPlugin


def _make_obs(obs_dir=None, bias_corrected_files=None) -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir) if obs_dir else None,
    )
    if bias_corrected_files is not None:
        obs.reduction_data["bias"] = {"corrected_files": bias_corrected_files}
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.dark.dark.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestDarkPlugin:

    async def test_raises_if_bias_missing(self):
        obs = _make_obs()  # reduction_data == {}, bias has never run
        with pytest.raises(RuntimeError, match="bias"):
            await DarkPlugin().execute(obs)

    async def test_raises_if_no_input_files(self):
        obs = _make_obs(bias_corrected_files=[])
        with pytest.raises(RuntimeError, match="corrected_files"):
            await DarkPlugin().execute(obs)

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None, bias_corrected_files=["spectrum_0001_b.fits"])
        with pytest.raises(RuntimeError, match="obs_dir"):
            await DarkPlugin().execute(obs)

    async def test_produces_dark_suffixed_files(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, bias_corrected_files=["spectrum_0001_b.fits"])

        result = await DarkPlugin().execute(obs)

        assert result.reduction_data["dark"]["corrected_files"] == ["spectrum_0001_bd.fits"]
