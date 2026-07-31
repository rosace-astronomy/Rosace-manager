"""
Tests du sous-plugin DARK (DarkPlugin).
Lancement : pytest plugins/reduction_plugins/dark/test_dark.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.dark.dark import DarkPlugin


def _make_obs(bias_corrected_files=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
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
        obs = _make_obs()  # reduction_data == {}, bias n'a jamais tourné
        with pytest.raises(RuntimeError, match="bias"):
            await DarkPlugin().execute(obs)

    async def test_raises_if_no_input_files(self):
        obs = _make_obs(bias_corrected_files=[])
        with pytest.raises(RuntimeError, match="corrected_files"):
            await DarkPlugin().execute(obs)

    async def test_produces_dark_suffixed_files(self, tmp_path):
        bias_corrected = str(tmp_path / "spectrum_0001_b.fits")
        obs = _make_obs(bias_corrected_files=[bias_corrected])

        result = await DarkPlugin().execute(obs)

        assert result.reduction_data["dark"]["corrected_files"] == [str(tmp_path / "spectrum_0001_bd.fits")]
