"""
Tests for horizon.py (local horizon mask: KStars export parsing + lookup).
Run: pytest plugins/schedulers/scheduler_rosace_2026/test_horizon.py -v
"""

import pytest

from rosace.plugins.schedulers import SchedulerFatalError
from rosace.plugins.schedulers.scheduler_rosace_2026.horizon import (
    horizon_altitude_deg,
    load_horizon,
)

# The real KStars export of the St-Pancrasse site (closes on its own first
# point, has both 360 and 0, starts mid-circle at azimuth 220).
REAL_PROFILE = """# KStars Artificial Horizon export (Alt Az)
#
Horizon Observatoire St-Pancrasse
220 0
230 9
240 10
250 12
260 17
270 34
280 35
290 30
300 18
310 33
320 39
330 45
340 46
350 36
360 36
0 36
10 38
20 21
30 27
40 27
50 26
60 14
70 10
80 25
90 22
100 20
110 16
120 20
130 21
140 22
150 23
160 16
170 16
180 13
190 10
200 8
210 9
220 0
"""


def _write(tmp_path, content: str):
    path = tmp_path / "horizon.txt"
    path.write_text(content, encoding="utf-8")
    return path


class TestLoadHorizon:

    def test_parses_the_real_profile(self, tmp_path):
        points = load_horizon(_write(tmp_path, REAL_PROFILE))

        # 38 lines of points: 220 given twice and 360/0 both given (same
        # direction, same altitude) -> 36 distinct azimuths.
        assert len(points) == 36
        assert points == sorted(points)
        assert points[0] == (0.0, 36.0)
        assert (270.0, 34.0) in points
        assert points[-1] == (350.0, 36.0)

    def test_comments_blank_lines_and_name_line_are_ignored(self, tmp_path):
        points = load_horizon(_write(tmp_path, "# c\n\nMy site\n\n0 10\n# mid\n180 20\n"))
        assert points == [(0.0, 10.0), (180.0, 20.0)]

    def test_name_line_is_optional(self, tmp_path):
        points = load_horizon(_write(tmp_path, "0 10\n180 20\n"))
        assert points == [(0.0, 10.0), (180.0, 20.0)]

    def test_profile_starting_mid_circle_is_returned_sorted(self, tmp_path):
        # Wraps through north once (270 -> 0): allowed, returned sorted.
        points = load_horizon(_write(tmp_path, "270 5\n0 10\n180 20\n"))
        assert [az for az, _ in points] == [0.0, 180.0, 270.0]

    def test_out_of_order_point_is_an_error(self, tmp_path):
        # 90 after 100 without wrapping through north: a typo, not a profile.
        with pytest.raises(SchedulerFatalError, match="line 3.*out of order"):
            load_horizon(_write(tmp_path, "0 10\n100 5\n90 6\n180 4\n"))

    def test_second_wrap_through_north_is_an_error(self, tmp_path):
        # 300 -> 10 is the allowed wrap; 200 -> 5 wraps a second time.
        with pytest.raises(SchedulerFatalError, match="line 4.*out of order"):
            load_horizon(_write(tmp_path, "300 5\n10 6\n200 3\n5 4\n"))

    def test_wrap_through_360_written_as_360_then_0_is_accepted(self, tmp_path):
        # The real export lists both "360 36" and "0 36": still one wrap only.
        points = load_horizon(_write(tmp_path, "300 5\n350 6\n360 7\n0 7\n90 8\n"))
        assert [az for az, _ in points] == [0.0, 90.0, 300.0, 350.0]

    def test_360_is_the_same_direction_as_0(self, tmp_path):
        points = load_horizon(_write(tmp_path, "0 10\n360 10\n180 20\n"))
        assert points == [(0.0, 10.0), (180.0, 20.0)]

    def test_360_and_0_with_different_altitudes_is_an_error(self, tmp_path):
        with pytest.raises(SchedulerFatalError, match="already given"):
            load_horizon(_write(tmp_path, "0 10\n360 12\n180 20\n"))

    def test_same_azimuth_with_different_altitudes_is_an_error(self, tmp_path):
        with pytest.raises(SchedulerFatalError, match="line 3.*already given"):
            load_horizon(_write(tmp_path, "0 10\n90 20\n90 25\n"))

    def test_garbage_line_after_the_points_names_the_line_number(self, tmp_path):
        with pytest.raises(SchedulerFatalError, match="line 4.*expected 'azimuth altitude'"):
            load_horizon(_write(tmp_path, "Name\n0 10\n90 20\nnot numbers\n"))

    def test_a_second_non_numeric_line_before_the_points_is_an_error(self, tmp_path):
        with pytest.raises(SchedulerFatalError, match="line 2"):
            load_horizon(_write(tmp_path, "Name\nSecond name\n0 10\n90 20\n"))

    def test_three_columns_is_an_error(self, tmp_path):
        with pytest.raises(SchedulerFatalError, match="line 2"):
            load_horizon(_write(tmp_path, "0 10\n90 20 30\n180 5\n"))

    @pytest.mark.parametrize("content, match", [
        ("0 10\n361 20\n", "azimuth 361.0 outside"),
        ("0 10\n-5 20\n", "azimuth -5.0 outside"),
        ("0 10\n90 91\n", "altitude 91.0 outside"),
        ("0 10\n90 -1\n", "altitude -1.0 outside"),
        ("0 10\n90 nan\n", "altitude nan outside"),
    ])
    def test_out_of_range_values_are_errors(self, tmp_path, content, match):
        with pytest.raises(SchedulerFatalError, match=match):
            load_horizon(_write(tmp_path, content))

    def test_fewer_than_two_distinct_points_is_an_error(self, tmp_path):
        with pytest.raises(SchedulerFatalError, match="at least 2 distinct points"):
            load_horizon(_write(tmp_path, "Name\n0 10\n360 10\n"))

    def test_empty_file_is_an_error(self, tmp_path):
        with pytest.raises(SchedulerFatalError, match="at least 2 distinct points"):
            load_horizon(_write(tmp_path, "# nothing here\n"))

    def test_missing_file_names_the_path(self, tmp_path):
        missing = tmp_path / "nope.txt"
        with pytest.raises(SchedulerFatalError, match="not found.*nope.txt"):
            load_horizon(missing)


