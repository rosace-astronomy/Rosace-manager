"""
Tests for the MASTER_DARK sub-plugin (MasterDarkPlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/master_dark/test_master_dark.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.master_dark.master_dark import MasterDarkPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.master_dark.master_dark.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestMasterDarkPlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await MasterDarkPlugin().execute(obs)

        assert result.reduction_data["master_dark"] == {"status": "placeholder"}
