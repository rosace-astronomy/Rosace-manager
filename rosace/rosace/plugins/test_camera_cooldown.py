"""
Tests for camera_cooldown.py (science camera cooldown, called once from
INIT_SESSION). Camera and focuser are mocks, no real hardware.
Run: pytest plugins/test_camera_cooldown.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from rosace.plugins import camera_cooldown as cooldown_mod
from rosace.plugins.camera_cooldown import (
    activate_cooling_for_every_camera,
    compute_camera_target_temperature,
    cool_science_camera_to_target,
    deactivate_cooling_for_every_camera,
    round_to_5,
)


class TestRoundTo5:

    def test_rounds_negative_up_to_next_multiple_of_5(self):
        assert round_to_5(-9) == -5

    def test_rounds_positive_up_to_next_multiple_of_5(self):
        assert round_to_5(3) == 5

    def test_value_already_a_multiple_of_5_is_unchanged(self):
        assert round_to_5(-5) == -5
        assert round_to_5(0) == 0
        assert round_to_5(10) == 10


class TestComputeCameraTargetTemperature:

    def test_user_worked_example(self):
        # Ambient 15C, cooling_capability -30C -> delta 24C -> raw -9C -> -5C
        assert compute_camera_target_temperature(ambient_temp_c=15, cooling_capability_deg_c=-30) == -5

    def test_accepts_positive_cooling_capability_too(self):
        assert compute_camera_target_temperature(ambient_temp_c=15, cooling_capability_deg_c=30) == -5

    def test_weak_cooling_capability_does_not_exceed_ambient(self):
        """Found on a real first-light test: ambient 15C, a weak
        cooling_capability_deg_c (-2C) gives delta=1.6C -> raw 13.4C ->
        round_to_5 (ceiling) would naively give 15C -- equal to ambient, so
        a cooling-only camera could never reach it (timed out). The safety
        floor must instead pick the next colder bucket, at least
        MIN_COOLING_MARGIN_DEG_C below ambient."""
        target = compute_camera_target_temperature(ambient_temp_c=15, cooling_capability_deg_c=-2)
        assert target <= 15 - 5
        assert target == 10

    def test_extremely_weak_cooling_capability_still_leaves_margin(self):
        # Ambient 9.9C, cooling_capability -0.1C -> delta 0.08C -> raw 9.82C
        # -> naive round_to_5 gives 10C (ABOVE ambient) without the floor.
        target = compute_camera_target_temperature(ambient_temp_c=9.9, cooling_capability_deg_c=-0.1)
        assert target <= 9.9 - 5
        assert target == 0

    def test_strong_cooling_capability_unaffected_by_safety_floor(self):
        """A real cooling margin (as in the worked example above) must not
        be clamped further than round_to_5 already gives."""
        assert compute_camera_target_temperature(ambient_temp_c=15, cooling_capability_deg_c=-30) == -5


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

    async def test_waits_before_first_temperature_read(self, monkeypatch):
        """Regression: reading right after set_temperature() returned the
        setpoint just written, not the sensor's real temperature."""
        calls = []

        async def _recording_sleep(_seconds):
            calls.append("sleep")

        monkeypatch.setattr(cooldown_mod.asyncio, "sleep", _recording_sleep)
        focuser = AsyncMock()
        focuser.get_temperature.return_value = 15.0
        camera = AsyncMock()

        async def _get_temperature():
            calls.append("read")
            return -5.0

        camera.get_temperature.side_effect = _get_temperature

        await cool_science_camera_to_target(
            camera, focuser, cooling_capability_deg_c=-30, tolerance_deg_c=2,
        )

        assert calls == ["sleep", "read"]

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


class TestActivateCoolingForEveryCamera:

    async def test_turns_on_cooling_for_every_camera_that_has_it(self):
        science_camera = AsyncMock()
        guiding_camera = AsyncMock()
        mount = AsyncMock()
        hardware = {"science_camera": science_camera, "guiding_camera": guiding_camera, "mount": mount}
        hardware_config = {
            "science_camera": {"cooling": True},
            "guiding_camera": {"cooling": True},
            "mount": {"backlash_offset_deg": 2},
        }

        await activate_cooling_for_every_camera(hardware, hardware_config)

        science_camera.set_cooling_on.assert_awaited_once()
        guiding_camera.set_cooling_on.assert_awaited_once()

    async def test_skips_devices_without_cooling(self):
        pointing_camera = AsyncMock()
        hardware = {"pointing_camera": pointing_camera}
        hardware_config = {"pointing_camera": {"cooling": False}}

        await activate_cooling_for_every_camera(hardware, hardware_config)

        pointing_camera.set_cooling_on.assert_not_awaited()

    async def test_skips_devices_that_never_declare_cooling(self):
        mount = AsyncMock()
        hardware = {"mount": mount}
        hardware_config = {"mount": {"backlash_offset_deg": 2}}

        await activate_cooling_for_every_camera(hardware, hardware_config)

        mount.set_cooling_on.assert_not_awaited()


class TestDeactivateCoolingForEveryCamera:

    async def test_turns_off_cooling_for_every_camera_that_has_it(self):
        science_camera = AsyncMock()
        guiding_camera = AsyncMock()
        hardware = {"science_camera": science_camera, "guiding_camera": guiding_camera}
        hardware_config = {
            "science_camera": {"cooling": True},
            "guiding_camera": {"cooling": True},
        }

        await deactivate_cooling_for_every_camera(hardware, hardware_config)

        science_camera.set_cooling_off.assert_awaited_once()
        guiding_camera.set_cooling_off.assert_awaited_once()

    async def test_one_failure_does_not_block_the_others(self):
        failing = AsyncMock()
        failing.set_cooling_off.side_effect = RuntimeError("boom")
        science_camera = AsyncMock()
        hardware = {"guiding_camera": failing, "science_camera": science_camera}
        hardware_config = {
            "guiding_camera": {"cooling": True},
            "science_camera": {"cooling": True},
        }

        await deactivate_cooling_for_every_camera(hardware, hardware_config)  # must not raise

        science_camera.set_cooling_off.assert_awaited_once()
