"""
Tests de la connexion matérielle pour INIT_SESSION (sequencer_backend=indi).
Lancement : pytest plugins/test_hardware_connectivity.py -v
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from rosace.plugins import hardware_connectivity as hc


@pytest.fixture
def short_timeout(monkeypatch):
    """Un timeout court pour ne pas ralentir la suite de tests."""
    monkeypatch.setattr(hc, "HARDWARE_CONNECT_TIMEOUT_SECONDS", 0.2)


MOUNT_DRIVER = {
    "role": "mount",
    "module": "mount",
    "module_dir": "rosace.hardware_indi.devices",
    "class_name": "IndiMount",
    "mount_name": "Test Mount",
    "indi_client": {"indi_host": "localhost", "indi_port": 7624},
}

FOCUSER_DRIVER = {
    "role": "focuser",
    "module": "focuser",
    "module_dir": "rosace.hardware_indi.devices",
    "class_name": "IndiFocuser",
    "focuser_name": "Test Focuser",
    "port": "/dev/ttyUSB0",
    "indi_client": {"indi_host": "localhost", "indi_port": 7624},
}

SPECTROSCOPE_DRIVER = {
    "role": "spectroscope",  # no adapter registered for this role yet
    "module": "usis_spectroscope",
    "module_dir": "rosace.hardware_indi.devices",
    "class_name": "IndiUsisSpectro",
    "indi_client": {"indi_host": "localhost", "indi_port": 7624},
}


class TestLoadHardwareConfig:

    def test_reads_yaml_into_dict(self, tmp_path, monkeypatch):
        hw_file = tmp_path / "hardware_test.yaml"
        hw_file.write_text(yaml.dump({"INDI_drivers": {"mount": MOUNT_DRIVER}}))
        monkeypatch.setattr(hc, "HARDWARE_CONFIG_PATH", hw_file)

        result = hc._load_hardware_config()

        assert result == {"INDI_drivers": {"mount": MOUNT_DRIVER}}


class TestLoadDeviceClass:

    def test_loads_a_real_device_class(self):
        from rosace.hardware_indi.devices.mount import IndiMount

        cls = hc._load_device_class("rosace.hardware_indi.devices", "mount", "IndiMount")

        assert cls is IndiMount

    def test_raises_on_unknown_module(self):
        with pytest.raises(ImportError):
            hc._load_device_class("rosace.hardware_indi.devices", "does_not_exist", "Whatever")

    def test_raises_on_unknown_class(self):
        with pytest.raises(AttributeError):
            hc._load_device_class("rosace.hardware_indi.devices", "mount", "DoesNotExist")


class TestConnectSessionHardware:

    async def test_all_succeed_returns_adapters_keyed_by_device_name(self, monkeypatch, short_timeout):
        monkeypatch.setattr(hc, "_load_hardware_config", lambda: {
            "INDI_drivers": {"mount": MOUNT_DRIVER, "focuser": FOCUSER_DRIVER},
            "PHD2_config": {"host": "127.0.0.1", "port": 4400},
        })
        monkeypatch.setattr(hc, "_connect_device_sync", MagicMock(return_value=MagicMock()))

        fake_writer = MagicMock()
        fake_writer.wait_closed = AsyncMock(return_value=None)

        async def fake_open_connection(host, port):
            return MagicMock(), fake_writer

        monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)

        adapters = await hc.connect_session_hardware()

        assert set(adapters) == {"mount", "focuser"}
        assert isinstance(adapters["mount"], hc.MountAdapter)
        assert isinstance(adapters["focuser"], hc.FocuserAdapter)

    async def test_unknown_role_is_connected_but_not_returned(self, monkeypatch, short_timeout):
        monkeypatch.setattr(hc, "_load_hardware_config", lambda: {
            "INDI_drivers": {"spectroscope": SPECTROSCOPE_DRIVER},
        })
        monkeypatch.setattr(hc, "_connect_device_sync", MagicMock(return_value=MagicMock()))

        adapters = await hc.connect_session_hardware()  # ne doit pas lever

        assert adapters == {}

    async def test_device_failure_is_collected_and_raised(self, monkeypatch, short_timeout):
        monkeypatch.setattr(hc, "_load_hardware_config", lambda: {
            "INDI_drivers": {"mount": MOUNT_DRIVER},
        })

        def failing_connect(device_cfg, timeout):
            raise RuntimeError("no indiserver")

        monkeypatch.setattr(hc, "_connect_device_sync", failing_connect)

        with pytest.raises(RuntimeError, match="mount"):
            await hc.connect_session_hardware()

    async def test_phd2_failure_is_collected_and_raised(self, monkeypatch, short_timeout):
        monkeypatch.setattr(hc, "_load_hardware_config", lambda: {
            "INDI_drivers": {},
            "PHD2_config": {"host": "127.0.0.1", "port": 4400},
        })

        async def failing_open_connection(host, port):
            raise ConnectionRefusedError("refused")

        monkeypatch.setattr(asyncio, "open_connection", failing_open_connection)

        with pytest.raises(RuntimeError, match="PHD2"):
            await hc.connect_session_hardware()

    async def test_device_timeout_is_reported(self, monkeypatch, short_timeout):
        monkeypatch.setattr(hc, "_load_hardware_config", lambda: {
            "INDI_drivers": {"mount": MOUNT_DRIVER},
        })

        def slow_connect(device_cfg, timeout):
            time.sleep(1)  # bien plus long que short_timeout (0.2s)

        monkeypatch.setattr(hc, "_connect_device_sync", slow_connect)

        with pytest.raises(RuntimeError, match="mount"):
            await hc.connect_session_hardware()

    async def test_devices_are_connected_one_at_a_time(self, monkeypatch, short_timeout):
        """Plusieurs devices peuvent partager le même IndiClient singleton (même
        indi_host/indi_port), dont l'initialisation n'est pas thread-safe : on
        vérifie qu'aucune connexion de device ne chevauche une autre."""
        monkeypatch.setattr(hc, "_load_hardware_config", lambda: {
            "INDI_drivers": {"mount": MOUNT_DRIVER, "focuser": FOCUSER_DRIVER},
        })

        concurrent_count = 0
        max_concurrent = 0

        def tracking_connect(device_cfg, timeout):
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            time.sleep(0.05)
            concurrent_count -= 1
            return MagicMock()

        monkeypatch.setattr(hc, "_connect_device_sync", tracking_connect)

        await hc.connect_session_hardware()

        assert max_concurrent == 1
