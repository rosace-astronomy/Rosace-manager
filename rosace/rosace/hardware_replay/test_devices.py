"""
Tests for hardware_replay/devices.py, through the real hardware_indi/
adapters, and the whole connection of a replay hardware profile by
plugins/hardware_connectivity.py (fake PHD2 included).
Run: pytest hardware_replay/test_devices.py -v
"""

from __future__ import annotations

import numpy as np
import pytest
import yaml

from rosace.hardware.spectroscope import LightSource
from rosace.hardware_indi.adapters.camera import CameraAdapter
from rosace.hardware_indi.adapters.focuser import FocuserAdapter
from rosace.hardware_indi.adapters.mount import MountAdapter
from rosace.hardware_indi.adapters.spectroscope import SpectroscopeAdapter
from rosace.hardware_replay.devices import ReplayCamera, ReplayFocuser, ReplayMount, ReplaySpectroscope
from rosace.hardware_replay.fake_phd2 import FakePhd2Server
from rosace.plugins import hardware_connectivity


def _camera(frames):
    return CameraAdapter(ReplayCamera(config={"replay_frames": frames, "max_wait_seconds": 0}))


class TestCamera:

    async def test_science_exposure_returns_the_recorded_frame_and_header(self, replay_bench):
        mount = MountAdapter(ReplayMount(config={"slew_seconds": 0}))
        await mount.slew_to(80.0, 20.0)
        image = await _camera("science").expose(5.0)
        assert image[0].data.dtype == np.uint16
        assert int(image[0].data[0, 0]) == 201
        assert image[0].header["EXPTIME"] == 180.0

    async def test_frame_written_back_reads_the_same(self, replay_bench, tmp_path):
        image = await _camera("science").expose(5.0)
        image.writeto(tmp_path / "copy.fits")
        from astropy.io import fits
        assert np.array_equal(fits.getdata(tmp_path / "copy.fits"), image[0].data)

    async def test_spectroscope_drives_the_frame_kind(self, replay_bench):
        await SpectroscopeAdapter(ReplaySpectroscope()).set_light_source(LightSource.DARK)
        assert int((await _camera("science").expose(0.0))[0].data[0, 0]) == 1
        assert int((await _camera("guiding").expose(10.0))[0].data[0, 0]) == 5

    async def test_frame_size_and_temperature(self, replay_bench):
        camera = _camera("science")
        assert await camera.get_frame_size() == (8, 6)
        assert await camera.get_temperature() == 15.0
        await camera.set_temperature(-5.0)
        assert await camera.get_temperature() == -5.0

    def test_unknown_frames_kind_is_refused(self):
        with pytest.raises(ValueError, match="replay_frames"):
            ReplayCamera(config={"replay_frames": "spectro"})


class TestMountAndFocuser:

    async def test_position_and_pier_side(self, replay_bench):
        mount = MountAdapter(ReplayMount(config={"slew_seconds": 0}))
        await mount.slew_to(10.0, 50.0)
        ra, dec = await mount.get_position()
        assert (ra, dec) == pytest.approx((10.0, 50.0))
        assert await mount.get_pier_side() in ("east", "west")
        assert replay_bench.star["queried_name"] == "Star A"

    async def test_focuser_ambient_follows_the_observation(self, replay_bench):
        focuser = FocuserAdapter(ReplayFocuser())
        await MountAdapter(ReplayMount(config={"slew_seconds": 0})).slew_to(10.0, 50.0)
        assert await focuser.get_temperature() == 11.0


class TestReplayProfileConnection:

    async def test_connect_session_hardware_with_a_replay_profile(self, replay_bench, tmp_path, monkeypatch):
        server = FakePhd2Server("127.0.0.1", 0, "Replay", settle_seconds=0.1)
        await server.start()
        device = {"module_dir": "rosace.hardware_replay", "module": "devices",
                  "indi_name": "replay", "indi_client": {"indi_host": "none", "indi_port": 0}}
        profile = {
            "simulated": True,
            "replay_dataset_dir": str(replay_bench.dataset_dir),
            "INDI_drivers": {
                "science_camera": {**device, "role": "camera", "class_name": "ReplayCamera",
                                   "replay_frames": "science", "cooling": True},
                "guiding_camera": {**device, "role": "camera", "class_name": "ReplayCamera",
                                   "replay_frames": "guiding"},
                "mount": {**device, "role": "mount", "class_name": "ReplayMount", "backlash_offset_deg": 2},
                "focuser": {**device, "role": "focuser", "class_name": "ReplayFocuser"},
                "spectroscope": {**device, "role": "spectroscope", "class_name": "ReplaySpectroscope"},
            },
            "PHD2_config": {"host": "127.0.0.1", "port": server.port, "profile_name": "Replay",
                            "settle": {"pixels": 1, "time": 1, "timeout": 10}},
        }
        path = tmp_path / "hardware_replay.yaml"
        path.write_text(yaml.safe_dump(profile))
        monkeypatch.setattr(hardware_connectivity, "HARDWARE_CONFIG_PATH", path)
        try:
            connected = await hardware_connectivity.connect_session_hardware()
            assert set(connected.adapters) == {"science_camera", "guiding_camera", "mount", "focuser",
                                               "spectroscope", "phd2"}
            assert connected.hardware_config["mount"] == {"backlash_offset_deg": 2}
            await connected.adapters["phd2"].disconnect()
        finally:
            await server.stop()
