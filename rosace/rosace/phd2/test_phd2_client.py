"""
Tests de Phd2Client contre un faux serveur PHD2 (asyncio.start_server local,
port éphémère) qui répond à des requêtes JSON-RPC scriptées et peut pousser
des events à la demande — plus fidèle qu'un mock profond de StreamReader/
StreamWriter, et events/réponses s'entrelacent réellement sur le fil, donc un
faux serveur scripté exerce vraiment ce comportement.
Lancement : pytest rosace/phd2/test_phd2_client.py -v
"""

import asyncio
import json

import pytest

from rosace.hardware.errors import DeviceCommandError, DeviceConnectionError, DeviceTimeoutError
from rosace.phd2 import phd2_client as phd2_client_module
from rosace.phd2.phd2_client import Phd2Client

SETTLE = {"pixels": 1.5, "time": 3, "timeout": 2}  # timeout court pour ne pas ralentir les tests


class FakePhd2Server:
    """Sert une unique connexion. self.handlers: method -> valeur de résultat,
    "NO_RESPONSE" (ne répond jamais, pour tester les timeouts), ou une
    coroutine `async def handler(msg) -> result`. Un résultat de la forme
    ("error", message) est envoyé comme réponse JSON-RPC d'erreur. Toute
    méthode non déclarée répond 0 par défaut (comme la plupart des méthodes
    PHD2 réelles : set_connected, stop_capture, guide...)."""

    def __init__(self):
        self.received: list[dict] = []
        self.handlers: dict = {}
        self._server: asyncio.base_events.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = asyncio.Event()

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle_conn, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._writer is not None:
            self._writer.close()
        self._server.close()
        await self._server.wait_closed()

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self._connected.set()
        while True:
            line = await reader.readline()
            if not line:
                return
            msg = json.loads(line)
            self.received.append(msg)
            handler = self.handlers.get(msg["method"], 0)
            if handler == "NO_RESPONSE":
                continue
            result = await handler(msg) if callable(handler) else handler
            if isinstance(result, tuple) and len(result) == 2 and result[0] == "error":
                payload = {"jsonrpc": "2.0", "error": {"code": 1, "message": result[1]}, "id": msg["id"]}
            else:
                payload = {"jsonrpc": "2.0", "result": result, "id": msg["id"]}
            await self._send(payload)

    async def _send(self, payload: dict) -> None:
        self._writer.write((json.dumps(payload) + "\r\n").encode())
        await self._writer.drain()

    async def push_event(self, event: dict) -> None:
        await self._connected.wait()
        await self._send(event)

    async def close_connection(self) -> None:
        """Simule une perte de connexion côté serveur."""
        await self._connected.wait()
        self._writer.close()
        await self._writer.wait_closed()


@pytest.fixture
async def server():
    srv = FakePhd2Server()
    port = await srv.start()
    srv.port = port
    yield srv
    await srv.stop()


@pytest.fixture
async def client(server):
    c = Phd2Client("127.0.0.1", server.port, settle=SETTLE)
    yield c
    if c._connected:
        await c.disconnect()


class TestCallRoundTrip:

    async def test_request_response_round_trip(self, server, client):
        server.handlers["get_app_state"] = "Guiding"
        await client.connect()

        state = await client.get_app_state()

        assert state == "Guiding"

    async def test_error_response_raises_device_command_error(self, server, client):
        server.handlers["get_app_state"] = ("error", "not connected to equipment")
        await client.connect()

        with pytest.raises(DeviceCommandError, match="not connected to equipment"):
            await client.get_app_state()

    async def test_no_response_within_timeout_raises_device_timeout_error(self, server, client):
        await client.connect()
        server.handlers["get_app_state"] = "NO_RESPONSE"

        with pytest.raises(DeviceTimeoutError):
            await client._call("get_app_state", timeout=0.2)


