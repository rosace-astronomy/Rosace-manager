"""
WebSocket broadcast of the spectro logger, for the "Journal" panel in the web GUI.
Bridges the sync logging.Handler.emit() call to the async WebSocket.send_json() via
call_soon_threadsafe, since log calls can occur outside the event loop's own coroutines.

Records are buffered and flushed on a fixed interval rather than broadcast one at a time:
a burst of log lines would otherwise spawn one asyncio task per line per client on the same
event loop that runs the observation orchestrator, and trigger one DOM redraw per line in
every connected browser tab.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

LOG_CREATE_MSG = "log.create"
FLUSH_INTERVAL = 0.25  # seconds — caps broadcast rate regardless of log burst size
MAX_BUFFERED = 500  # caps memory if a burst arrives faster than it can be flushed


class WebSocketLogHandler(logging.Handler):
    """Buffers formatted log records and flushes them in batches to connected GUI clients."""

    def __init__(self, level: int = logging.INFO):
        super().__init__(level)
        self.setFormatter(logging.Formatter("%(message)s"))
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._buffer: list[dict] = []
        self._dropped = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        loop.create_task(self._flush_loop())

    def add_client(self, websocket: WebSocket) -> None:
        self._clients.add(websocket)

    def remove_client(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    def emit(self, record: logging.LogRecord) -> None:
        # No history replay on connect: nothing is buffered while no client is listening.
        if not self._clients or self._loop is None:
            return
        payload = {"level": record.levelname, "text": self.format(record)}
        self._loop.call_soon_threadsafe(self._enqueue, payload)

    def _enqueue(self, payload: dict) -> None:
        if len(self._buffer) >= MAX_BUFFERED:
            self._buffer.pop(0)
            self._dropped += 1
        self._buffer.append(payload)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            if not self._buffer or not self._clients:
                continue

            lines = self._buffer
            self._buffer = []
            if self._dropped:
                lines = [{"level": "WARNING", "text": f"log_broadcast: {self._dropped} message(s) dropped"}] + lines
                self._dropped = 0

            message = {"msg": LOG_CREATE_MSG, "params": {"lines": lines}}
            for websocket in list(self._clients):
                asyncio.create_task(self._send(websocket, message))

    async def _send(self, websocket: WebSocket, message: dict) -> None:
        try:
            await websocket.send_json(message)
        except Exception:
            self._clients.discard(websocket)


ws_log_handler = WebSocketLogHandler()
