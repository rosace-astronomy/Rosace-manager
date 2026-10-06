"""
Tests for the STACK sub-plugin (StackPlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/stack/test_stack.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.stack.stack import StackPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.stack.stack.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestStackPlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await StackPlugin().execute(obs)

        assert result.reduction_data["stack"] == {"status": "placeholder"}
