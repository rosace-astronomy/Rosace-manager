"""
Tests for the FOCUS sub-plugin (FocusPlugin), internal backend.
Run: pytest plugins/sequencer_plugins/focus/test_focus_internal.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.sequencer_plugins.focus.focus_internal import FocusPlugin


def _make_obs() -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.sequencer_plugins.focus.focus_internal.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestFocusPlugin:

    async def test_sets_focus_with_simulated_values(self):
        obs = _make_obs()
        result = await FocusPlugin().execute(obs)

        assert result is obs
        assert obs.sequencer_data["focus"] == {"position_um": 12450, "fwhm_px": 2.3}
