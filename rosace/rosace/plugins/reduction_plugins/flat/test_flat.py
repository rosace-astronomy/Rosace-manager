"""
Tests for the FLAT sub-plugin (FlatPlugin).
Run: pytest plugins/reduction_plugins/flat/test_flat.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.flat.flat import FlatPlugin


def _make_obs(obs_dir=None, dark_corrected_files=None) -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir) if obs_dir else None,
    )
    if dark_corrected_files is not None:
        obs.reduction_data["dark"] = {"corrected_files": dark_corrected_files}
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.flat.flat.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestFlatPlugin:

    async def test_raises_if_dark_missing(self):
        obs = _make_obs()  # reduction_data == {}, dark has never run
        with pytest.raises(RuntimeError, match="dark"):
            await FlatPlugin().execute(obs)

    async def test_raises_if_no_input_files(self):
        obs = _make_obs(dark_corrected_files=[])
        with pytest.raises(RuntimeError, match="corrected_files"):
            await FlatPlugin().execute(obs)

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None, dark_corrected_files=["spectrum_0001_bd.fits"])
        with pytest.raises(RuntimeError, match="obs_dir"):
            await FlatPlugin().execute(obs)

    async def test_produces_flat_suffixed_files(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, dark_corrected_files=["spectrum_0001_bd.fits"])

        result = await FlatPlugin().execute(obs)

        assert result.reduction_data["flat"]["corrected_files"] == ["spectrum_0001_bdf.fits"]
