"""
Tests for the MASTER_BIAS sub-plugin (MasterBiasPlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/master_bias/test_master_bias.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.master_bias.master_bias import MasterBiasPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.master_bias.master_bias.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestMasterBiasPlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await MasterBiasPlugin().execute(obs)

        assert result.reduction_data["master_bias"] == {"status": "placeholder"}
