"""
Tests du sous-plugin EXTRACT (ExtractPlugin).
Lancement : pytest plugins/reduction_plugins/test_extract.py -v
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.extract import ExtractPlugin


def _make_obs(raw_files=None, bias_corrected_files=None, dark_corrected_files=None,
              flat_corrected_files=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if raw_files is not None:
        obs.sequencer_data["raw_files"] = raw_files
    if bias_corrected_files is not None:
        obs.reduction_data["bias_corrected_files"] = bias_corrected_files
    if dark_corrected_files is not None:
        obs.reduction_data["dark_corrected_files"] = dark_corrected_files
    if flat_corrected_files is not None:
        obs.reduction_data["flat_corrected_files"] = flat_corrected_files
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.extract.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestExtractPlugin:

    async def test_raises_if_no_input_files(self):
        obs = _make_obs()
        with pytest.raises(RuntimeError, match="aucun fichier calibré"):
            await ExtractPlugin().execute(obs)

    async def test_falls_back_to_raw_files(self, tmp_path):
        raw = str(tmp_path / "spectrum_0001.fits")
        obs = _make_obs(raw_files=[raw])

        result = await ExtractPlugin().execute(obs)

        assert result.reduction_data["reduced_files"] == [str(tmp_path / "spectrum_0001_reduced.fits")]
        assert result.reduction_data["extract_done"] is True

    async def test_prefers_flat_over_everything_else(self, tmp_path):
        raw = str(tmp_path / "spectrum_0001.fits")
        bias = str(tmp_path / "spectrum_0001_b.fits")
        dark = str(tmp_path / "spectrum_0001_bd.fits")
        flat = str(tmp_path / "spectrum_0001_f.fits")
        obs = _make_obs(
            raw_files=[raw], bias_corrected_files=[bias],
            dark_corrected_files=[dark], flat_corrected_files=[flat],
        )

        result = await ExtractPlugin().execute(obs)

        assert result.reduction_data["reduced_files"] == [str(tmp_path / "spectrum_0001_reduced.fits")]

    async def test_output_files_exist_on_disk(self, tmp_path):
        raw = str(tmp_path / "spectrum_0001.fits")
        obs = _make_obs(raw_files=[raw])

        result = await ExtractPlugin().execute(obs)

        assert Path(result.reduction_data["reduced_files"][0]).is_file()
