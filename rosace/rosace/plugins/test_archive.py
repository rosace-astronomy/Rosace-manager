"""
Tests for the ARCHIVE plugin (ArchivePlugin).
Run: pytest plugins/test_archive.py -v
"""

import logging
from datetime import datetime, timezone

from rosace.models import Observation
from rosace.plugins.archive import ArchivePlugin


def _make_obs() -> Observation:
    return Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        yaml_file="20261004T210000_Vega.yaml",
    )


class TestArchivePlugin:

    async def test_logs_the_dossier_without_reading_any_casier(self, caplog):
        obs = _make_obs()  # reduction_data == {}: no sub-plugin output is needed

        with caplog.at_level(logging.INFO, logger="spectro.plugin.archive"):
            result = await ArchivePlugin().run(obs)

        assert result is obs
        assert any("20261004T210000_Vega.yaml" in r.message for r in caplog.records)
