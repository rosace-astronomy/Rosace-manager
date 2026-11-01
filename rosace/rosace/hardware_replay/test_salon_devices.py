"""
Tests for hardware_replay/salon_devices.py: the shifted sky of the real
mount, the hybrid cameras and the spectroscope, on the synthetic dataset
(fixture replay_bench) -- the INDI parent classes replaced, no INDI server.
Run: pytest hardware_replay/test_salon_devices.py -v
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
from astropy import units as u
from astropy.coordinates import SkyCoord

from rosace.astro_math import hour_angle_deg
from rosace.hardware.spectroscope import LightSource
from rosace.hardware_indi.devices.mount import IndiMount
from rosace.hardware_indi.devices.spectro_Alpy600 import IndiSpectroAlpy600
from rosace.hardware_replay.salon_devices import (
    BenchSpectroscope, HybridCameraMixin, ShiftedSkyMount, sky_shift_deg,
)
from rosace.hardware_replay.testing import SHAPE

RECORDED_AT = datetime(2026, 10, 14, 21, 0, tzinfo=timezone.utc)


class TestSkyShift:

    def test_one_hour_later_is_one_sidereal_hour(self):
        shift = sky_shift_deg(RECORDED_AT, RECORDED_AT + timedelta(hours=1))
        assert shift == pytest.approx(15.041, abs=0.001)

    def test_shifted_position_has_the_recorded_hour_angle(self):
        now = RECORDED_AT + timedelta(days=18, hours=-9, minutes=17)
        ra = 54.1
        shifted = (ra + sky_shift_deg(RECORDED_AT, now)) % 360
        assert hour_angle_deg(shifted, 5.9, now) == pytest.approx(hour_angle_deg(ra, 5.9, RECORDED_AT), abs=1e-6)


class TestShiftedSkyMount:

    @pytest.fixture
    def mount(self, replay_bench, monkeypatch):
        for star in replay_bench.stars:
            star["recorded_at"] = RECORDED_AT.isoformat()
        sent = []
        monkeypatch.setattr(IndiMount, "set_coord", lambda self, coord, wait=True: sent.append(coord))
        monkeypatch.setattr(IndiMount, "get_current_coordinates", lambda self: sent[-1])
        monkeypatch.setattr(IndiMount, "on_coord_set", lambda self, what: None)
        monkeypatch.setattr(IndiMount, "_wait_for_slew", lambda self, coord: None)
        monkeypatch.setattr(IndiMount, "is_parked", False, raising=False)
        device = ShiftedSkyMount.__new__(ShiftedSkyMount)
        device._shift_deg = 0.0
        device.logger = logging.getLogger("test.salon")
        return device, sent

    def test_sends_the_shifted_position_reads_back_the_original(self, mount):
        device, sent = mount
        device.slew_to_coord_and_track(SkyCoord(ra=10.0 * u.deg, dec=50.0 * u.deg, frame="icrs"))
        expected = (10.0 + device._shift_deg) % 360
        assert sent[-1].ra.deg == pytest.approx(expected)
        assert sent[-1].dec.deg == pytest.approx(50.0)
        back = device.get_current_coordinates()
        assert back.ra.deg == pytest.approx(10.0)
        assert back.dec.deg == pytest.approx(50.0)

    def test_bench_points_at_the_unshifted_position(self, mount, replay_bench):
        device, _ = mount
        device.slew_to_coord_and_track(SkyCoord(ra=80.0 * u.deg, dec=20.0 * u.deg, frame="icrs"))
        assert replay_bench.star["queried_name"] == "Star B"

    def test_shift_follows_the_nearest_observation(self, mount, replay_bench):
        device, _ = mount
        replay_bench.stars[1]["recorded_at"] = (RECORDED_AT + timedelta(hours=2)).isoformat()
        device.set_coord(SkyCoord(ra=12.0 * u.deg, dec=48.0 * u.deg, frame="icrs"))   # approach of Star A
        shift_a = device._shift_deg
        device.set_coord(SkyCoord(ra=80.0 * u.deg, dec=20.0 * u.deg, frame="icrs"))
        assert (shift_a - device._shift_deg) % 360 == pytest.approx(2 * 15.041, abs=0.01)


class _FakeCamera:
    """Stands for IndiCamera: records what the mixin asks of the real camera."""

    def __init__(self, config=None, logger=None, connect_on_create=False):
        self.logger = logging.getLogger("test.salon")
        self.exp_time_sec = None
        self.shot = False
        self.received = False

    def setExpTimeSec(self, exp_time_sec):
        self.exp_time_sec = exp_time_sec

    def shoot_async(self):
        self.shot = True

    def get_received_image(self):
        self.received = True
        return "real frame"


class _Hybrid(HybridCameraMixin, _FakeCamera):
    pass


class TestHybridCamera:

    def test_real_exposure_capped_recorded_frame_returned(self, replay_bench):
        camera = _Hybrid(config={"replay_frames": "science", "max_wait_seconds": 30})
        replay_bench.point_at(80.0, 20.0)
        camera.setExpTimeSec(180.0)
        camera.shoot_async()
        image = camera.get_received_image()
        assert camera.exp_time_sec == 30.0 and camera.shot and camera.received
        assert int(image[0].data[0, 0]) == 201
        assert image[0].header["EXPTIME"] == 180.0

    def test_a_zero_second_exposure_with_the_dark_lamp_is_a_bias(self, replay_bench):
        camera = _Hybrid(config={"replay_frames": "science"})
        replay_bench.light_source = LightSource.DARK
        camera.setExpTimeSec(0.0)
        camera.shoot_async()
        assert int(camera.get_received_image()[0].data[0, 0]) == 1

    def test_guiding_frames_and_recorded_size(self, replay_bench):
        camera = _Hybrid(config={"replay_frames": "guiding"})
        replay_bench.point_at(10.0, 50.0)
        camera.setExpTimeSec(10.0)
        camera.shoot_async()
        assert int(camera.get_received_image()[0].data[0, 0]) == 110
        roi = camera.get_roi()
        assert (roi["HEIGHT"], roi["WIDTH"]) == SHAPE

    def test_unknown_frames_refused(self):
        with pytest.raises(ValueError, match="replay_frames"):
            _Hybrid(config={"replay_frames": "sky"})


class TestBenchSpectroscope:

    def test_bench_follows_the_real_lamp(self, replay_bench, monkeypatch):
        sent = []
        monkeypatch.setattr(IndiSpectroAlpy600, "set_light_source", lambda self, source: sent.append(source))
        device = BenchSpectroscope.__new__(BenchSpectroscope)
        device.set_light_source(LightSource.CALIB)
        assert sent == [LightSource.CALIB]
        assert replay_bench.light_source is LightSource.CALIB
