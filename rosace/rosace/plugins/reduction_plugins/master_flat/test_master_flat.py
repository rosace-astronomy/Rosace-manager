"""
Tests for the MASTER_FLAT sub-plugin (MasterFlatPlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/master_flat/test_master_flat.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.master_flat.master_flat import MasterFlatPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.master_flat.master_flat.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestMasterFlatPlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await MasterFlatPlugin().execute(obs)

        assert result.reduction_data["master_flat"] == {"status": "placeholder"}