class TestConnect:

    async def test_connect_without_profile_only_engages_equipment(self, server, client):
        await client.connect()

        methods = [m["method"] for m in server.received]
        assert methods == ["set_connected"]
        assert server.received[0]["params"] == [True]

    async def test_connect_resolves_profile_id_and_engages_equipment(self, server, client):
        server.handlers["get_profiles"] = [
            {"id": 1, "name": "Simulators"}, {"id": 2, "name": "Nomade 72ED Alpy"},
        ]
        await client.connect(profile_name="Nomade 72ED Alpy")

        methods = [m["method"] for m in server.received]
        assert methods == ["get_profiles", "set_profile", "set_connected"]
        assert server.received[1]["params"] == [2]
        assert server.received[2]["params"] == [True]

    async def test_connect_raises_on_unknown_profile_name(self, server, client):
        server.handlers["get_profiles"] = [{"id": 1, "name": "Simulators"}]

        with pytest.raises(DeviceCommandError, match="Nomade 72ED Alpy"):
            await client.connect(profile_name="Nomade 72ED Alpy")


class TestStartGuiding:

    async def test_returns_once_settle_done_with_status_zero(self, server, client):
        async def on_guide(msg):
            await server.push_event({"Event": "SettleDone", "Status": 0})
            return 0

        server.handlers["guide"] = on_guide
        await client.connect()

        await client.start_guiding()  # ne doit pas lever

        assert server.received[-1]["method"] == "guide"
        assert server.received[-1]["params"]["settle"] == SETTLE

    async def test_raises_when_settle_done_reports_failure(self, server, client):
        async def on_guide(msg):
            await server.push_event({"Event": "SettleDone", "Status": 1, "Error": "star lost"})
            return 0

        server.handlers["guide"] = on_guide
        await client.connect()

        with pytest.raises(DeviceCommandError, match="star lost"):
            await client.start_guiding()

    async def test_fails_fast_on_calibration_failed_instead_of_waiting_out_settle_timeout(self, server, client):
        async def on_guide(msg):
            await server.push_event({"Event": "CalibrationFailed", "Reason": "star moved too much"})
            return 0

        server.handlers["guide"] = on_guide
        await client.connect()

        with pytest.raises(DeviceCommandError, match="star moved too much"):
            await client.start_guiding()

    async def test_recalibrate_param_defaults_from_do_calibration(self, server):
        async def on_guide(msg):
            await server.push_event({"Event": "SettleDone", "Status": 0})
            return 0

        server.handlers["guide"] = on_guide
        client = Phd2Client("127.0.0.1", server.port, settle=SETTLE, do_calibration=True)
        await client.connect()

        await client.start_guiding()

        assert server.received[-1]["params"]["recalibrate"] is True
        await client.disconnect()

    async def test_raises_on_timeout_when_no_settle_event_arrives(self, server, client, monkeypatch):
        # start_guiding()'s real wait is settle["timeout"] + _DEFAULT_CALL_TIMEOUT --
        # SETTLE["timeout"] is already short (2s) but _DEFAULT_CALL_TIMEOUT (10s,
        # a real safety margin in production) isn't, so this test used to take a
        # genuine 12s. Shrunk to a near-instant timeout here only.
        monkeypatch.setattr(phd2_client_module, "_DEFAULT_CALL_TIMEOUT", 0.05)
        server.handlers["guide"] = 0  # ack, but never sends SettleDone/CalibrationFailed
        await client.connect()

        with pytest.raises(DeviceTimeoutError):
            await client.start_guiding()


class TestStopCapture:

    async def test_returns_once_app_state_reaches_stopped(self, server, client):
        async def on_stop(msg):
            await server.push_event({"Event": "AppState", "State": "Stopped"})
            return 0

        server.handlers["stop_capture"] = on_stop
        await client.connect()

        await client.stop_capture()  # ne doit pas lever

    async def test_ignores_intermediate_app_states_before_stopped(self, server, client):
        async def on_stop(msg):
            await server.push_event({"Event": "AppState", "State": "GuidingStopped"})
            await server.push_event({"Event": "AppState", "State": "Stopped"})
            return 0

        server.handlers["stop_capture"] = on_stop
        await client.connect()

        await client.stop_capture()  # ne doit pas lever, malgré l'état intermédiaire

    async def test_raises_on_timeout_when_stopped_never_reported(self, server, client):
        server.handlers["stop_capture"] = 0  # ack, mais aucun AppState="Stopped" n'arrive
        await client.connect()

        with pytest.raises(DeviceTimeoutError):
            await client.stop_capture(timeout=0.2)