class TestHorizonAltitudeDeg:

    @pytest.fixture
    def real(self, tmp_path):
        return load_horizon(_write(tmp_path, REAL_PROFILE))

    def test_exact_points_return_their_altitude(self, real):
        assert horizon_altitude_deg(real, 270.0) == pytest.approx(34.0)
        assert horizon_altitude_deg(real, 340.0) == pytest.approx(46.0)
        assert horizon_altitude_deg(real, 220.0) == pytest.approx(0.0)
        assert horizon_altitude_deg(real, 0.0) == pytest.approx(36.0)

    def test_midpoint_is_linearly_interpolated(self, real):
        assert horizon_altitude_deg(real, 335.0) == pytest.approx(45.5)   # 330:45 -> 340:46
        assert horizon_altitude_deg(real, 15.0) == pytest.approx(29.5)    # 10:38 -> 20:21
        assert horizon_altitude_deg(real, 215.0) == pytest.approx(4.5)    # 210:9 -> 220:0
        assert horizon_altitude_deg(real, 225.0) == pytest.approx(4.5)    # 220:0 -> 230:9

    def test_360_is_treated_as_0_and_negative_azimuths_wrap(self, real):
        assert horizon_altitude_deg(real, 360.0) == pytest.approx(36.0)
        assert horizon_altitude_deg(real, -90.0) == pytest.approx(horizon_altitude_deg(real, 270.0))
        assert horizon_altitude_deg(real, 725.0) == pytest.approx(horizon_altitude_deg(real, 5.0))

    def test_wraps_across_north_when_the_profile_does_not_close(self, tmp_path):
        # Only 90 -> 10 and 270 -> 30: North (0/360) sits half-way across the
        # wrap-around between them, going 270 -> 360 -> 90 (a 180 deg arc).
        points = load_horizon(_write(tmp_path, "90 10\n270 30\n"))
        assert horizon_altitude_deg(points, 0.0) == pytest.approx(20.0)
        assert horizon_altitude_deg(points, 315.0) == pytest.approx(25.0)   # 45/180 into 270 -> 90
        assert horizon_altitude_deg(points, 45.0) == pytest.approx(15.0)
        assert horizon_altitude_deg(points, 180.0) == pytest.approx(20.0)   # 90 -> 270, other arc

    def test_flat_profile_is_flat_everywhere(self, tmp_path):
        points = load_horizon(_write(tmp_path, "0 15\n120 15\n240 15\n"))
        for az in (0.0, 37.0, 119.9, 200.0, 359.9):
            assert horizon_altitude_deg(points, az) == pytest.approx(15.0)
