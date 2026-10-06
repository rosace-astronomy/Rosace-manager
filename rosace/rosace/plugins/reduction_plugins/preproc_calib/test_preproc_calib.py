"""
Tests for the PREPROC_CALIB sub-plugin (PreprocCalibPlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/preproc_calib/test_preproc_calib.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.preproc_calib.preproc_calib import PreprocCalibPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.preproc_calib.preproc_calib.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestPreprocCalibPlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await PreprocCalibPlugin().execute(obs)

        assert result.reduction_data["preproc_calib"] == {"status": "placeholder"}
