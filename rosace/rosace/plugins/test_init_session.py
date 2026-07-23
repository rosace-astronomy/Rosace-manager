"""
Tests du plugin INIT_SESSION (InitSessionPlugin).
Lancement : pytest plugins/test_init_session.py -v
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rosace.models import SessionContext
from rosace.plugins.init_session import InitSessionPlugin


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """Évite d'attendre le sleep(1.0) simulé à chaque test."""
    monkeypatch.setattr(
        "rosace.plugins.init_session.asyncio.sleep", AsyncMock(return_value=None)
    )


class TestInitSessionPlugin:

    async def test_creates_session_directory(self):
        ctx = SessionContext(site_name="St-Pancrasse")
        result = await InitSessionPlugin().run(ctx)

        assert result is ctx  # même objet, enrichi
        assert ctx.session_dir is not None
        assert Path(ctx.session_dir).is_dir()

    async def test_directory_name_contains_date_and_site(self):
        ctx = SessionContext(site_name="St-Pancrasse")
        await InitSessionPlugin().run(ctx)

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert f"{date_str}_St-Pancrasse" in ctx.session_dir

    async def test_idempotent_if_called_twice(self):
        """mkdir(exist_ok=True) : un second appel ne doit pas lever d'erreur."""
        ctx = SessionContext(site_name="St-Pancrasse")
        await InitSessionPlugin().run(ctx)
        first_dir = ctx.session_dir

        ctx2 = SessionContext(site_name="St-Pancrasse")
        await InitSessionPlugin().run(ctx2)

        assert ctx2.session_dir == first_dir  # même nuit, même répertoire

    async def test_preserves_session_id(self):
        ctx = SessionContext(site_name="St-Pancrasse")
        original_id = ctx.session_id
        await InitSessionPlugin().run(ctx)
        assert ctx.session_id == original_id
