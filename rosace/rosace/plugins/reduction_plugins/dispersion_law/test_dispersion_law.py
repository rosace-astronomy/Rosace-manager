"""
Tests for the DISPERSION_LAW sub-plugin (DispersionLawPlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/dispersion_law/test_dispersion_law.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.dispersion_law.dispersion_law import DispersionLawPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.dispersion_law.dispersion_law.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestDispersionLawPlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await DispersionLawPlugin().execute(obs)

        assert result.reduction_data["dispersion_law"] == {"status": "placeholder"}
