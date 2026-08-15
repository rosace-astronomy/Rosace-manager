"""
Tests for camera_cooldown.py (science camera cooldown, called once from
INIT_SESSION). Camera and focuser are mocks, no real hardware.
Run: pytest plugins/test_camera_cooldown.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from rosace.plugins import camera_cooldown as cooldown_mod
from rosace.plugins.camera_cooldown import cool_science_camera_to_target


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """The convergence/timeout loop sleeps between polls -- make it instant
    so these tests run fast regardless of POLL_INTERVAL_SECONDS."""
    async def _instant_sleep(_seconds):
        return None
    monkeypatch.setattr(cooldown_mod.asyncio, "sleep", _instant_sleep)


class TestCoolScienceCameraToTarget:

    async def test_converges_immediately_when_already_at_target(self):
        focuser = AsyncMock()
        focuser.get_temperature.return_value = 15.0
        camera = AsyncMock()
        # ambient 15C, cooling_capability -30C -> delta 24C -> target -5C
        camera.get_temperature.return_value = -5.0

        ambient, target = await cool_science_camera_to_target(
            camera, focuser, cooling_capability_deg_c=-30, tolerance_deg_c=2,
        )

        assert ambient == 15.0
        assert target == -5
        camera.set_temperature.assert_awaited_once_with(-5)

    async def test_converges_after_a_few_polls_within_tolerance(self):
        focuser = AsyncMock()
        focuser.get_temperature.return_value = 15.0
        camera = AsyncMock()
        # First reading far from target, then within tolerance.
        camera.get_temperature.side_effect = [5.0, 0.0, -4.0]

        ambient, target = await cool_science_camera_to_target(
            camera, focuser, cooling_capability_deg_c=-30, tolerance_deg_c=2,
        )

        assert target == -5
        assert camera.get_temperature.await_count == 3

    async def test_raises_on_timeout_without_reaching_target(self, monkeypatch):
        monkeypatch.setattr(cooldown_mod, "CAMERA_COOLDOWN_TIMEOUT_SECONDS", 10)
        focuser = AsyncMock()
        focuser.get_temperature.return_value = 15.0
        camera = AsyncMock()
        camera.get_temperature.return_value = 10.0  # never converges

        with pytest.raises(RuntimeError, match="not reached"):
            await cool_science_camera_to_target(
                camera, focuser, cooling_capability_deg_c=-30, tolerance_deg_c=2,
            )

    async def test_stop_check_interrupts_wait_without_raising(self, monkeypatch):
        monkeypatch.setattr(cooldown_mod, "CAMERA_COOLDOWN_TIMEOUT_SECONDS", 3600)
        focuser = AsyncMock()
        focuser.get_temperature.return_value = 15.0
        camera = AsyncMock()
        camera.get_temperature.return_value = 10.0  # never converges

        ambient, target = await cool_science_camera_to_target(
            camera, focuser, cooling_capability_deg_c=-30, tolerance_deg_c=2,
            stop_check=lambda: True,
        )

        assert target == -5
