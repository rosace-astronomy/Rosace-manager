"""
Tests du sous-plugin FOCUS (FocusPlugin), backend indi — placeholder.
Vérifie uniquement la structure (clés posées), pas de comportement matériel réel.
Lancement : pytest plugins/sequencer_plugins/focus/test_focus.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.sequencer_plugins.focus.focus import FocusPlugin


def _make_obs(slew_done=True) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if slew_done:
        obs.sequencer_data["slew"] = {"target": "Vega"}
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.sequencer_plugins.focus.focus.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestFocusPlugin:

    async def test_raises_if_slew_not_done(self):
        obs = _make_obs(slew_done=False)
        with pytest.raises(RuntimeError, match="slew"):
            await FocusPlugin().execute(obs)

    async def test_sets_focus_placeholder_values(self):
        obs = _make_obs()
        result = await FocusPlugin().execute(obs)

        assert result is obs
        assert obs.sequencer_data["focus"] == {"position_um": None, "fwhm_px": None}
