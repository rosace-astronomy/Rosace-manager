"""
Tests for the HOME_SYNC_AM3 sub-plugin (HomeSyncAm3Plugin), indi backend.
Covers the once-per-session gating (keyed on session_id) and the
park/unpark/sync call sequence.
Run: pytest plugins/sequencer_plugins/home_sync_am3/test_home_sync_am3.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation
from rosace.plugins.sequencer_plugins.home_sync_am3 import home_sync_am3 as hsa_mod
from rosace.plugins.sequencer_plugins.home_sync_am3.home_sync_am3 import HomeSyncAm3Plugin


@pytest.fixture(autouse=True)
def reset_module_state():
    """The 'already homed this session' flag is module-level, shared across
    tests running in the same process -- reset it before and after every
    test so tests don't leak state into each other."""
    hsa_mod._last_homed_session_id = None
    yield
    hsa_mod._last_homed_session_id = None


def _make_obs(session_id, mount=None):
    obs = Observation(id="obs1", created_at=datetime.now(timezone.utc), session_id=session_id)
    obs.hardware["mount"] = mount if mount is not None else AsyncMock()
    return obs


class TestHomeSyncAm3Plugin:

    async def test_runs_park_unpark_and_sync_on_first_call_for_a_session(self):
        mount = AsyncMock()
        mount.get_home_position.return_value = (123.4, 89.5)
        obs = _make_obs("session-a", mount=mount)

        result = await HomeSyncAm3Plugin().execute(obs)

        mount.park.assert_awaited_once()
        mount.unpark.assert_awaited_once()
        mount.get_home_position.assert_awaited_once()
        mount.sync_to_coordinates.assert_awaited_once_with(123.4, 89.5)
        assert result is obs

    async def test_skips_on_second_call_for_the_same_session(self):
        mount = AsyncMock()
        mount.get_home_position.return_value = (123.4, 89.5)
        obs1 = _make_obs("session-a", mount=mount)
        await HomeSyncAm3Plugin().execute(obs1)

        mount2 = AsyncMock()
        obs2 = _make_obs("session-a", mount=mount2)
        await HomeSyncAm3Plugin().execute(obs2)

        mount2.park.assert_not_awaited()
        mount2.unpark.assert_not_awaited()
        mount2.sync_to_coordinates.assert_not_awaited()

    async def test_runs_again_for_a_new_session(self):
        mount1 = AsyncMock()
        mount1.get_home_position.return_value = (123.4, 89.5)
        await HomeSyncAm3Plugin().execute(_make_obs("session-a", mount=mount1))

        mount2 = AsyncMock()
        mount2.get_home_position.return_value = (10.0, 89.0)
        obs2 = _make_obs("session-b", mount=mount2)
        await HomeSyncAm3Plugin().execute(obs2)

        mount2.park.assert_awaited_once()
        mount2.unpark.assert_awaited_once()
        mount2.sync_to_coordinates.assert_awaited_once_with(10.0, 89.0)

    async def test_park_before_unpark_before_sync_order(self):
        mount = AsyncMock()
        mount.get_home_position.return_value = (1.0, 2.0)
        obs = _make_obs("session-a", mount=mount)

        parent = AsyncMock()
        parent.attach_mock(mount.park, "park")
        parent.attach_mock(mount.unpark, "unpark")
        parent.attach_mock(mount.sync_to_coordinates, "sync_to_coordinates")

        await HomeSyncAm3Plugin().execute(obs)

        call_names = [c[0] for c in parent.mock_calls]
        assert call_names == ["park", "unpark", "sync_to_coordinates"]
