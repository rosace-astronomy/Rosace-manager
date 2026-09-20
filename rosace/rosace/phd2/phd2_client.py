"""
PHD2 JSON-RPC client (Event Monitoring protocol, TCP, default port 4400).

Connected once per session (INIT_SESSION) and kept open through every stage
of every observation, mirroring the INDI device adapters' own lifecycle —
see hardware_connectivity.py::connect_session_hardware(). Not a hardware/
Protocol implementation: there is exactly one PHD2 client, no per-backend
split (see phd2/__init__.py).

Wire format: one JSON object per line, CRLF-terminated. Requests carry an
"id" and get a matched {"result": ...}/{"error": {...}} response; the same
socket also carries unsolicited "Event" messages (AppState, SettleDone,
GuidingStopped, CalibrationFailed, ...) interleaved with responses — see
_read_loop(), which tells the two apart.

No dither() here, deliberately: dithering shifts the mount a few pixels
between frames, which is actively wrong for this app — it guides a
spectroscope slit, which needs the star held as precisely and stably as
possible, not moved around.

MultiStar guiding mode and the slit-position overlay have no PHD2 RPC
method (checked against PHD2's full documented method list) — neither is
exposed here; both remain settings to configure once, by hand, in the PHD2
profile/GUI itself.

Connection loss mid-session is not retried automatically here — same
precedent as every other device in hardware_indi/adapters/, none of which
reconnect on a dropped connection either. A dead connection fails every
pending call/event wait with DeviceConnectionError and makes any further
call raise the same immediately; re-establishing the connection only
happens the next time INIT_SESSION runs (a full session restart).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from typing import Any

from ..hardware.errors import DeviceCommandError, DeviceConnectionError, DeviceTimeoutError

log = logging.getLogger("spectro.phd2")

_DEFAULT_CALL_TIMEOUT = 10.0

# Events that all signal "PHD2 has stopped capturing", per the Event
# Monitoring docs -- see stop_capture()'s own docstring.
_STOP_EVENTS = ("AppState", "GuidingStopped", "LoopingExposuresStopped")


class Phd2Client:
    """One PHD2 Event Monitoring connection. settle/do_calibration come from
    PHD2_config in the active hardware YAML (settle{pixels,time,timeout},
    do_calibration) and are used as defaults by start_guiding() so callers
    don't need to know or re-pass them."""

    def __init__(self, host: str, port: int, settle: dict, do_calibration: bool = False):
        self._host = host
        self._port = port
        self._settle = settle
        self._do_calibration = do_calibration

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._connected = False

        self._id_counter = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._event_subscribers: dict[str, list[asyncio.Queue]] = {}
        self._last_app_state: str | None = None

    # ── Connection lifecycle ─────────────────────────────────────────

    async def connect(self, profile_name: str | None = None) -> None:
        """Opens the TCP connection, starts the background reader, then (if
        profile_name is given) loads that PHD2 profile and engages PHD2's
        own equipment (set_connected(True)) — the client is fully ready to
        guide by the time this returns."""
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        self._connected = True
        self._read_task = asyncio.create_task(self._read_loop())

        if profile_name is not None:
            profiles = await self._call("get_profiles")
            match = next((p for p in profiles if p["name"] == profile_name), None)
            if match is None:
                raise DeviceCommandError(
                    f"PHD2: profile {profile_name!r} not found "
                    f"(available: {[p['name'] for p in profiles]})"
                )
            await self._call("set_profile", [match["id"]])

        await self.set_connected(True)

    async def disconnect(self) -> None:
        self._connected = False
        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()

    # ── Wire plumbing ────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    log.warning(f"[PHD2] Ignoring malformed line: {line!r}")
                    continue

                if "Event" in msg:
                    self._dispatch_event(msg)
                elif "id" in msg:
                    future = self._pending.pop(msg["id"], None)
                    if future is not None and not future.done():
                        future.set_result(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(f"[PHD2] Reader loop failed: {e}")
        finally:
            self._fail_all_pending(DeviceConnectionError("PHD2 connection lost"))

    def _dispatch_event(self, msg: dict) -> None:
        event_name = msg["Event"]
        if event_name == "AppState":
            self._last_app_state = msg["State"]
        for queue in self._event_subscribers.get(event_name, []):
            queue.put_nowait(msg)

    def _fail_all_pending(self, exc: Exception) -> None:
        self._connected = False
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()
        for queues in self._event_subscribers.values():
            for queue in queues:
                queue.put_nowait(exc)

    def _subscribe(self, name: str) -> asyncio.Queue:
        """Every occurrence of event `name` received while subscribed is
        queued, in order — unlike a single one-shot future, this can't drop
        an event that arrives before the previous one has been consumed
        (PHD2 can emit several AppState events back-to-back, e.g. an
        intermediate state immediately followed by "Stopped"). A dead
        connection is signalled by pushing the exception itself onto every
        subscribed queue (see _fail_all_pending) — _dequeue() below
        recognizes and re-raises it."""
        queue: asyncio.Queue = asyncio.Queue()
        self._event_subscribers.setdefault(name, []).append(queue)
        return queue

    def _unsubscribe(self, name: str, queue: asyncio.Queue) -> None:
        subscribers = self._event_subscribers.get(name)
        if subscribers and queue in subscribers:
            subscribers.remove(queue)
            if not subscribers:
                del self._event_subscribers[name]

    def _subscribe_many(self, names: tuple[str, ...]) -> asyncio.Queue:
        """Like _subscribe(), but routes several different event names into
        one shared queue -- for a caller (stop_capture()) that reacts the
        same way to whichever of several distinct event names arrives
        first, without caring which."""
        queue: asyncio.Queue = asyncio.Queue()
        for name in names:
            self._event_subscribers.setdefault(name, []).append(queue)
        return queue

    def _unsubscribe_many(self, names: tuple[str, ...], queue: asyncio.Queue) -> None:
        for name in names:
            self._unsubscribe(name, queue)

    @staticmethod
    async def _dequeue(queue: asyncio.Queue, timeout: float | None) -> dict:
        item = await asyncio.wait_for(queue.get(), timeout=timeout)
        if isinstance(item, Exception):
            raise item
        return item

    async def _call(self, method: str, params: Any = None, timeout: float = _DEFAULT_CALL_TIMEOUT) -> Any:
        if not self._connected:
            raise DeviceConnectionError(f"PHD2: not connected (method={method!r})")

        request_id = next(self._id_counter)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        payload: dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            payload["params"] = params

        try:
            self._writer.write((json.dumps(payload) + "\r\n").encode())
            await self._writer.drain()
        except OSError as e:
            self._pending.pop(request_id, None)
            raise DeviceConnectionError(f"PHD2: failed to send {method!r}: {e}") from e

        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise DeviceTimeoutError(f"PHD2: no response to {method!r} within {timeout}s")

        if "error" in response:
            raise DeviceCommandError(f"PHD2: {method!r} failed: {response['error'].get('message')}")
        return response.get("result")

    # ── Guiding ──────────────────────────────────────────────────────

    async def start_guiding(self, recalibrate: bool | None = None) -> None:
        """Starts guiding and returns only once PHD2 confirms it has
        actually settled (SettleDone, Status==0) — by the time this
        returns, the target is locked within the configured pixel/time
        tolerance and it's safe to start acquiring science images. Races
        SettleDone against CalibrationFailed so a failed calibration
        surfaces immediately instead of only as a generic timeout once
        settling never happens.

        Self-heals PHD2's own equipment link first: found 2026-09-19/20
        that PHD2 can lose it mid-session (its own "Connect Equipment"
        going false, independently of this client's TCP connection, which
        stays up) — every subsequent guide() call then fails with "all
        equipment must be connected first" until reconnected by hand. This
        is the one place that failure actually bites, so it's the one
        place that checks and repairs it -- no background polling, no
        change needed anywhere else (pointing_72ed.py included)."""
        if recalibrate is None:
            recalibrate = self._do_calibration

        if not await self.get_connected():
            log.warning("[PHD2] Equipment link was down -- reconnecting before guiding")
            await self.set_connected(True)

        settle_queue = self._subscribe("SettleDone")
        calibration_queue = self._subscribe("CalibrationFailed")
        try:
            await self._call("guide", {"settle": self._settle, "recalibrate": recalibrate})

            timeout = self._settle["timeout"] + _DEFAULT_CALL_TIMEOUT
            settle_task = asyncio.ensure_future(self._dequeue(settle_queue, timeout=None))
            calibration_task = asyncio.ensure_future(self._dequeue(calibration_queue, timeout=None))
            done, pending = await asyncio.wait(
                [settle_task, calibration_task], timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()

            if not done:
                raise DeviceTimeoutError(f"PHD2: guiding did not settle within {timeout}s")

            if calibration_task in done:
                reason = calibration_task.result().get("Reason", "unknown reason")
                raise DeviceCommandError(f"PHD2: calibration failed: {reason}")

            msg = settle_task.result()
            if msg.get("Status", 1) != 0:
                raise DeviceCommandError(f"PHD2: guiding failed to settle: {msg.get('Error', 'unknown error')}")
        finally:
            self._unsubscribe("SettleDone", settle_queue)
            self._unsubscribe("CalibrationFailed", calibration_queue)

    async def stop_capture(self, timeout: float = 10.0) -> None:
        """PHD2's own universal stop: halts guiding and plain looping alike
        (continuous framing exposures with no guiding active, e.g. during
        initial framing before start_guiding() is ever called) — PHD2 has
        no separate "stop looping" command.

        No-op if PHD2 is already Stopped: calling stop_capture on an idle
        PHD2 doesn't emit a new event (nothing actually changes), so
        waiting for one would time out for no reason — found the hard way,
        calling this defensively at the start of a sub-plugin on a
        freshly-connected, never-yet-guided PHD2.

        Found the hard way (2026-08-11, real simulator testing), then
        confirmed against PHD2's own Event Monitoring documentation:
        stopping from active Guiding does NOT emit an AppState event at
        all -- PHD2 instead emits a distinct top-level "GuidingStopped"
        event (not a value of AppState's own "State" field, despite that
        being an easy assumption to make). Stopping from mere Looping
        (no guide star locked) emits "LoopingExposuresStopped" instead,
        the documented counterpart. This listens for whichever of the
        three (AppState="Stopped", GuidingStopped, LoopingExposuresStopped)
        is the actually-correct one for PHD2's current state, rather than
        assuming "AppState" alone is always how PHD2 announces this."""
        if await self.get_app_state() == "Stopped":
            return
        log.info("[PHD2] stop_capture: requesting stop")

        deadline = time.monotonic() + timeout
        queue = self._subscribe_many(_STOP_EVENTS)
        try:
            await self._call("stop_capture")
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DeviceTimeoutError(f"PHD2: stop_capture did not confirm Stopped state within {timeout}s")
                try:
                    msg = await self._dequeue(queue, timeout=remaining)
                except asyncio.TimeoutError:
                    raise DeviceTimeoutError(f"PHD2: stop_capture did not confirm Stopped state within {timeout}s")
                event = msg.get("Event")
                log.debug(f"[PHD2] stop_capture: received {event!r} while waiting")
                if event in ("GuidingStopped", "LoopingExposuresStopped"):
                    return
                if event == "AppState" and msg["State"] == "Stopped":
                    return
        finally:
            self._unsubscribe_many(_STOP_EVENTS, queue)

    # ── Lock position ────────────────────────────────────────────────

    async def set_lock_position(self, x: float, y: float, exact: bool = True) -> None:
        await self._call("set_lock_position", [x, y, exact])

    async def get_lock_position(self) -> tuple[float, float] | None:
        result = await self._call("get_lock_position")
        return tuple(result) if result is not None else None

    async def find_star(self, roi: list[int] | None = None) -> tuple[float, float]:
        """Auto-selects a guide star (PHD2's own "Auto-select Star"). roi,
        if given, is [x, y, width, height]. Returns the selected star's
        lock position."""
        params = [roi] if roi is not None else None
        result = await self._call("find_star", params)
        return tuple(result)

    # ── State / calibration ──────────────────────────────────────────

    async def get_connected(self) -> bool:
        """Whether PHD2's own "Connect Equipment" is currently engaged --
        distinct from this client's own TCP connection (self._connected),
        which can stay up while this goes false on PHD2's side. See
        start_guiding()'s own docstring for why this exists."""
        return await self._call("get_connected")

    async def set_connected(self, connected: bool) -> None:
        await self._call("set_connected", [connected])

    async def get_app_state(self) -> str:
        return await self._call("get_app_state")

    async def get_calibrated(self) -> bool:
        return await self._call("get_calibrated")

    async def get_calibration_data(self, which: str = "Mount") -> dict:
        """which: "Mount" or "AO". Returns PHD2's raw result dict as-is
        (e.g. calibrated/xAngle/xRate/xParity/yAngle/yRate/yParity)."""
        return await self._call("get_calibration_data", [which])

    async def clear_calibration(self, which: str | None = None) -> None:
        """which: "mount"/"ao"/"both"; omitted clears both (PHD2's own default)."""
        params = [which] if which is not None else None
        await self._call("clear_calibration", params)

    async def get_paused(self) -> bool:
        return await self._call("get_paused")

    async def set_paused(self, paused: bool, full: bool = False) -> None:
        """full also pauses PHD2's looping exposures, not just guide corrections."""
        params = [paused, "full"] if full else [paused]
        await self._call("set_paused", params)

    # ── Guide camera ─────────────────────────────────────────────────

    async def get_exposure(self) -> int:
        """Guide camera exposure time, in milliseconds."""
        return await self._call("get_exposure")

    async def set_exposure(self, exposure_ms: int) -> None:
        await self._call("set_exposure", [exposure_ms])

    async def get_star_image(self, size: int | None = None) -> dict:
        """Returns PHD2's raw result dict as-is (frame, width, height,
        star_pos, base64-encoded 16-bit pixels) — decoding pixels into a
        usable image is left to whichever future caller actually needs it."""
        params = [size] if size is not None else None
        return await self._call("get_star_image", params)
