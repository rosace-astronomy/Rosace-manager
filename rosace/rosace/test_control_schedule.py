"""Tests for control_schedule.py (local time of day -> next UTC instant).

Independent of the machine's timezone: each test checks the local time of day
of the result and where it falls, never a hard-coded UTC instant."""

from datetime import datetime, timedelta, timezone

import pytest

from rosace.control_schedule import next_occurrence

NOW = datetime(2026, 10, 11, 15, 0, 0, tzinfo=timezone.utc)


def _local_hhmm(instant: datetime) -> str:
    return instant.astimezone().strftime("%H:%M")


class TestNextOccurrence:
    @pytest.mark.parametrize("hhmm", ["00:00", "05:30", "15:00", "21:20", "23:59"])
    def test_local_time_of_day_within_24_hours(self, hhmm):
        result = next_occurrence(hhmm, NOW)
        assert _local_hhmm(result) == hhmm
        assert timedelta(0) < result - NOW <= timedelta(hours=25)  # 25: a daylight-saving day

    def test_exactly_now_means_tomorrow(self):
        hhmm = _local_hhmm(NOW)
        result = next_occurrence(hhmm, NOW)
        assert _local_hhmm(result) == hhmm
        assert timedelta(hours=23) <= result - NOW <= timedelta(hours=25)

    def test_stop_resolved_after_the_scheduled_start(self):
        """A stop time earlier in the day than the start belongs to the day after the start."""
        start = next_occurrence("21:00", NOW)
        stop = next_occurrence("20:00", start)
        assert _local_hhmm(stop) == "20:00"
        assert timedelta(hours=22) <= stop - start <= timedelta(hours=24)

    def test_result_is_aware_utc(self):
        assert next_occurrence("21:20", NOW).tzinfo == timezone.utc


class TestInvalid:
    @pytest.mark.parametrize("hhmm", ["25:00", "9h30", "", "9:30", "12:60", "12:30:00"])
    def test_malformed_time_raises(self, hhmm):
        with pytest.raises(ValueError):
            next_occurrence(hhmm, NOW)
