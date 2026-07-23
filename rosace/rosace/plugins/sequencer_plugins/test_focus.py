"""
Tests du sous-plugin FOCUS (FocusPlugin).
Lancement : pytest plugins/sequencer_plugins/test_focus.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.sequencer_plugins.focus import FocusPlugin


def _make_obs(slew_done=True, last_focus_age_sec=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if slew_done:
        obs.sequencer_data["slew_done"] = True
    if last_focus_age_sec is not None:
        obs.sequencer_data["last_focus_age_sec"] = last_focus_age_sec
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.sequencer_plugins.focus.asyncio.sleep", AsyncMock(return_value=None)
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
        assert obs.sequencer_data["last_focus_age_sec"] == 0

    async def test_skips_if_recently_focused(self):
        obs = _make_obs(last_focus_age_sec=100)
        plugin = FocusPlugin()
        plugin._config = {"skip_if_recent_sec": 600}  # 100s < 600s -> skip

        result = await plugin.execute(obs)

        assert result.sequencer_data["focus_skipped"] is True
        assert "focus_done" not in result.sequencer_data

    async def test_does_not_skip_if_not_recent_enough(self):
        obs = _make_obs(last_focus_age_sec=900)
        plugin = FocusPlugin()
        plugin._config = {"skip_if_recent_sec": 600}  # 900s >= 600s -> ne saute pas

        result = await plugin.execute(obs)

        assert result.sequencer_data["focus_done"] is True
        assert "focus_skipped" not in result.sequencer_data
