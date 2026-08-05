"""
Tests du sous-plugin SHARING "blank_share" (BlankSharePlugin).
Lancement : pytest plugins/sharing_plugins/blank_share/test_blank_share.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.sharing_plugins.blank_share.blank_share import BlankSharePlugin


def _make_obs(reduced_files=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if reduced_files is not None:
        obs.reduction_data["extract"] = {"reduced_files": reduced_files}
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """Évite d'attendre le sleep(0.3) simulé par fichier diffusé."""
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr("rosace.plugins.sharing_plugins.blank_share.blank_share.asyncio.sleep", mock)
    return mock


class TestBlankSharePlugin:

    async def test_raises_if_extract_missing(self):
        obs = _make_obs()  # reduction_data == {}, extract n'a jamais tourné
        with pytest.raises(RuntimeError, match="reduced_files"):
            await BlankSharePlugin().execute(obs)

    async def test_raises_if_no_reduced_files(self):
        obs = _make_obs(reduced_files=[])
        with pytest.raises(RuntimeError, match="reduced_files"):
            await BlankSharePlugin().execute(obs)

    async def test_processes_every_reduced_file(self, fast_sleep):
        files = ["/tmp/a_reduced.fits", "/tmp/b_reduced.fits"]
        obs = _make_obs(reduced_files=files)

        result = await BlankSharePlugin().execute(obs)

        assert result is obs
        assert fast_sleep.await_count == len(files)
        assert result.sharing_data["blank_share"] == {"shared_files": files}
