"""
Tests for the CLOSE_SESSION plugin (CloseSessionPlugin).
Run: pytest plugins/test_close_session.py -v
"""

from unittest.mock import AsyncMock

import pytest

from rosace.models import SessionContext
from rosace.plugins.close_session import CloseSessionPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """Avoids waiting out the simulated sleep(7.0) (telescope parking)."""
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr("rosace.plugins.close_session.asyncio.sleep", mock)
    return mock


class TestCloseSessionPlugin:

    async def test_returns_same_context(self):
        ctx = SessionContext(site_name="St-Pancrasse")
        result = await CloseSessionPlugin().run(ctx)
        assert result is ctx

    async def test_preserves_session_id(self):
        ctx = SessionContext(site_name="St-Pancrasse")
        original_id = ctx.session_id
        await CloseSessionPlugin().run(ctx)
        assert ctx.session_id == original_id

    async def test_waits_before_returning(self, fast_sleep):
        ctx = SessionContext(site_name="St-Pancrasse")
        await CloseSessionPlugin().run(ctx)
        fast_sleep.assert_awaited_once()

    async def test_disconnects_every_hardware_adapter(self):
        ctx = SessionContext(site_name="St-Pancrasse")
        ctx.hardware = {"mount": AsyncMock(), "science_camera": AsyncMock()}

        await CloseSessionPlugin().run(ctx)

        ctx.hardware["mount"].disconnect.assert_awaited_once()
        ctx.hardware["science_camera"].disconnect.assert_awaited_once()

    async def test_one_disconnect_failure_does_not_block_the_others(self):
        failing = AsyncMock()
        failing.disconnect.side_effect = RuntimeError("boom")
        ctx = SessionContext(site_name="St-Pancrasse")
        ctx.hardware = {"mount": failing, "science_camera": AsyncMock()}

        await CloseSessionPlugin().run(ctx)  # must not raise

        ctx.hardware["science_camera"].disconnect.assert_awaited_once()

    async def test_turns_off_cooling_for_every_camera_that_has_it(self):
        ctx = SessionContext(site_name="St-Pancrasse")
        ctx.hardware = {"science_camera": AsyncMock(), "guiding_camera": AsyncMock(), "mount": AsyncMock()}
        ctx.hardware_config = {
            "science_camera": {"cooling": True},
            "guiding_camera": {"cooling": True},
            "mount": {"backlash_offset_deg": 2},
        }

        await CloseSessionPlugin().run(ctx)

        ctx.hardware["science_camera"].set_cooling_off.assert_awaited_once()
        ctx.hardware["guiding_camera"].set_cooling_off.assert_awaited_once()

    async def test_cooling_turned_off_before_devices_disconnect(self):
        """The cooler command needs a live connection -- must happen before disconnect()."""
        calls = []
        camera = AsyncMock()
        camera.set_cooling_off.side_effect = lambda: calls.append("cooling_off")
        camera.disconnect.side_effect = lambda: calls.append("disconnect")
        ctx = SessionContext(site_name="St-Pancrasse")
        ctx.hardware = {"science_camera": camera}
        ctx.hardware_config = {"science_camera": {"cooling": True}}

        await CloseSessionPlugin().run(ctx)

        assert calls == ["cooling_off", "disconnect"]

    async def test_parks_mount_when_present(self):
        ctx = SessionContext(site_name="St-Pancrasse")
        ctx.hardware = {"mount": AsyncMock(), "science_camera": AsyncMock()}

        await CloseSessionPlugin().run(ctx)

        ctx.hardware["mount"].park.assert_awaited_once()

    async def test_no_park_call_when_no_mount_present(self):
        ctx = SessionContext(site_name="St-Pancrasse")
        ctx.hardware = {"science_camera": AsyncMock()}

        await CloseSessionPlugin().run(ctx)  # must not raise

    async def test_park_failure_does_not_block_disconnects(self):
        mount = AsyncMock()
        mount.park.side_effect = RuntimeError("boom")
        ctx = SessionContext(site_name="St-Pancrasse")
        ctx.hardware = {"mount": mount, "science_camera": AsyncMock()}

        await CloseSessionPlugin().run(ctx)  # must not raise

        mount.disconnect.assert_awaited_once()
        ctx.hardware["science_camera"].disconnect.assert_awaited_once()

    async def test_mount_parked_before_disconnect(self):
        calls = []
        mount = AsyncMock()
        mount.park.side_effect = lambda: calls.append("park")
        mount.disconnect.side_effect = lambda: calls.append("disconnect")
        ctx = SessionContext(site_name="St-Pancrasse")
        ctx.hardware = {"mount": mount}

        await CloseSessionPlugin().run(ctx)

        assert calls == ["park", "disconnect"]
