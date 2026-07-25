"""
Tests for log_broadcast.py, next to it (same convention as test_observation_file.py).
Each test builds its own WebSocketLogHandler instance rather than importing the module-level
singleton, to stay isolated from the real spectro logger and from other tests.
"""

import asyncio
import logging

import pytest

from rosace.log_broadcast import LOG_CREATE_MSG, MAX_BUFFERED, WebSocketLogHandler


def _make_record(level: int = logging.INFO, msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="spectro.test", level=level, pathname=__file__, lineno=1,
        msg=msg, args=None, exc_info=None,
    )


class FakeWebSocket:
    """Stands in for a fastapi.WebSocket: records what it was sent, can be made to fail."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        if self.fail:
            raise RuntimeError("connection closed")
        self.sent.append(data)


class TestEnqueue:
    """_enqueue() is pure and synchronous — tested directly, no event loop needed."""

    def test_appends_payload(self):
        handler = WebSocketLogHandler()
        handler._enqueue({"level": "INFO", "text": "a"})
        assert handler._buffer == [{"level": "INFO", "text": "a"}]

    def test_caps_buffer_and_counts_dropped(self):
        handler = WebSocketLogHandler()
        for i in range(MAX_BUFFERED + 10):
            handler._enqueue({"level": "INFO", "text": str(i)})

        assert len(handler._buffer) == MAX_BUFFERED
        assert handler._dropped == 10
        # oldest entries were evicted first (FIFO)
        assert handler._buffer[0] == {"level": "INFO", "text": "10"}
        assert handler._buffer[-1] == {"level": "INFO", "text": str(MAX_BUFFERED + 9)}


class TestEmit:
    """emit() bridges the sync logging call to the buffer via call_soon_threadsafe."""

    async def test_no_op_without_clients(self):
        handler = WebSocketLogHandler()
        handler._loop = asyncio.get_running_loop()

        handler.emit(_make_record())
        await asyncio.sleep(0)

        assert handler._buffer == []

    async def test_no_op_without_bound_loop(self):
        handler = WebSocketLogHandler()
        handler.add_client(FakeWebSocket())

        handler.emit(_make_record())  # handler._loop is still None

        assert handler._buffer == []

    async def test_buffers_when_a_client_is_connected(self):
        handler = WebSocketLogHandler()
        handler._loop = asyncio.get_running_loop()
        handler.add_client(FakeWebSocket())

        handler.emit(_make_record(msg="observation started"))
        await asyncio.sleep(0)  # let the call_soon_threadsafe callback run

        assert len(handler._buffer) == 1
        assert handler._buffer[0]["level"] == "INFO"
        assert "observation started" in handler._buffer[0]["text"]

    async def test_debug_is_filtered_out_by_the_logger(self):
        """emit() itself doesn't filter by level — the attached logger does, via handler.level."""
        handler = WebSocketLogHandler()  # default level=INFO
        handler._loop = asyncio.get_running_loop()
        handler.add_client(FakeWebSocket())

        logger = logging.getLogger("spectro.test.filtering")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.propagate = False
        try:
            logger.debug("should be dropped")
            logger.info("should pass")
            await asyncio.sleep(0)
        finally:
            logger.removeHandler(handler)

        texts = [entry["text"] for entry in handler._buffer]
        assert any("should pass" in t for t in texts)
        assert not any("should be dropped" in t for t in texts)


class TestSend:
    async def test_delivers_the_message(self):
        handler = WebSocketLogHandler()
        ws = FakeWebSocket()
        message = {"msg": LOG_CREATE_MSG, "params": {"lines": [{"level": "INFO", "text": "x"}]}}

        await handler._send(ws, message)

        assert ws.sent == [message]

    async def test_removes_client_when_send_fails(self):
        handler = WebSocketLogHandler()
        ws = FakeWebSocket(fail=True)
        handler.add_client(ws)

        await handler._send(ws, {"msg": LOG_CREATE_MSG, "params": {"lines": []}})

        assert ws not in handler._clients


class TestFlushLoop:
    async def test_flush_delivers_batched_lines_and_clears_buffer(self, monkeypatch):
        import rosace.log_broadcast as mod
        monkeypatch.setattr(mod, "FLUSH_INTERVAL", 0.01)

        handler = WebSocketLogHandler()
        ws = FakeWebSocket()
        handler.add_client(ws)
        handler.bind_loop(asyncio.get_running_loop())

        handler.emit(_make_record(msg="one"))
        handler.emit(_make_record(msg="two"))

        await asyncio.sleep(0.05)

        assert len(ws.sent) == 1
        message = ws.sent[0]
        assert message["msg"] == LOG_CREATE_MSG
        texts = [line["text"] for line in message["params"]["lines"]]
        assert any("one" in t for t in texts)
        assert any("two" in t for t in texts)
        assert handler._buffer == []

    async def test_flush_prepends_dropped_warning(self, monkeypatch):
        import rosace.log_broadcast as mod
        monkeypatch.setattr(mod, "FLUSH_INTERVAL", 0.01)

        handler = WebSocketLogHandler()
        ws = FakeWebSocket()
        handler.add_client(ws)
        handler._dropped = 3
        handler._buffer = [{"level": "INFO", "text": "kept"}]
        handler.bind_loop(asyncio.get_running_loop())

        await asyncio.sleep(0.05)

        lines = ws.sent[0]["params"]["lines"]
        assert lines[0]["level"] == "WARNING"
        assert "3" in lines[0]["text"]
        assert lines[1]["text"] == "kept"
        assert handler._dropped == 0
