"""
Tests for the INIT_SESSION plugin (InitSessionPlugin).
Run: pytest plugins/test_init_session.py -v
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
    """Avoids waiting out the simulated sleep(1.0) on every test."""
    monkeypatch.setattr(
        "rosace.plugins.init_session.asyncio.sleep", AsyncMock(return_value=None)
    )


@pytest.fixture(autouse=True)
def mock_hardware_check(monkeypatch):
    """By default, the hardware connection succeeds instantly and returns a
    device — tests concerned with something else don't have to talk to real
    INDI hardware. AsyncMock, not a plain object(): INIT_SESSION now calls
    mount.unpark() generically (see below), which a bare object() has no
    method for."""
    mock = AsyncMock(return_value=ConnectedHardware(
        adapters={"mount": AsyncMock()}, hardware_config={"mount": {"some_param": 42}},
    ))
    monkeypatch.setattr("rosace.plugins.init_session.connect_session_hardware", mock)
    return mock


class TestInitSessionPlugin:

    async def test_creates_session_directory(self, tmp_data_dir):
        ctx = SessionContext(site_name="St-Pancrasse")
        result = await InitSessionPlugin().run(ctx)

        assert result is ctx  # same object, enriched
        assert ctx.session_dir is not None
        assert Path(ctx.session_dir).is_dir()
        # Isolation: written under the test's temporary directory, never under the real ~/.rosace.
        assert Path(ctx.session_dir).is_relative_to(tmp_data_dir)

    async def test_directory_name_contains_date_and_site(self):
        ctx = SessionContext(site_name="St-Pancrasse")
        await InitSessionPlugin().run(ctx)

        date_str = astronomical_day_local_date()
        assert f"{date_str}_St-Pancrasse" in ctx.session_dir

    async def test_idempotent_if_called_twice(self):
        """mkdir(exist_ok=True): a second call must not raise."""
        ctx = SessionContext(site_name="St-Pancrasse")
        await InitSessionPlugin().run(ctx)
        first_dir = ctx.session_dir

        ctx2 = SessionContext(site_name="St-Pancrasse")
        await InitSessionPlugin().run(ctx2)

        assert ctx2.session_dir == first_dir  # same night, same directory

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
    """The hardware connectivity check only runs when sequencer_backend=indi."""

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


class TestMountUnpark:
    """Generic, mount-agnostic unpark right after connecting -- symmetric to
    CLOSE_SESSION's own generic park(). Not gated behind sequencer_prefix/
    home_sync_am3: a mount left parked by a previous session (or never
    unparked at all) can't be slewed at all until this runs."""

    async def test_unparks_the_mount_when_backend_is_indi(self, monkeypatch, mock_hardware_check):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "indi")
        ctx = SessionContext(site_name="St-Pancrasse")

        await InitSessionPlugin().run(ctx)

        ctx.hardware["mount"].unpark.assert_awaited_once_with()

    async def test_skips_unpark_when_backend_is_internal(self, monkeypatch, mock_hardware_check):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "internal")
        ctx = SessionContext(site_name="St-Pancrasse")

        await InitSessionPlugin().run(ctx)

        mock_hardware_check.assert_not_awaited()

    async def test_no_crash_when_no_mount_declared(self, monkeypatch):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "indi")
        monkeypatch.setattr(
            "rosace.plugins.init_session.connect_session_hardware",
            AsyncMock(return_value=ConnectedHardware(
                adapters={"science_camera": AsyncMock()}, hardware_config={},
            )),
        )
        ctx = SessionContext(site_name="St-Pancrasse")

        await InitSessionPlugin().run(ctx)  # must not raise

    async def test_propagates_unpark_failure(self, monkeypatch):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "indi")
        connected = ConnectedHardware(adapters={"mount": AsyncMock()}, hardware_config={"mount": {}})
        connected.adapters["mount"].unpark.side_effect = RuntimeError("indi boom")
        monkeypatch.setattr(
            "rosace.plugins.init_session.connect_session_hardware",
            AsyncMock(return_value=connected),
        )
        ctx = SessionContext(site_name="St-Pancrasse")

        with pytest.raises(RuntimeError, match="indi boom"):
            await InitSessionPlugin().run(ctx)


class TestCameraCooldownDispatch:
    """The science camera is only cooled if backend=indi AND its hardware
    entry declares cooling: true (see camera_cooldown.py)."""

    @pytest.fixture(autouse=True)
    def mock_activate_cooling(self, monkeypatch):
        """activate_cooling_for_every_camera() is tested on its own in
        test_camera_cooldown.py -- these tests only care about the
        science-camera cooldown dispatch logic, so it's mocked away here to
        keep the hardware mocks (plain object()) simple."""
        monkeypatch.setattr("rosace.plugins.init_session.activate_cooling_for_every_camera", AsyncMock())

    def _mock_hardware_with_camera(self, cooling: bool):
        return AsyncMock(return_value=ConnectedHardware(
            adapters={"science_camera": object(), "focuser": object()},
            hardware_config={
                "science_camera": {
                    "cooling": cooling,
                    "cooling_capability": -30,
                    "temperature_tolerance_deg_c": 2,
                },
            },
        ))

    async def test_cools_camera_when_cooling_enabled(self, monkeypatch):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "indi")
        monkeypatch.setattr(
            "rosace.plugins.init_session.connect_session_hardware",
            self._mock_hardware_with_camera(cooling=True),
        )
        mock_cooldown = AsyncMock(return_value=(15.0, -5))
        monkeypatch.setattr("rosace.plugins.init_session.cool_science_camera_to_target", mock_cooldown)
        ctx = SessionContext(site_name="St-Pancrasse")

        await InitSessionPlugin().run(ctx)

        mock_cooldown.assert_awaited_once()
        assert ctx.ambient_temperature_deg_c == 15.0
        assert ctx.camera_target_temperature_deg_c == -5

    async def test_skips_cooldown_when_cooling_disabled(self, monkeypatch):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "indi")
        monkeypatch.setattr(
            "rosace.plugins.init_session.connect_session_hardware",
            self._mock_hardware_with_camera(cooling=False),
        )
        mock_cooldown = AsyncMock()
        monkeypatch.setattr("rosace.plugins.init_session.cool_science_camera_to_target", mock_cooldown)
        ctx = SessionContext(site_name="St-Pancrasse")

        await InitSessionPlugin().run(ctx)

        mock_cooldown.assert_not_awaited()
        assert ctx.camera_target_temperature_deg_c is None

    async def test_skips_cooldown_when_no_science_camera_declared(self, monkeypatch, mock_hardware_check):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "indi")
        mock_cooldown = AsyncMock()
        monkeypatch.setattr("rosace.plugins.init_session.cool_science_camera_to_target", mock_cooldown)
        ctx = SessionContext(site_name="St-Pancrasse")

        await InitSessionPlugin().run(ctx)

        mock_cooldown.assert_not_awaited()

    async def test_propagates_cooldown_failure(self, monkeypatch):
        monkeypatch.setattr("rosace.plugins.init_session.SEQUENCER_BACKEND", "indi")
        monkeypatch.setattr(
            "rosace.plugins.init_session.connect_session_hardware",
            self._mock_hardware_with_camera(cooling=True),
        )
        mock_cooldown = AsyncMock(side_effect=RuntimeError("CAMERA_COOLDOWN: target -5C not reached within 300s"))
        monkeypatch.setattr("rosace.plugins.init_session.cool_science_camera_to_target", mock_cooldown)
        ctx = SessionContext(site_name="St-Pancrasse")

        with pytest.raises(RuntimeError, match="not reached"):
            await InitSessionPlugin().run(ctx)