class TestLockPositionAndStar:

    async def test_set_lock_position_sends_x_y_exact(self, server, client):
        await client.connect()

        await client.set_lock_position(123.4, 56.7, exact=False)

        assert server.received[-1]["method"] == "set_lock_position"
        assert server.received[-1]["params"] == [123.4, 56.7, False]

    async def test_get_lock_position_returns_tuple(self, server, client):
        server.handlers["get_lock_position"] = [123.4, 56.7]
        await client.connect()

        result = await client.get_lock_position()

        assert result == (123.4, 56.7)

    async def test_get_lock_position_returns_none_when_unset(self, server, client):
        server.handlers["get_lock_position"] = None
        await client.connect()

        assert await client.get_lock_position() is None

    async def test_find_star_returns_lock_position(self, server, client):
        server.handlers["find_star"] = [200.0, 150.0]
        await client.connect()

        result = await client.find_star()

        assert result == (200.0, 150.0)
        assert server.received[-1].get("params") is None

    async def test_find_star_passes_roi(self, server, client):
        server.handlers["find_star"] = [200.0, 150.0]
        await client.connect()

        await client.find_star(roi=[0, 0, 400, 400])

        assert server.received[-1]["params"] == [[0, 0, 400, 400]]


class TestCalibration:

    async def test_get_calibrated(self, server, client):
        server.handlers["get_calibrated"] = True
        await client.connect()

        assert await client.get_calibrated() is True

    async def test_get_calibration_data_passes_which_and_returns_raw_dict(self, server, client):
        raw = {"calibrated": True, "xAngle": -167.1, "xRate": 39.124, "xParity": "-"}
        server.handlers["get_calibration_data"] = raw
        await client.connect()

        result = await client.get_calibration_data(which="AO")

        assert result == raw
        assert server.received[-1]["params"] == ["AO"]

    async def test_clear_calibration_omitted_which_sends_no_params(self, server, client):
        await client.connect()

        await client.clear_calibration()

        assert server.received[-1].get("params") is None

    async def test_clear_calibration_passes_which(self, server, client):
        await client.connect()

        await client.clear_calibration(which="mount")

        assert server.received[-1]["params"] == ["mount"]


class TestPauseAndExposure:

    async def test_get_paused(self, server, client):
        server.handlers["get_paused"] = True
        await client.connect()

        assert await client.get_paused() is True

    async def test_set_paused_without_full(self, server, client):
        await client.connect()

        await client.set_paused(True)

        assert server.received[-1]["params"] == [True]

    async def test_set_paused_with_full(self, server, client):
        await client.connect()

        await client.set_paused(True, full=True)

        assert server.received[-1]["params"] == [True, "full"]

    async def test_get_set_exposure(self, server, client):
        server.handlers["get_exposure"] = 300
        await client.connect()

        assert await client.get_exposure() == 300

        await client.set_exposure(500)
        assert server.received[-1]["params"] == [500]

    async def test_get_star_image_passes_through_raw_result(self, server, client):
        raw = {"frame": 1, "width": 15, "height": 15, "star_pos": [7, 7], "pixels": "base64=="}
        server.handlers["get_star_image"] = raw
        await client.connect()

        result = await client.get_star_image(size=32)

        assert result == raw
        assert server.received[-1]["params"] == [32]


class TestDisconnectAndConnectionLoss:

    async def test_disconnect_stops_reader_task_cleanly(self, server, client):
        await client.connect()

        await client.disconnect()

        assert client._read_task.done()
        assert client._connected is False

    async def test_server_side_close_fails_pending_call_with_connection_error(self, server, client):
        server.handlers["get_app_state"] = "NO_RESPONSE"
        await client.connect()

        call_task = asyncio.ensure_future(client.get_app_state())
        await asyncio.sleep(0.05)  # laisse la requête partir et arriver au faux serveur
        await server.close_connection()

        with pytest.raises(DeviceConnectionError):
            await call_task

    async def test_next_call_after_connection_loss_raises_immediately(self, server, client):
        await client.connect()

        await server.close_connection()
        await asyncio.sleep(0.05)  # laisse _read_loop détecter la fermeture

        with pytest.raises(DeviceConnectionError):
            await client.get_app_state()
