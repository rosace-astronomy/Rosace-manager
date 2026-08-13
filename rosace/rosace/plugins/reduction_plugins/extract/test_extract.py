"""
Tests du sous-plugin EXTRACT (ExtractPlugin).
Lancement : pytest plugins/reduction_plugins/extract/test_extract.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.extract.extract import ExtractPlugin


def _make_obs(obs_dir=None, flat_corrected_files=None) -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir) if obs_dir else None,
    )
    if flat_corrected_files is not None:
        obs.reduction_data["flat"] = {"corrected_files": flat_corrected_files}
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.extract.extract.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestExtractPlugin:

    async def test_raises_if_flat_missing(self):
        obs = _make_obs()  # reduction_data == {}, flat n'a jamais tourné
        with pytest.raises(RuntimeError, match="flat"):
            await ExtractPlugin().execute(obs)

    async def test_raises_if_no_input_files(self):
        obs = _make_obs(flat_corrected_files=[])
        with pytest.raises(RuntimeError, match="corrected_files"):
            await ExtractPlugin().execute(obs)

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None, flat_corrected_files=["spectrum_0001_bdf.fits"])
        with pytest.raises(RuntimeError, match="obs_dir"):
            await ExtractPlugin().execute(obs)

    async def test_produces_reduced_files(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, flat_corrected_files=["spectrum_0001_bdf.fits"])

        result = await ExtractPlugin().execute(obs)

        assert result.reduction_data["extract"]["reduced_files"] == ["spectrum_0001_reduced.fits"]

    async def test_output_files_exist_on_disk(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, flat_corrected_files=["spectrum_0001_bdf.fits"])

        result = await ExtractPlugin().execute(obs)

        reduced_name = result.reduction_data["extract"]["reduced_files"][0]
        assert (tmp_path / reduced_name).is_file()
