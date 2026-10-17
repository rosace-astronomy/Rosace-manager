"""
Tests for guide_log.py: reading a real PHD2 GuideLog excerpt, building a
section from events, and the round trip between the two.
Run: pytest rosace/phd2/test_guide_log.py -v
"""

from datetime import datetime, timezone

import pytest

from rosace.phd2.guide_log import CSV_HEADER, GuideLogRecorder, parse_guide_log, settled_frame

# Excerpt of PHD2's own GuideLog, night of 2026-10-16 (first section, shortened)
REAL_LOG = """PHD2 version 2.6.13dev7 [Linux], Log version 2.5. Log enabled at 2026-10-16 19:06:53
INFO: Guiding parameter change, MultiStar = true

Guiding Begins at 2026-10-16 20:18:52
Equipment Profile = 72ED Stellarmate
Pixel scale = 2.88 arc-sec/px, Binning = 1, Focal length = 420 mm
Exposure = 2000 ms
Lock position = 546.997, 675.303, Star position = 546.614, 675.693, HFD = 2.88 px
Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,RAGuideDistance,DECGuideDistance,RADuration,RADirection,DECDuration,DECDirection,XStep,YStep,StarMass,SNR,ErrorCode
INFO: SETTLING STATE CHANGE, Settling started
1,3.050,"Mount",-0.498,0.260,0.493,-0.278,0.311,0.000,144,W,0,,,,225244,299.72,0
2,5.715,"Mount",-0.046,0.164,0.042,-0.166,0.000,0.000,0,,0,,,,224498,304.23,0
INFO: SETTLING STATE CHANGE, Settling complete
3,8.577,"Mount",0.011,0.383,-0.019,-0.382,0.000,0.000,0,,0,,,,233572,305.53,0
4,11.569,"Mount",-0.018,0.254,0.013,-0.254,0.000,-0.254,0,,100,N,,,222071,301.19,0
INFO: SET LOCK POSITION, new lock pos = 546.997, 675.303
5,16.552,"Mount",-4.459,2.075,4.416,-2.231,2.782,-2.231,1288,W,875,N,,,223596,298.07,0
6,69.499,"DROP",,,,,,,,,,,,,0,0.00,2,"Star lost - low SNR"
7,72.696,"Mount",0.325,-0.111,-0.322,0.123,-0.203,0.000,94,E,0,,,,218234,293.04,0
Guiding Ends at 2026-10-16 20:20:22

Calibration Begins at 2026-10-16 20:21:00
1,2.000,"Mount",1.0,1.0
"""


