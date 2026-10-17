"""
Tests for hardware_replay/fake_phd2.py, driven by the real Phd2Client.
Run: pytest hardware_replay/test_fake_phd2.py -v
"""

from __future__ import annotations

import asyncio

import pytest

from rosace.hardware.errors import DeviceCommandError
from rosace.hardware_replay.fake_phd2 import FakePhd2Server
from rosace.phd2.phd2_client import Phd2Client

SETTLE = {"pixels": 1.0, "time": 1, "timeout": 10}


@pytest.fixture
async def server():
    fake = FakePhd2Server("127.0.0.1", 0, "Replay", settle_seconds=0.1)
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def client(server):
    phd2 = Phd2Client("127.0.0.1", server.port, settle=SETTLE)
    await phd2.connect(profile_name="Replay")
    yield phd2
    await phd2.disconnect()


class TestFakePhd2:

    async def test_unknown_profile_is_refused(self, server):
        phd2 = Phd2Client("127.0.0.1", server.port, settle=SETTLE)
        try:
            with pytest.raises(DeviceCommandError, match="not found"):
                await phd2.connect(profile_name="Other")
        finally:
            await phd2.disconnect()

    async def test_guide_settles_on_the_recorded_lock_position(self, replay_bench, client):
        replay_bench.point_at(10.0, 50.0)
        await client.start_guiding()
        assert await client.get_app_state() == "Guiding"
        assert await client.get_lock_position() == (501.0, 401.0)

    async def test_lock_moved_by_rosace_is_kept_for_the_second_guide(self, replay_bench, client):
        replay_bench.point_at(10.0, 50.0)
        await client.start_guiding()
        await client.set_lock_position(498.5, 403.25)
        await client.start_guiding()
        assert await client.get_lock_position() == (498.5, 403.25)

    async def test_stop_capture_confirms_and_forgets_the_lock(self, replay_bench, client):
        replay_bench.point_at(10.0, 50.0)
        await client.start_guiding()
        await client.stop_capture()
        assert await client.get_app_state() == "Stopped"
        assert await client.get_lock_position() is None

    async def test_stop_when_already_stopped(self, client):
        await client.stop_capture()

    async def test_stop_does_not_wait_for_a_connected_client(self, replay_bench):
        """A session still running at server shutdown keeps its client
        connected: stop() must close it, not wait for it."""
        fake = FakePhd2Server("127.0.0.1", 0, "Replay", settle_seconds=0.1)
        await fake.start()
        phd2 = Phd2Client("127.0.0.1", fake.port, settle=SETTLE)
        await phd2.connect(profile_name="Replay")
        try:
            await asyncio.wait_for(fake.stop(), timeout=5.0)
        finally:
            await phd2.disconnect()


class TestGuideLogReplay:

    async def test_recorded_frames_replayed_into_the_client_guide_log(self, replay_bench, server, client):
        server.step_seconds = 0.01
        replay_bench.point_at(10.0, 50.0)
        await client.start_guiding()
        await client.set_lock_position(498.5, 403.25)
        await client.start_guiding()
        await asyncio.sleep(0.2)
        await client.stop_capture()

        text = client.guide_log_section().text
        lines = text.splitlines()
        assert text.count("Guiding Begins at ") == 1 and lines[-1].startswith("Guiding Ends at ")
        assert "Pixel scale = 2.88 arc-sec/px" in lines and "Equipment Profile = Replay" in lines
        assert '1,2.000,"Mount",0.000,0.000,0.500,-0.250,0.400,-0.250,150,W,90,N,,,1000,30.00,0' in lines
        assert any(line.startswith('3,6.000,"DROP"') for line in lines)
        assert "INFO: SET LOCK POSITION, new lock pos = 498.500, 403.250" in lines
        assert lines.count("INFO: SETTLING STATE CHANGE, Settling complete") == 2
        # The recorded times, so the log lines up with the replayed frames
        assert "Guiding Begins at 2026-10-14 21:00:00" in lines
        assert client.guide_log_section().received_at > client.guide_log_section().started_at

    async def test_observation_without_guide_log_replays_no_frame(self, replay_bench, server, client):
        server.step_seconds = 0.01
        replay_bench.point_at(80.0, 20.0)
        await client.start_guiding()
        await asyncio.sleep(0.1)
        await client.stop_capture()

        text = client.guide_log_section().text
        assert '"Mount"' not in text and "Guiding Ends at " in text
