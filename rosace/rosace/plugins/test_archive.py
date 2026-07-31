"""
Tests du plugin ARCHIVE (ArchivePlugin).
Lancement : pytest plugins/test_archive.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.archive import ArchivePlugin


def _make_obs(reduced_files=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if reduced_files is not None:
        obs.reduction_data["extract"] = {"reduced_files": reduced_files}
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """Évite d'attendre le sleep(0.3) simulé par fichier archivé."""
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr("rosace.plugins.archive.asyncio.sleep", mock)
    return mock


class TestArchivePlugin:

    async def test_raises_if_no_reduced_files(self):
        obs = _make_obs(reduced_files=[])
        with pytest.raises(RuntimeError, match="reduction_data"):
            await ArchivePlugin().run(obs)

    async def test_raises_if_reduction_data_empty(self):
        obs = _make_obs()  # reduction_data == {}
        with pytest.raises(RuntimeError, match="reduction_data"):
            await ArchivePlugin().run(obs)

    async def test_processes_every_reduced_file(self, fast_sleep):
        files = ["/tmp/a_reduced.fits", "/tmp/b_reduced.fits"]
        obs = _make_obs(reduced_files=files)

        result = await ArchivePlugin().run(obs)

        assert result is obs
        assert fast_sleep.await_count == len(files)
