"""
Tests for the WRITE_SPECTRUM sub-plugin (WriteSpectrumPlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/write_spectrum/test_write_spectrum.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.write_spectrum.write_spectrum import WriteSpectrumPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.write_spectrum.write_spectrum.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestWriteSpectrumPlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await WriteSpectrumPlugin().execute(obs)

        assert result.reduction_data["write_spectrum"] == {"status": "placeholder"}
