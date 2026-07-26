"""
Tests du sous-plugin FOCUS (FocusPlugin), backend internal.
Lancement : pytest plugins/sequencer_plugins/internal/test_focus.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.sequencer_plugins.internal.focus import FocusPlugin


def _make_obs(slew_done=True) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if slew_done:
        obs.sequencer_data["slew_done"] = True
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.sequencer_plugins.internal.focus.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestFocusPlugin:

    async def test_raises_if_slew_not_done(self):
        obs = _make_obs(slew_done=False)
        with pytest.raises(RuntimeError, match="slew_done"):
            await FocusPlugin().execute(obs)

    async def test_sets_focus_done_with_simulated_values(self):
        obs = _make_obs()
        result = await FocusPlugin().execute(obs)

        assert result is obs
        assert obs.sequencer_data["focus_done"] is True
        assert obs.sequencer_data["focus_position_um"] == 12450
        assert obs.sequencer_data["focus_fwhm_px"] == 2.3
