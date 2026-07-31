"""
Tests du sous-plugin FLAT (FlatPlugin).
Lancement : pytest plugins/reduction_plugins/test_flat.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.flat.flat import FlatPlugin


def _make_obs(raw_files=None, bias_corrected_files=None, dark_corrected_files=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if raw_files is not None:
        obs.sequencer_data["raw_files"] = raw_files
    if bias_corrected_files is not None:
        obs.reduction_data["bias_corrected_files"] = bias_corrected_files
    if dark_corrected_files is not None:
        obs.reduction_data["dark_corrected_files"] = dark_corrected_files
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.flat.flat.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestFlatPlugin:

    async def test_raises_if_no_input_files(self):
        obs = _make_obs()
        with pytest.raises(RuntimeError, match="aucun fichier"):
            await FlatPlugin().execute(obs)

    async def test_falls_back_to_raw_files(self, tmp_path):
        raw = str(tmp_path / "spectrum_0001.fits")
        obs = _make_obs(raw_files=[raw])

        result = await FlatPlugin().execute(obs)

        assert result.reduction_data["flat_corrected_files"] == [str(tmp_path / "spectrum_0001_f.fits")]
        assert result.reduction_data["flat_done"] is True

    async def test_prefers_dark_over_bias_and_raw(self, tmp_path):
        raw = str(tmp_path / "spectrum_0001.fits")
        bias = str(tmp_path / "spectrum_0001_b.fits")
        dark = str(tmp_path / "spectrum_0001_bd.fits")
        obs = _make_obs(raw_files=[raw], bias_corrected_files=[bias], dark_corrected_files=[dark])

        result = await FlatPlugin().execute(obs)

        assert result.reduction_data["flat_corrected_files"] == [str(tmp_path / "spectrum_0001_f.fits")]

    async def test_prefers_bias_over_raw_when_no_dark(self, tmp_path):
        raw = str(tmp_path / "spectrum_0001.fits")
        bias = str(tmp_path / "other_name_b.fits")
        obs = _make_obs(raw_files=[raw], bias_corrected_files=[bias])

        result = await FlatPlugin().execute(obs)

        assert result.reduction_data["flat_corrected_files"] == [str(tmp_path / "other_name_f.fits")]
