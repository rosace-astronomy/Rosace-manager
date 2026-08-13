"""
Tests du plugin INIT_SESSION (InitSessionPlugin).
Lancement : pytest plugins/test_init_session.py -v
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rosace.models import SessionContext
from rosace.plugins.hardware_connectivity import ConnectedHardware
from rosace.plugins.init_session import InitSessionPlugin
from rosace.time_helpers import astronomical_day_local_date


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """Évite d'attendre le sleep(1.0) simulé à chaque test."""
    monkeypatch.setattr(
        "rosace.plugins.init_session.asyncio.sleep", AsyncMock(return_value=None)
    )


@pytest.fixture(autouse=True)
def mock_hardware_check(monkeypatch):
    """Par défaut, la connexion matérielle réussit instantanément et renvoie un
    device — les tests qui portent sur autre chose n'ont pas à parler à du vrai
    matériel INDI."""
    mock = AsyncMock(return_value=ConnectedHardware(
        adapters={"mount": object()}, hardware_config={"mount": {"some_param": 42}},
    ))
    monkeypatch.setattr("rosace.plugins.init_session.connect_session_hardware", mock)
    return mock


class TestInitSessionPlugin:

    async def test_creates_session_directory(self, tmp_data_dir):
        ctx = SessionContext(site_name="St-Pancrasse")
        result = await InitSessionPlugin().run(ctx)

        assert result is ctx  # même objet, enrichi
        assert ctx.session_dir is not None
        assert Path(ctx.session_dir).is_dir()
        # Isolation : on écrit bien sous le répertoire temporaire du test, jamais sous le vrai ~/.rosace.
        assert Path(ctx.session_dir).is_relative_to(tmp_data_dir)

    async def test_directory_name_contains_date_and_site(self):
        ctx = SessionContext(site_name="St-Pancrasse")
        await InitSessionPlugin().run(ctx)

        date_str = astronomical_day_local_date()
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


class TestSessionDirectoryUsesAstronomicalDay:
    """Regression check: the session directory must switch to the next night at
    local noon (astronomical day), not at UTC midnight — a historical bug, never
    wired to the same logic as database.py::astronomical_day_start_utc."""

    async def test_directory_keeps_previous_date_just_before_local_noon(self, monkeypatch):
        system_tz = datetime.now().astimezone().tzinfo
        local_date = datetime.now(system_tz).date()
        just_before_noon = datetime(local_date.year, local_date.month, local_date.day, 11, 30, tzinfo=system_tz)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return just_before_noon.astimezone(tz) if tz else just_before_noon

        monkeypatch.setattr("rosace.time_helpers.datetime", _FrozenDatetime)

        ctx = SessionContext(site_name="St-Pancrasse")
        await InitSessionPlugin().run(ctx)

        expected_date = (local_date - timedelta(days=1)).strftime("%Y-%m-%d")
        assert f"{expected_date}_St-Pancrasse" in ctx.session_dir


class TestHardwareConnectivityDispatch:
    """Le check de connectivité matérielle n'est déclenché que si sequencer_backend=indi."""

    async def test_calls_check_when_backend_is_indi(self, monkeypatch, mock_hardware_check):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "indi")
        ctx = SessionContext(site_name="St-Pancrasse")

        await InitSessionPlugin().run(ctx)

        mock_hardware_check.assert_awaited_once()

    async def test_skips_check_when_backend_is_internal(self, monkeypatch, mock_hardware_check):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "internal")
        ctx = SessionContext(site_name="St-Pancrasse")

        await InitSessionPlugin().run(ctx)

        mock_hardware_check.assert_not_awaited()

    async def test_logs_demo_mode_when_backend_is_internal(self, monkeypatch, mock_hardware_check, caplog):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "internal")
        ctx = SessionContext(site_name="St-Pancrasse")

        with caplog.at_level("INFO", logger="spectro.plugin.init_session"):
            await InitSessionPlugin().run(ctx)

        assert any("DEMO mode" in record.message for record in caplog.records)

    async def test_no_demo_mode_log_when_backend_is_indi(self, monkeypatch, mock_hardware_check, caplog):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "indi")
        ctx = SessionContext(site_name="St-Pancrasse")

        with caplog.at_level("INFO", logger="spectro.plugin.init_session"):
            await InitSessionPlugin().run(ctx)

        assert not any("DEMO mode" in record.message for record in caplog.records)

    async def test_sets_ctx_hardware_when_backend_is_indi(self, monkeypatch, mock_hardware_check):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "indi")
        ctx = SessionContext(site_name="St-Pancrasse")

        await InitSessionPlugin().run(ctx)

        assert set(ctx.hardware) == {"mount"}

    async def test_sets_ctx_hardware_config_when_backend_is_indi(self, monkeypatch, mock_hardware_check):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "indi")
        ctx = SessionContext(site_name="St-Pancrasse")

        await InitSessionPlugin().run(ctx)

        assert ctx.hardware_config == {"mount": {"some_param": 42}}

    async def test_propagates_hardware_connectivity_failure(self, monkeypatch, mock_hardware_check):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "indi")
        mock_hardware_check.side_effect = RuntimeError("device 'mount': timeout")
        ctx = SessionContext(site_name="St-Pancrasse")

        with pytest.raises(RuntimeError, match="mount"):
            await InitSessionPlugin().run(ctx)
