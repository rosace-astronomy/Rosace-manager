"""
Tests for the PREPROC_TARGET sub-plugin (PreprocTargetPlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/preproc_target/test_preproc_target.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.preproc_target.preproc_target import PreprocTargetPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.preproc_target.preproc_target.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestPreprocTargetPlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await PreprocTargetPlugin().execute(obs)

        assert result.reduction_data["preproc_target"] == {"status": "placeholder"}
