"""
Tests du plugin CLOSE_SESSION (CloseSessionPlugin).
Lancement : pytest plugins/test_close_session.py -v
"""

from unittest.mock import AsyncMock

import pytest

from rosace.models import SessionContext
from rosace.plugins.close_session import CloseSessionPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """Évite d'attendre le sleep(7.0) simulé (stationnement télescope)."""
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

        await CloseSessionPlugin().run(ctx)  # ne doit pas lever

        ctx.hardware["science_camera"].disconnect.assert_awaited_once()
