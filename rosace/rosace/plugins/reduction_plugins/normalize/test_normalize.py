"""
Tests for the NORMALIZE sub-plugin (NormalizePlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/normalize/test_normalize.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.normalize.normalize import NormalizePlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.normalize.normalize.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestNormalizePlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await NormalizePlugin().execute(obs)

        assert result.reduction_data["normalize"] == {"status": "placeholder"}
