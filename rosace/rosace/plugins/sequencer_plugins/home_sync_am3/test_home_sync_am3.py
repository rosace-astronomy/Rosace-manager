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


HOME_RA, HOME_DEC = 123.456, 45.678


def _make_mount() -> AsyncMock:
    mount = AsyncMock()
    mount.get_home_position.return_value = (HOME_RA, HOME_DEC)
    return mount


def _make_obs(session_id, mount=None):
    obs = Observation(id="obs1", created_at=datetime.now(timezone.utc), session_id=session_id)
    obs.hardware["mount"] = mount if mount is not None else _make_mount()
    return obs


class TestHomeSyncAm3Plugin:

    async def test_runs_park_and_unpark_on_first_call_for_a_session(self):
        mount = _make_mount()
        obs = _make_obs("session-a", mount=mount)

        result = await HomeSyncAm3Plugin().execute(obs)

        mount.park.assert_awaited_once()
        mount.unpark.assert_awaited_once()
        assert result is obs

    async def test_skips_on_second_call_for_the_same_session(self):
        mount = _make_mount()
        obs1 = _make_obs("session-a", mount=mount)
        await HomeSyncAm3Plugin().execute(obs1)

        mount2 = _make_mount()
        obs2 = _make_obs("session-a", mount=mount2)
        await HomeSyncAm3Plugin().execute(obs2)

        mount2.park.assert_not_awaited()
        mount2.unpark.assert_not_awaited()
        mount2.sync_to_coordinates.assert_not_awaited()

    async def test_runs_again_for_a_new_session(self):
        mount1 = _make_mount()
        await HomeSyncAm3Plugin().execute(_make_obs("session-a", mount=mount1))

        mount2 = _make_mount()
        obs2 = _make_obs("session-b", mount=mount2)
        await HomeSyncAm3Plugin().execute(obs2)

        mount2.park.assert_awaited_once()
        mount2.unpark.assert_awaited_once()
        mount2.sync_to_coordinates.assert_awaited_once()

    async def test_park_unpark_sync_order(self):
        mount = _make_mount()
        obs = _make_obs("session-a", mount=mount)

        parent = AsyncMock()
        parent.attach_mock(mount.park, "park")
        parent.attach_mock(mount.unpark, "unpark")
        parent.attach_mock(mount.get_home_position, "get_home_position")
        parent.attach_mock(mount.sync_to_coordinates, "sync_to_coordinates")

        await HomeSyncAm3Plugin().execute(obs)

        call_names = [c[0] for c in parent.mock_calls]
        assert call_names == ["park", "unpark", "get_home_position", "sync_to_coordinates"]

    async def test_syncs_to_the_mount_reported_home_position(self):
        mount = _make_mount()
        obs = _make_obs("session-a", mount=mount)

        await HomeSyncAm3Plugin().execute(obs)

        mount.sync_to_coordinates.assert_awaited_once_with(HOME_RA, HOME_DEC)

    async def test_sync_failure_does_not_raise_and_still_completes(self, monkeypatch):
        mount = _make_mount()
        mount.sync_to_coordinates.side_effect = RuntimeError("indi boom")
        obs = _make_obs("session-a", mount=mount)

        result = await HomeSyncAm3Plugin().execute(obs)

        assert result is obs

    async def test_sync_failure_still_marks_the_session_as_homed(self, monkeypatch):
        # A failed sync must not leave the module-level gate unset -- otherwise a
        # deterministic failure would retry (and re-fail) forever on every SCHEDULER
        # restart, blocking the whole session.
        mount = _make_mount()
        mount.sync_to_coordinates.side_effect = RuntimeError("indi boom")
        obs1 = _make_obs("session-a", mount=mount)
        await HomeSyncAm3Plugin().execute(obs1)

        mount2 = _make_mount()
        obs2 = _make_obs("session-a", mount=mount2)
        await HomeSyncAm3Plugin().execute(obs2)

        mount2.park.assert_not_awaited()

    async def test_sync_disabled_skips_get_home_position_and_sync(self, monkeypatch):
        monkeypatch.setattr(hsa_mod, "SYNC_TO_COMPUTED_HOME", False)
        mount = _make_mount()
        obs = _make_obs("session-a", mount=mount)

        result = await HomeSyncAm3Plugin().execute(obs)

        mount.park.assert_awaited_once()
        mount.unpark.assert_awaited_once()
        mount.get_home_position.assert_not_awaited()
        mount.sync_to_coordinates.assert_not_awaited()
        assert result is obs
