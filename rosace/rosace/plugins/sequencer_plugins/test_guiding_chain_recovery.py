"""
Tests for the guiding-chain recovery helper (guiding_chain_recovery.py).
Run: pytest plugins/sequencer_plugins/test_guiding_chain_recovery.py -v
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from rosace.errors import SessionFatalError
from rosace.hardware.errors import DeviceCommandError
from rosace.models import Observation
from rosace.plugins.sequencer_plugins import guiding_chain_recovery
from rosace.plugins.sequencer_plugins.guiding_chain_recovery import (
    MAX_RECOVERY_ATTEMPTS,
    run_with_guiding_recovery,
)


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(guiding_chain_recovery, "PAUSE_BETWEEN_ATTEMPTS_SECONDS", 0.0)


def _make_obs(with_camera: bool = True, with_phd2: bool = True) -> Observation:
    obs = Observation(id="obs1", created_at=datetime.now(timezone.utc), session_id="sess1")
    if with_camera:
        obs.hardware["guiding_camera"] = AsyncMock()
    if with_phd2:
        obs.hardware["phd2"] = AsyncMock()
    return obs


class TestHealthyPath:
    async def test_successful_operation_touches_no_hardware(self):
        obs = _make_obs()
        operation = AsyncMock(return_value=42)

        assert await run_with_guiding_recovery(obs, operation, "test op") == 42

        operation.assert_awaited_once()
        obs.hardware["guiding_camera"].expose.assert_not_awaited()
        obs.hardware["phd2"].stop_capture.assert_not_awaited()

    async def test_no_guiding_camera_runs_unguarded(self):
        obs = _make_obs(with_camera=False)
        operation = AsyncMock(side_effect=DeviceCommandError("boom"))

        with pytest.raises(DeviceCommandError, match="boom"):
            await run_with_guiding_recovery(obs, operation, "test op")

        operation.assert_awaited_once()
        obs.hardware["phd2"].stop_capture.assert_not_awaited()

    async def test_non_device_error_is_not_guarded(self):
        """Only device errors mean a possible camera fault -- anything else
        (a bug, bad data) propagates at once, without probing the camera."""
        obs = _make_obs()
        operation = AsyncMock(side_effect=ValueError("bad input"))

        with pytest.raises(ValueError, match="bad input"):
            await run_with_guiding_recovery(obs, operation, "test op")

        obs.hardware["guiding_camera"].expose.assert_not_awaited()


class TestCameraStillAnswers:
    async def test_original_error_is_reraised_and_camera_left_alone(self):
        obs = _make_obs()
        operation = AsyncMock(side_effect=DeviceCommandError("no star found"))

        with pytest.raises(DeviceCommandError, match="no star found"):
            await run_with_guiding_recovery(obs, operation, "test op")

        operation.assert_awaited_once()  # not replayed
        obs.hardware["phd2"].stop_capture.assert_awaited_once()
        obs.hardware["guiding_camera"].expose.assert_awaited_once()  # the probe
        obs.hardware["guiding_camera"].disconnect.assert_not_awaited()
        obs.hardware["guiding_camera"].connect.assert_not_awaited()


class TestRecovery:
    async def test_recovered_camera_replays_the_operation(self):
        obs = _make_obs()
        camera = obs.hardware["guiding_camera"]
        # first probe fails (dead camera), probe after the reconnect succeeds
        camera.expose.side_effect = [DeviceCommandError("CAMERA_CLOSED"), None]
        operation = AsyncMock(side_effect=[DeviceCommandError("boom"), "replayed"])

        assert await run_with_guiding_recovery(obs, operation, "test op") == "replayed"

        assert operation.await_count == 2
        camera.disconnect.assert_awaited_once()
        camera.connect.assert_awaited_once()

    async def test_second_attempt_can_succeed(self):
        obs = _make_obs()
        camera = obs.hardware["guiding_camera"]
        camera.expose.side_effect = [
            DeviceCommandError("dead"), DeviceCommandError("still dead"), None,
        ]
        operation = AsyncMock(side_effect=[DeviceCommandError("boom"), "replayed"])

        assert await run_with_guiding_recovery(obs, operation, "test op") == "replayed"

        assert camera.connect.await_count == 2

    async def test_replay_failure_propagates(self):
        obs = _make_obs()
        camera = obs.hardware["guiding_camera"]
        camera.expose.side_effect = [DeviceCommandError("dead"), None]
        operation = AsyncMock(side_effect=[DeviceCommandError("boom"), DeviceCommandError("boom again")])

        with pytest.raises(DeviceCommandError, match="boom again"):
            await run_with_guiding_recovery(obs, operation, "test op")

        assert operation.await_count == 2  # replayed once, never a third time

    async def test_failing_disconnect_does_not_block_reconnect(self):
        obs = _make_obs()
        camera = obs.hardware["guiding_camera"]
        camera.expose.side_effect = [DeviceCommandError("dead"), None]
        camera.disconnect.side_effect = RuntimeError("already gone")
        operation = AsyncMock(side_effect=[DeviceCommandError("boom"), "replayed"])

        assert await run_with_guiding_recovery(obs, operation, "test op") == "replayed"

        camera.connect.assert_awaited_once()

    async def test_failing_phd2_stop_does_not_block_recovery(self):
        obs = _make_obs()
        obs.hardware["phd2"].stop_capture.side_effect = DeviceCommandError("PHD2 stuck")
        camera = obs.hardware["guiding_camera"]
        camera.expose.side_effect = [DeviceCommandError("dead"), None]
        operation = AsyncMock(side_effect=[DeviceCommandError("boom"), "replayed"])

        assert await run_with_guiding_recovery(obs, operation, "test op") == "replayed"


class TestUnrecoverable:
    async def test_session_fatal_error_after_every_attempt_failed(self):
        obs = _make_obs()
        camera = obs.hardware["guiding_camera"]
        camera.expose.side_effect = DeviceCommandError("ASI_ERROR_CAMERA_REMOVED")
        operation = AsyncMock(side_effect=DeviceCommandError("boom"))

        with pytest.raises(SessionFatalError, match=f"after {MAX_RECOVERY_ATTEMPTS} recovery attempts"):
            await run_with_guiding_recovery(obs, operation, "test op")

        operation.assert_awaited_once()  # never replayed on a dead camera
        assert camera.connect.await_count == MAX_RECOVERY_ATTEMPTS

    async def test_fatal_message_carries_the_last_error(self):
        obs = _make_obs()
        obs.hardware["guiding_camera"].expose.side_effect = DeviceCommandError("ASI_ERROR_CAMERA_REMOVED")
        operation = AsyncMock(side_effect=DeviceCommandError("boom"))

        with pytest.raises(SessionFatalError, match="ASI_ERROR_CAMERA_REMOVED"):
            await run_with_guiding_recovery(obs, operation, "test op")

    async def test_hanging_camera_call_counts_as_failure(self, monkeypatch):
        monkeypatch.setattr(guiding_chain_recovery, "CALL_TIMEOUT_SECONDS", 0.01)
        obs = _make_obs()

        async def hang(*args, **kwargs):
            await asyncio.sleep(3600)

        obs.hardware["guiding_camera"].expose.side_effect = hang
        operation = AsyncMock(side_effect=DeviceCommandError("boom"))

        with pytest.raises(SessionFatalError):
            await run_with_guiding_recovery(obs, operation, "test op")
