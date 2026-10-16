"""
Tests for hardware_replay/bench.py: which recorded frame is served, from the
mount's position and the spectroscope's light source.
Run: pytest hardware_replay/test_bench.py -v
"""

from __future__ import annotations

import pytest
from astropy.io import fits

from rosace.hardware.spectroscope import LightSource
from rosace.hardware_replay.bench import ReplayBench


def _value(path):
    return int(fits.getdata(path)[0, 0])


class TestPointing:

    def test_nearest_observation_within_homing_distance(self, replay_bench):
        replay_bench.point_at(10.5, 50.3)
        assert replay_bench.star["queried_name"] == "Star A"

    def test_far_from_every_observation_is_homing(self, replay_bench):
        replay_bench.point_at(200.0, -10.0)
        assert replay_bench.star is None
        assert _value(replay_bench.guide_frame()) == 9

    def test_missing_dataset_names_the_builder(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="build_demo_dataset"):
            ReplayBench(tmp_path / "nothing")


class TestScienceFrames:

    def test_sky_serves_the_series_in_order_then_cycles(self, replay_bench):
        replay_bench.point_at(80.0, 20.0)
        assert [_value(replay_bench.science_frame(180)) for _ in range(3)] == [201, 202, 201]

    @pytest.mark.parametrize("source, exposure, expected", [
        (LightSource.DARK, 0.0, 1), (LightSource.DARK, 180.0, 2),
        (LightSource.CALIB, 3.0, 3), (LightSource.FLAT, 3.0, 4),
    ])
    def test_calibration_by_light_source(self, replay_bench, source, exposure, expected):
        replay_bench.light_source = source
        assert _value(replay_bench.science_frame(exposure)) == expected

    def test_new_observation_starts_its_frames_again(self, replay_bench):
        replay_bench.point_at(10.0, 50.0)
        replay_bench.science_frame(180)
        replay_bench.point_at(80.0, 20.0)
        replay_bench.point_at(10.0, 50.0)
        assert _value(replay_bench.science_frame(180)) == 101


class TestGuideFrames:

    def test_sky_in_recorded_order_last_repeated(self, replay_bench):
        replay_bench.point_at(10.0, 50.0)
        # slew 110, pointing 112, focus 113, stop 114 (slit 111 is lamp-lit)
        assert [_value(replay_bench.guide_frame()) for _ in range(5)] == [110, 112, 113, 114, 114]

    def test_same_observation_does_not_restart(self, replay_bench):
        replay_bench.point_at(10.0, 50.0)
        replay_bench.guide_frame()
        replay_bench.point_at(10.2, 50.1)   # corrected slew, same observation
        assert _value(replay_bench.guide_frame()) == 112

    def test_approach_point_does_not_restart_the_series(self, replay_bench):
        """slew_72ed slews to an approach point offset by backlash_offset_deg
        (far from every observation) before every exact slew."""
        replay_bench.point_at(10.0, 50.0)
        replay_bench.guide_frame()                 # slew image
        replay_bench.point_at(12.0, 52.0)          # approach point, ~2.4 deg away
        assert replay_bench.star is None
        replay_bench.point_at(10.0, 50.0)
        assert _value(replay_bench.guide_frame()) == 112   # pointing image, not slew again

    def test_lamp_serves_the_slit_image(self, replay_bench):
        replay_bench.point_at(10.0, 50.0)
        replay_bench.light_source = LightSource.CALIB
        assert _value(replay_bench.guide_frame()) == 111

    def test_dark_serves_guiding_darks(self, replay_bench):
        replay_bench.light_source = LightSource.DARK
        assert _value(replay_bench.guide_frame()) == 5


class TestRecordedValues:

    def test_ambient_of_the_observation_pointed_at(self, replay_bench):
        assert replay_bench.ambient_temp_c() == 15.0
        replay_bench.point_at(80.0, 20.0)
        assert replay_bench.ambient_temp_c() == 12.0

    def test_lock_position(self, replay_bench):
        assert replay_bench.lock_position() is None
        replay_bench.point_at(10.0, 50.0)
        assert replay_bench.lock_position() == (501.0, 401.0)
