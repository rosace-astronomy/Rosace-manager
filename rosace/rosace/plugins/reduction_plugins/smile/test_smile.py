"""
Tests for the SMILE sub-plugin (SmilePlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/smile/test_smile.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.smile.smile import SmilePlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.smile.smile.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestSmilePlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await SmilePlugin().execute(obs)

        assert result.reduction_data["smile"] == {"status": "placeholder"}