def _local_timestamp(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()


class TestParse:

    def test_real_excerpt(self):
        sections = parse_guide_log(REAL_LOG)

        assert len(sections) == 1
        section = sections[0]
        assert section.started_at == datetime.strptime("2026-10-16 20:18:52", "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc)
        assert section.ended_at is not None and section.ended_at > section.started_at
        assert section.pixel_scale == 2.88
        assert section.header[0] == "Equipment Profile = 72ED Stellarmate"
        assert len(section.steps) == 7
        step = section.steps[4]
        assert (step.frame, step.time_s, step.ra_raw_px, step.dec_raw_px) == (5, 16.552, 4.416, -2.231)
        assert (step.ra_duration_ms, step.ra_direction, step.dec_duration_ms, step.dec_direction) == (1288, "W", 875, "N")
        assert section.steps[5].dropped and section.steps[5].ra_raw_px is None
        assert section.infos == [(0, "SETTLING STATE CHANGE, Settling started"),
                                 (2, "SETTLING STATE CHANGE, Settling complete"),
                                 (4, "SET LOCK POSITION, new lock pos = 546.997, 675.303")]
        assert settled_frame(section) == 2

    def test_unreadable_row_names_its_line(self):
        text = REAL_LOG.replace("3,8.577,", "3,abc,")
        with pytest.raises(ValueError, match="GuideLog line 14"):
            parse_guide_log(text)


def _events(start: float) -> list[dict]:
    return [
        {"Event": "Version", "PHDVersion": "2.6.13dev7"},
        {"Event": "StartGuiding", "Timestamp": start},
        {"Event": "SettleBegin"},
        {"Event": "GuideStep", "Frame": 1, "Time": 3.05, "Mount": "INDI Mount", "dx": -0.498, "dy": 0.26,
         "RADistanceRaw": 0.493, "DECDistanceRaw": -0.278, "RADistanceGuide": 0.311, "DECDistanceGuide": 0.0,
         "RADuration": 144, "RADirection": "West", "StarMass": 225244, "SNR": 299.72, "ErrorCode": 0},
        {"Event": "Settling", "Distance": 0.5},
        {"Event": "SettleDone", "Status": 0},
        {"Event": "LockPositionSet", "X": 546.997, "Y": 675.303},
        {"Event": "StartGuiding", "Timestamp": start + 20},     # guide again while guiding: same section
        {"Event": "GuideStep", "Frame": 2, "Time": 5.715, "dx": -0.046, "dy": 0.164,
         "RADistanceRaw": 0.042, "DECDistanceRaw": -0.166, "RADistanceGuide": 0.0, "DECDistanceGuide": -0.166,
         "DECDuration": 100, "DECDirection": "North", "StarMass": 224498, "SNR": 304.23, "ErrorCode": 0},
        {"Event": "StarLost", "Frame": 3, "Time": 8.1, "StarMass": 0, "SNR": 0.0, "ErrorCode": 2,
         "Status": "Star lost - low SNR"},
        {"Event": "GuidingStopped", "Timestamp": start + 90},
    ]


class TestRecorder:

    def test_section_text_in_phd2_format(self):
        recorder = GuideLogRecorder()
        start = _local_timestamp("2026-10-16 20:18:52")
        opened = [recorder.on_event(event) for event in _events(start)]
        recorder.set_header(["Equipment Profile = 72ED Stellarmate", "Pixel scale = 2.88 arc-sec/px"])

        assert opened.count(True) == 1
        section = recorder.section()
        assert section.started_at == datetime.fromtimestamp(start, timezone.utc)
        assert section.text.splitlines() == [
            "PHD2 version 2.6.13dev7 [recorded by Rosace], Log version 2.5. Log enabled at 2026-10-16 20:18:52",
            "",
            "Guiding Begins at 2026-10-16 20:18:52",
            "Equipment Profile = 72ED Stellarmate",
            "Pixel scale = 2.88 arc-sec/px",
            CSV_HEADER,
            "INFO: SETTLING STATE CHANGE, Settling started",
            '1,3.050,"Mount",-0.498,0.260,0.493,-0.278,0.311,0.000,144,W,0,,,,225244,299.72,0',
            "INFO: SETTLING STATE CHANGE, Settling complete",
            "INFO: SET LOCK POSITION, new lock pos = 546.997, 675.303",
            '2,5.715,"Mount",-0.046,0.164,0.042,-0.166,0.000,-0.166,0,,100,N,,,224498,304.23,0',
            '3,8.100,"DROP",,,,,,,,,,,,,0,0.00,2,"Star lost - low SNR"',
            "Guiding Ends at 2026-10-16 20:20:22",
        ]

    def test_round_trip(self):
        recorder = GuideLogRecorder()
        for event in _events(_local_timestamp("2026-10-16 20:18:52")):
            recorder.on_event(event)
        recorder.set_header(["Pixel scale = 2.88 arc-sec/px"])

        (section,) = parse_guide_log(recorder.section().text)

        assert section.started_at == recorder.section().started_at
        assert section.pixel_scale == 2.88
        assert [(s.frame, s.dropped) for s in section.steps] == [(1, False), (2, False), (3, True)]
        assert section.steps[1].dec_guide_px == -0.166 and section.steps[1].dec_direction == "N"

    def test_events_outside_a_section_are_ignored(self):
        recorder = GuideLogRecorder()
        recorder.on_event({"Event": "GuideStep", "Frame": 1, "Time": 1.0})
        recorder.on_event({"Event": "LockPositionSet", "X": 1.0, "Y": 2.0})

        assert recorder.section() is None
        assert recorder.lock_position == (1.0, 2.0)

    def test_new_guiding_after_a_stop_replaces_the_section(self):
        recorder = GuideLogRecorder()
        start = _local_timestamp("2026-10-16 20:18:52")
        for event in _events(start):
            recorder.on_event(event)

        assert recorder.on_event({"Event": "StartGuiding", "Timestamp": start + 600}) is True
        section = recorder.section()
        assert section.started_at == datetime.fromtimestamp(start + 600, timezone.utc)
        assert "GuideStep" not in section.text and section.text.count(",\"Mount\",") == 0
