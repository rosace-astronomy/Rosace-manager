"""
Tests for the TILT sub-plugin (TiltPlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/tilt/test_tilt.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.tilt.tilt import TiltPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.tilt.tilt.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestTiltPlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await TiltPlugin().execute(obs)

        assert result.reduction_data["tilt"] == {"status": "placeholder"}
