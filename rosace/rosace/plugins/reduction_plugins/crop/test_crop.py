"""
Tests for the CROP sub-plugin (CropPlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/crop/test_crop.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.crop.crop import CropPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.crop.crop.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestCropPlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await CropPlugin().execute(obs)

        assert result.reduction_data["crop"] == {"status": "placeholder"}
