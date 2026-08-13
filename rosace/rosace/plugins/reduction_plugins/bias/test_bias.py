"""
Tests du sous-plugin BIAS (BiasPlugin).
Lancement : pytest plugins/reduction_plugins/bias/test_bias.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.bias.bias import BiasPlugin


def _make_obs(obs_dir=None, raw_files=None) -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir) if obs_dir else None,
    )
    if raw_files is not None:
        obs.sequencer_data["star_spectra_images"] = raw_files
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.bias.bias.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestBiasPlugin:

    async def test_raises_if_star_spectra_images_missing(self):
        obs = _make_obs()  # sequencer_data == {}, aucun sous-plugin d'acquisition n'a tourné
        with pytest.raises(RuntimeError, match="star_spectra_images"):
            await BiasPlugin().execute(obs)

    async def test_raises_if_no_raw_files(self):
        obs = _make_obs(raw_files=[])
        with pytest.raises(RuntimeError, match="star_spectra_images"):
            await BiasPlugin().execute(obs)

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None, raw_files=["spectrum_0001.fits"])
        with pytest.raises(RuntimeError, match="obs_dir"):
            await BiasPlugin().execute(obs)

    async def test_produces_bias_suffixed_files(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, raw_files=["spectrum_0001.fits"])

        result = await BiasPlugin().execute(obs)

        corrected = result.reduction_data["bias"]["corrected_files"]
        assert corrected == ["spectrum_0001_b.fits"]
        assert (tmp_path / corrected[0]).is_file()
