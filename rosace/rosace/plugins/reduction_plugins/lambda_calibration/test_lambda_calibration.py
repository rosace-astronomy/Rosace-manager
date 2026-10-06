"""
Tests for the LAMBDA_CALIBRATION sub-plugin (LambdaCalibrationPlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/lambda_calibration/test_lambda_calibration.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.lambda_calibration.lambda_calibration import LambdaCalibrationPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.lambda_calibration.lambda_calibration.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestLambdaCalibrationPlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await LambdaCalibrationPlugin().execute(obs)

        assert result.reduction_data["lambda_calibration"] == {"status": "placeholder"}
