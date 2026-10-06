"""
Tests for the PLOT_SPECTRUM sub-plugin (PlotSpectrumPlugin) — placeholder for now.
Run: pytest plugins/reduction_plugins/plot_spectrum/test_plot_spectrum.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.reduction_plugins.plot_spectrum.plot_spectrum import PlotSpectrumPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.reduction_plugins.plot_spectrum.plot_spectrum.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestPlotSpectrumPlugin:

    async def test_records_placeholder_casier(self):
        obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")

        result = await PlotSpectrumPlugin().execute(obs)

        assert result.reduction_data["plot_spectrum"] == {"status": "placeholder"}
