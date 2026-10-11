"""Tests for control_schedule.py (time of day -> next instant)."""

from datetime import datetime, timedelta, timezone

import pytest

from rosace.control_schedule import next_occurrence

NOW = datetime(2026, 10, 11, 15, 0, 0, tzinfo=timezone.utc)


class TestUtc:
    def test_later_today(self):
        assert next_occurrence("21:20", "utc", NOW) == datetime(2026, 10, 11, 21, 20, tzinfo=timezone.utc)

    def test_already_past_means_tomorrow(self):
        assert next_occurrence("05:30", "utc", NOW) == datetime(2026, 10, 12, 5, 30, tzinfo=timezone.utc)

    def test_exactly_now_means_tomorrow(self):
        assert next_occurrence("15:00", "utc", NOW) == datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)

    def test_stop_resolved_after_the_scheduled_start(self):
        start = next_occurrence("21:00", "utc", NOW)
        stop = next_occurrence("20:00", "utc", start)
        assert stop == datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)

    def test_result_is_aware_utc(self):
        assert next_occurrence("21:20", "utc", NOW).tzinfo == timezone.utc


class TestLocal:
    """Independent of the machine's timezone: checks the local time of day of the
    result and that it falls within the next 24 hours."""

    @pytest.mark.parametrize("hhmm", ["00:00", "05:30", "15:00", "21:20", "23:59"])
    def test_local_time_of_day_within_24_hours(self, hhmm):
        result = next_occurrence(hhmm, "local", NOW)
        local = result.astimezone()
        assert local.strftime("%H:%M") == hhmm
        assert timedelta(0) < result - NOW <= timedelta(hours=25)  # 25: a daylight-saving day
        assert result.tzinfo == timezone.utc


class TestInvalid:
    @pytest.mark.parametrize("hhmm", ["25:00", "9h30", "", "9:30", "12:60", "12:30:00"])
    def test_malformed_time_raises(self, hhmm):
        with pytest.raises(ValueError):
            next_occurrence(hhmm, "utc", NOW)
