"""
Tests for the SHARING "blank_share" sub-plugin (BlankSharePlugin).
Run: pytest plugins/sharing_plugins/blank_share/test_blank_share.py -v
"""

import logging
from datetime import datetime, timezone

from rosace.models import Observation
from rosace.plugins.sharing_plugins.blank_share.blank_share import BlankSharePlugin


def _make_obs() -> Observation:
    return Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        yaml_file="20261004T210000_Vega.yaml",
    )


class TestBlankSharePlugin:

    async def test_logs_the_dossier_without_reading_any_casier(self, caplog):
        obs = _make_obs()  # reduction_data == {}: no REDUCTION sub-plugin output is needed

        with caplog.at_level(logging.INFO, logger="spectro.subplugin.sharing.blank_share"):
            result = await BlankSharePlugin().execute(obs)

        assert result is obs
        assert any("20261004T210000_Vega.yaml" in r.message for r in caplog.records)
        assert result.sharing_data == {}
