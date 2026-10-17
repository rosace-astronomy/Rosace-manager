"""
A PHD2 stand-in for the demo mode: a TCP server speaking PHD2's Event
Monitoring protocol (one JSON object per line, requests answered by id,
unsolicited "Event" messages), so the real phd2/phd2_client.py connects to it
unchanged.

It answers the calls Rosace makes (profiles, equipment connection, app
state, guide, stop_capture, lock position, and the guide log header's
pixel scale/equipment) and sends the events Rosace waits for: StartGuiding
when guiding starts, SettleBegin then SettleDone settle_seconds after a
guide call, LockPositionSet, GuidingStopped after a stop. The lock position
PHD2 auto-selects is the one PHD2 locked on during the recorded observation
the replay mount points at (bench.py), until Rosace moves it with
set_lock_position. Nothing is guided: the frames are recorded.

While guiding, it replays the GuideStep and StarLost events of the recorded
observation's guide log section (bench.guide_log(), when the dataset holds
one), one frame every step_seconds, with their recorded values and times --
so Rosace writes a real guide log. StartGuiding and GuidingStopped carry the
recorded section's own times, so the guide log lines up with the replayed
frames, which keep their recording night's dates. The settling and lock
position lines come from Rosace's own calls, not from the recording.

start_fake_phd2() starts it on the active hardware profile's PHD2_config
host/port, only for a replay profile (one declaring replay_dataset_dir).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import yaml

from ..config import HARDWARE_CONFIG_PATH
from ..phd2.guide_log import GuideSection, parse_guide_log
from .bench import dataset_dir_from_profile, get_bench

log = logging.getLogger("spectro.replay.fake_phd2")

DEFAULT_LOCK_POSITION = (960.0, 600.0)
_DIRECTIONS = {"E": "East", "W": "West", "N": "North", "S": "South"}


class FakePhd2Server:
    def __init__(self, host: str, port: int, profile_name: str | None = None, settle_seconds: float = 3.0,
                 step_seconds: float = 0.2) -> None:
        self.host, self.port = host, port
        self.profile_name = profile_name or "Replay"
        self.settle_seconds = settle_seconds
        self.step_seconds = step_seconds
        self._replay_task: asyncio.Task | None = None
        self._section: GuideSection | None = None
        self._replayed_s = 0.0          # recorded time of the last replayed frame
        self.app_state = "Stopped"
        self.equipment_connected = False
        self.lock_position: tuple[float, float] | None = None
        self._server: asyncio.base_events.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info(f"[REPLAY] Fake PHD2 listening on {self.host}:{self.port}")

    async def stop(self) -> None:
        """Closes the server and its clients' connections first: wait_closed()
        waits for every connection to end, and a session still running at
        server shutdown keeps its PHD2 client connected (Ctrl+C then hung)."""
        self._stop_replay()
        if self._server is not None:
            self._server.close()
            for writer in list(self._writers):
                writer.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
            except asyncio.TimeoutError:
                log.warning("[REPLAY] Fake PHD2: connections still open at shutdown, not waited for")
            self._server = None

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        def send(message: dict[str, Any]) -> None:
            writer.write((json.dumps(message) + "\r\n").encode())

        self._writers.add(writer)
        send({"Event": "Version", "PHDVersion": "replay", "MsgVersion": 1})
        send({"Event": "AppState", "State": self.app_state})
        try:
            while line := await reader.readline():
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    result = self._call(request.get("method"), request.get("params"), send)
                    send({"jsonrpc": "2.0", "result": result, "id": request.get("id")})
                except ValueError as e:
                    send({"jsonrpc": "2.0", "error": {"code": 1, "message": str(e)}, "id": request.get("id")})
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def _set_state(self, state: str, send) -> None:
        self.app_state = state
        send({"Event": "AppState", "State": state})

    def _recorded_section(self) -> GuideSection | None:
        path = get_bench().guide_log()
        if path is None:
            return None
        try:
            sections = parse_guide_log(path)
        except (OSError, ValueError) as e:
            log.warning(f"[REPLAY] Fake PHD2: guide log {path.name} unreadable: {e}")
            return None
        return sections[-1] if sections else None

    async def _replay(self, send) -> None:
        """The recorded section's guided and lost frames, one every step_seconds."""
        for step in self._section.steps if self._section is not None else []:
            await asyncio.sleep(self.step_seconds)
            self._replayed_s = step.time_s
            if step.dropped:
                send({"Event": "StarLost", "Timestamp": time.time(), "Frame": step.frame, "Time": step.time_s,
                      "StarMass": step.star_mass, "SNR": step.snr, "ErrorCode": 2, "Status": "Star lost (replayed)"})
                continue
            send({"Event": "GuideStep", "Timestamp": time.time(), "Frame": step.frame, "Time": step.time_s,
                  "Mount": "Replay mount", "dx": 0.0, "dy": 0.0,
                  "RADistanceRaw": step.ra_raw_px, "DECDistanceRaw": step.dec_raw_px,
                  "RADistanceGuide": step.ra_guide_px or 0.0, "DECDistanceGuide": step.dec_guide_px or 0.0,
                  "RADuration": step.ra_duration_ms, "RADirection": _DIRECTIONS.get(step.ra_direction, ""),
                  "DECDuration": step.dec_duration_ms, "DECDirection": _DIRECTIONS.get(step.dec_direction, ""),
                  "StarMass": step.star_mass, "SNR": step.snr, "ErrorCode": 0})

    def _recorded_time(self, seconds: float) -> float:
        """The recorded section's start plus seconds, as a Unix time; now
        without a recorded section."""
        if self._section is None:
            return time.time()
        return self._section.started_at.timestamp() + seconds

    def _stop_replay(self) -> None:
        if self._replay_task is not None:
            self._replay_task.cancel()
            self._replay_task = None

    def _call(self, method: str, params: Any, send) -> Any:
        if method == "get_profiles":
            return [{"id": 1, "name": self.profile_name}]
        if method in ("set_profile", "clear_calibration", "set_paused", "set_exposure"):
            return 0
        if method == "get_connected":
            return self.equipment_connected
        if method == "set_connected":
            self.equipment_connected = bool(params[0])
            return 0
        if method == "get_app_state":
            return self.app_state
        if method == "get_calibrated":
            return True
        if method == "get_paused":
            return False
        if method == "get_exposure":
            return 1000
        if method == "get_calibration_data":
            return {}
        if method == "get_profile":
            return {"id": 1, "name": self.profile_name}
        if method == "get_current_equipment":
            return {"camera": {"name": "Replay camera", "connected": True},
                    "mount": {"name": "Replay mount", "connected": True}}
        if method == "get_pixel_scale":
            section = self._section or self._recorded_section()
            if section is None or section.pixel_scale is None:
                raise ValueError("pixel scale unknown (no recorded guide log)")
            return section.pixel_scale
        if method in ("guide", "find_star"):
            if not self.equipment_connected:
                raise ValueError("all equipment must be connected first")
            if self.lock_position is None:
                self.lock_position = get_bench().lock_position() or DEFAULT_LOCK_POSITION
            if method == "find_star":
                return list(self.lock_position)
            if self.app_state != "Guiding":
                self._section = self._recorded_section()
                self._replayed_s = 0.0
                self._set_state("Guiding", send)
                send({"Event": "StartGuiding", "Timestamp": self._recorded_time(0.0)})
                self._stop_replay()
                self._replay_task = asyncio.get_running_loop().create_task(self._replay(send))
            send({"Event": "SettleBegin", "Timestamp": time.time()})
            asyncio.get_running_loop().call_later(
                self.settle_seconds, send, {"Event": "SettleDone", "Status": 0, "TotalFrames": 3, "DroppedFrames": 0}
            )
            return 0
        if method == "stop_capture":
            self._stop_replay()
            if self.app_state == "Guiding":
                send({"Event": "GuidingStopped", "Timestamp": self._recorded_time(self._replayed_s + 1.0)})
            self._set_state("Stopped", send)
            self.lock_position = None
            return 0
        if method == "get_lock_position":
            return list(self.lock_position) if self.lock_position is not None else None
        if method == "set_lock_position":
            self.lock_position = (float(params[0]), float(params[1]))
            send({"Event": "LockPositionSet", "Timestamp": time.time(), "X": self.lock_position[0],
                  "Y": self.lock_position[1]})
            return 0
        raise ValueError(f"method {method!r} not supported by the fake PHD2")


async def start_fake_phd2() -> FakePhd2Server | None:
    """Starts the fake PHD2 for a replay hardware profile (None for any other
    profile), on its PHD2_config host/port."""
    if dataset_dir_from_profile() is None:
        return None
    with open(HARDWARE_CONFIG_PATH, encoding="utf-8") as f:
        phd2_cfg = (yaml.safe_load(f) or {}).get("PHD2_config", {})
    server = FakePhd2Server(phd2_cfg.get("host", "127.0.0.1"), int(phd2_cfg.get("port", 4499)),
                            phd2_cfg.get("profile_name"), float(phd2_cfg.get("replay_settle_seconds", 3.0)),
                            float(phd2_cfg.get("replay_step_seconds", 0.2)))
    await server.start()
    return server
