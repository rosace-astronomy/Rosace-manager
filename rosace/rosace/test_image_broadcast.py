"""
Tests for image_broadcast.py, next to it (same convention as test_log_broadcast.py).
Each test builds its own WebSocketImageBroadcaster instance rather than importing the
module-level singleton, to stay isolated from other tests.
"""

import asyncio
import threading

from rosace.image_broadcast import IMAGE_NEW_MSG, WebSocketImageBroadcaster


class FakeWebSocket:
    """Stands in for a fastapi.WebSocket: records what it was sent, can be made to fail."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[dict] = []
        self.sent_from_threads: list[str] = []

    async def send_json(self, data: dict) -> None:
        if self.fail:
            raise RuntimeError("connection closed")
        self.sent.append(data)
        self.sent_from_threads.append(threading.current_thread().name)


class TestBroadcast:

    async def test_no_op_without_clients(self):
        broadcaster = WebSocketImageBroadcaster()

        await broadcaster.broadcast("guidage")  # must not raise

    async def test_sends_message_to_connected_client(self):
        broadcaster = WebSocketImageBroadcaster()
        ws = FakeWebSocket()
        broadcaster.add_client(ws)

        await broadcaster.broadcast("guidage")

        assert ws.sent == [{"msg": IMAGE_NEW_MSG, "params": {"panel": "guidage"}}]

    async def test_sends_to_every_connected_client(self):
        broadcaster = WebSocketImageBroadcaster()
        ws1, ws2 = FakeWebSocket(), FakeWebSocket()
        broadcaster.add_client(ws1)
        broadcaster.add_client(ws2)

        await broadcaster.broadcast("guidage")

        assert len(ws1.sent) == 1
        assert len(ws2.sent) == 1

    async def test_includes_source_when_given(self):
        broadcaster = WebSocketImageBroadcaster()
        ws = FakeWebSocket()
        broadcaster.add_client(ws)

        await broadcaster.broadcast("guidage", source="focus")

        assert ws.sent == [{"msg": IMAGE_NEW_MSG, "params": {"panel": "guidage", "source": "focus"}}]

    async def test_includes_overlay_when_given(self):
        broadcaster = WebSocketImageBroadcaster()
        ws = FakeWebSocket()
        broadcaster.add_client(ws)
        overlay = {"zone": [313.0, 584.0, 913.0, 644.0], "line": None, "point": None}

        await broadcaster.broadcast("guidage", source="slit_xy", overlay=overlay)

        assert ws.sent == [
            {"msg": IMAGE_NEW_MSG, "params": {"panel": "guidage", "source": "slit_xy", "overlay": overlay}}
        ]

    async def test_removes_client_when_send_fails(self):
        broadcaster = WebSocketImageBroadcaster()
        ws = FakeWebSocket(fail=True)
        broadcaster.add_client(ws)

        await broadcaster.broadcast("guidage")

        assert ws not in broadcaster._clients

    async def test_remove_client_stops_further_sends(self):
        broadcaster = WebSocketImageBroadcaster()
        ws = FakeWebSocket()
        broadcaster.add_client(ws)
        broadcaster.remove_client(ws)

        await broadcaster.broadcast("guidage")

        assert ws.sent == []


class TestBroadcastFromAnotherThread:
    """The post-obs thread (main.py) runs its own event loop: a broadcast from there
    must be sent from the bound main loop, the one the WebSockets belong to."""

    async def test_call_from_another_loop_is_sent_from_the_bound_loop(self):
        broadcaster = WebSocketImageBroadcaster()
        broadcaster.bind_loop(asyncio.get_running_loop())
        ws = FakeWebSocket()
        broadcaster.add_client(ws)
        main_thread = threading.current_thread().name

        def worker():
            asyncio.run(broadcaster.broadcast("spectre", source="reduction"))

        # Run the worker without blocking this loop, which must stay free to send.
        await asyncio.to_thread(worker)

        assert ws.sent == [{"msg": IMAGE_NEW_MSG, "params": {"panel": "spectre", "source": "reduction"}}]
        assert ws.sent_from_threads == [main_thread]

    async def test_call_from_the_bound_loop_sends_directly(self):
        broadcaster = WebSocketImageBroadcaster()
        broadcaster.bind_loop(asyncio.get_running_loop())
        ws = FakeWebSocket()
        broadcaster.add_client(ws)

        await broadcaster.broadcast("guidage")

        assert ws.sent == [{"msg": IMAGE_NEW_MSG, "params": {"panel": "guidage"}}]
