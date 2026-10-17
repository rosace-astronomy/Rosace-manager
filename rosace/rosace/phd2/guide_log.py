"""
PHD2's GuideLog format (Log version 2.5), both ways: GuideLogRecorder builds a
guiding section from PHD2's Event Monitoring events, parse_guide_log() reads a
GuideLog file back (one written by Rosace, or PHD2's own).

Rosace cannot read the GuideLog PHD2 writes on its own machine, so it rebuilds
the section of each observation from the events it already receives on its
connection: the same lines, in the same order, as PHD2 writes them --

    Guiding Begins at <local time>
    <header lines>                  (what PHD2's RPC methods can tell)
    Frame,Time,mount,dx,dy,...      (CSV header)
    1,3.050,"Mount",-0.498,...      (GuideStep)
    20,69.499,"DROP",,,...          (StarLost)
    INFO: SETTLING STATE CHANGE, Settling started
    INFO: SET LOCK POSITION, new lock pos = x, y
    Guiding Ends at <local time>

Times are local, like PHD2's: parse_guide_log() converts them to UTC with the
server's own time zone (Rosace already assumes the server runs in the site's).

RA/Dec distances are in guide-camera pixels, signed like PHD2's: a positive
raw RA error is corrected by a West pulse, a positive Dec error by a South
pulse; the GuideDistance columns carry the error's sign.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

LOG_VERSION = "2.5"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
CSV_HEADER = ("Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,RAGuideDistance,DECGuideDistance,"
              "RADuration,RADirection,DECDuration,DECDirection,XStep,YStep,StarMass,SNR,ErrorCode")
BEGINS = "Guiding Begins at "
ENDS = "Guiding Ends at "
DIRECTIONS = {"East": "E", "West": "W", "North": "N", "South": "S"}

_PIXEL_SCALE = re.compile(r"Pixel scale = ([\d.]+) arc-sec/px")


def _local(timestamp: float | None) -> str:
    when = datetime.fromtimestamp(timestamp) if timestamp else datetime.now()
    return when.strftime(TIME_FORMAT)


def _number(value, digits: int) -> str:
    return f"{float(value):.{digits}f}" if value is not None else ""


@dataclass
class RecordedSection:
    started_at: datetime    # UTC, PHD2's own StartGuiding time
    text: str
    received_at: datetime | None = None     # UTC, when Rosace received StartGuiding


@dataclass
class _Section:
    started_at: datetime
    begins: str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    header: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    ends: str | None = None


class GuideLogRecorder:
    """Builds the current guiding section from PHD2 events (on_event()).

    StartGuiding opens a new section, replacing the previous one -- unless one
    is still open: PHD2 keeps one section when guide is called again while it
    already guides (pointing_72ed's second start_guiding()). GuidingStopped
    closes it. The header lines come separately (set_header()), read by RPC
    once guiding has started, and are placed before the CSV rows whatever the
    order they arrive in."""

    def __init__(self) -> None:
        self.phd2_version: str | None = None
        self.lock_position: tuple[float, float] | None = None
        self._section: _Section | None = None
        self._settling = False

    @property
    def is_open(self) -> bool:
        return self._section is not None and self._section.ends is None

    def on_event(self, msg: dict) -> bool:
        """Records one event. True when it opened a new section (the caller
        then reads the header lines)."""
        name = msg.get("Event")
        if name == "Version":
            self.phd2_version = msg.get("PHDVersion")
        elif name == "LockPositionSet":
            self.lock_position = (float(msg["X"]), float(msg["Y"]))
            self._info(f"SET LOCK POSITION, new lock pos = {self.lock_position[0]:.3f}, {self.lock_position[1]:.3f}")
        elif name == "StartGuiding":
            if self.is_open:
                return False
            timestamp = msg.get("Timestamp") or datetime.now().timestamp()
            self._section = _Section(datetime.fromtimestamp(timestamp, timezone.utc), BEGINS + _local(timestamp))
            self._settling = False
            return True
        elif name in ("SettleBegin", "Settling"):
            if not self._settling:
                self._settling = True
                self._info("SETTLING STATE CHANGE, Settling started")
        elif name == "SettleDone":
            self._settling = False
            status = "complete" if msg.get("Status", 1) == 0 else "failed"
            self._info(f"SETTLING STATE CHANGE, Settling {status}")
        elif name == "GuideStep":
            self._row(self._guide_step(msg))
        elif name == "StarLost":
            self._row(self._star_lost(msg))
        elif name == "GuidingStopped":
            if self.is_open:
                self._section.ends = ENDS + _local(msg.get("Timestamp"))
        return False

    def set_header(self, lines: list[str]) -> None:
        if self._section is not None:
            self._section.header = list(lines)

    def section(self) -> RecordedSection | None:
        """The current (or last) section as GuideLog text; None before any."""
        s = self._section
        if s is None:
            return None
        version = self.phd2_version or "unknown"
        lines = [f"PHD2 version {version} [recorded by Rosace], Log version {LOG_VERSION}. "
                 f"Log enabled at {s.begins[len(BEGINS):]}", "", s.begins, *s.header, CSV_HEADER, *s.lines]
        if s.ends is not None:
            lines.append(s.ends)
        return RecordedSection(s.started_at, "\n".join(lines) + "\n", s.received_at)

    def _info(self, text: str) -> None:
        if self.is_open:
            self._section.lines.append(f"INFO: {text}")

    def _row(self, line: str) -> None:
        if self.is_open:
            self._section.lines.append(line)

    @staticmethod
    def _guide_step(msg: dict) -> str:
        fields = [
            str(msg.get("Frame", "")), _number(msg.get("Time"), 3), '"Mount"',
            _number(msg.get("dx"), 3), _number(msg.get("dy"), 3),
            _number(msg.get("RADistanceRaw"), 3), _number(msg.get("DECDistanceRaw"), 3),
            _number(msg.get("RADistanceGuide", 0.0), 3), _number(msg.get("DECDistanceGuide", 0.0), 3),
            str(int(msg.get("RADuration", 0))), DIRECTIONS.get(msg.get("RADirection"), ""),
            str(int(msg.get("DECDuration", 0))), DIRECTIONS.get(msg.get("DECDirection"), ""),
            "", "",                                     # XStep, YStep: adaptive optics only
            str(int(msg.get("StarMass", 0))), _number(msg.get("SNR", 0.0), 2), str(int(msg.get("ErrorCode", 0))),
        ]
        return ",".join(fields)

    @staticmethod
    def _star_lost(msg: dict) -> str:
        reason = str(msg.get("Status", "Star lost")).replace('"', "'")
        return (f'{msg.get("Frame", "")},{_number(msg.get("Time"), 3)},"DROP",' + "," * 12
                + f'{int(msg.get("StarMass", 0))},{_number(msg.get("SNR", 0.0), 2)},'
                  f'{int(msg.get("ErrorCode", 0))},"{reason}"')


# ── Reading ──────────────────────────────────────────────────────────

@dataclass
class GuideStep:
    frame: int
    time_s: float               # seconds since guiding began
    dropped: bool
    ra_raw_px: float | None = None
    dec_raw_px: float | None = None
    ra_guide_px: float | None = None
    dec_guide_px: float | None = None
    ra_duration_ms: int = 0
    ra_direction: str = ""
    dec_duration_ms: int = 0
    dec_direction: str = ""
    star_mass: float = 0.0
    snr: float = 0.0


@dataclass
class GuideSection:
    started_at: datetime        # UTC
    ended_at: datetime | None = None
    pixel_scale: float | None = None    # arcsec per pixel, as PHD2 knows it
    header: list[str] = field(default_factory=list)
    steps: list[GuideStep] = field(default_factory=list)
    infos: list[tuple[int, str]] = field(default_factory=list)  # (last frame before it, text)


SETTLING_COMPLETE = "SETTLING STATE CHANGE, Settling complete"


def settled_frame(section: GuideSection) -> int | None:
    """The last frame before the section's last completed settling (0 when
    it completed before any frame); None when PHD2 never settled in it."""
    frames = [frame for frame, text in section.infos if text == SETTLING_COMPLETE]
    return frames[-1] if frames else None


def _utc(local_text: str) -> datetime:
    return datetime.strptime(local_text.strip(), TIME_FORMAT).astimezone(timezone.utc)


def _float(value: str) -> float | None:
    return float(value) if value not in ("", None) else None


def _step(row: list[str]) -> GuideStep:
    frame, time_s = int(row[0]), float(row[1])
    if row[2] == "DROP":
        return GuideStep(frame, time_s, True, star_mass=_float(row[15]) or 0.0, snr=_float(row[16]) or 0.0)
    return GuideStep(
        frame, time_s, False,
        ra_raw_px=_float(row[5]), dec_raw_px=_float(row[6]),
        ra_guide_px=_float(row[7]), dec_guide_px=_float(row[8]),
        ra_duration_ms=int(_float(row[9]) or 0), ra_direction=row[10],
        dec_duration_ms=int(_float(row[11]) or 0), dec_direction=row[12],
        star_mass=_float(row[15]) or 0.0, snr=_float(row[16]) or 0.0,
    )


def parse_guide_log(source: str | Path) -> list[GuideSection]:
    """Every guiding section of a GuideLog (a path, or its text). Lines
    outside a guiding section (calibration, the file's own header) are
    ignored; a row that cannot be read raises ValueError naming its line."""
    text = Path(source).read_text(errors="replace") if isinstance(source, Path) else source
    sections: list[GuideSection] = []
    current: GuideSection | None = None
    in_rows = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if line.startswith(BEGINS):
            current = GuideSection(_utc(line[len(BEGINS):]))
            sections.append(current)
            in_rows = False
        elif current is None:
            continue
        elif line.startswith(ENDS) or line.startswith("Calibration Begins at "):
            if line.startswith(ENDS):
                current.ended_at = _utc(line[len(ENDS):])
            current = None
        elif line.startswith("Frame,Time,"):
            in_rows = True
        elif line.startswith("INFO:"):
            last = current.steps[-1].frame if current.steps else 0
            current.infos.append((last, line[len("INFO:"):].strip()))
        elif in_rows and line[:1].isdigit():
            try:
                current.steps.append(_step(next(csv.reader([line]))))
            except (ValueError, IndexError) as e:
                raise ValueError(f"GuideLog line {number}: {line!r}: {e}") from e
        elif not in_rows and line:
            current.header.append(line)
            match = _PIXEL_SCALE.search(line)
            if match:
                current.pixel_scale = float(match.group(1))
    return sections
