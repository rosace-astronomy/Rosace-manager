"""
Tests de la connexion matérielle pour INIT_SESSION (sequencer_backend=indi).
Lancement : pytest plugins/test_hardware_connectivity.py -v
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from rosace.plugins import hardware_connectivity as hc

_CONF_DIR = Path(__file__).parent.parent / "conf"
_HARDWARE_YAML_FILES = sorted(_CONF_DIR.glob("hardware_indi_*.yaml"))


@pytest.fixture
def short_timeout(monkeypatch):
    """Un timeout court pour ne pas ralentir la suite de tests."""
    monkeypatch.setattr(hc, "HARDWARE_CONNECT_TIMEOUT_SECONDS", 0.2)


MOUNT_DRIVER = {
    "role": "mount",
    "module": "mount",
    "module_dir": "rosace.hardware_indi.devices",
    "class_name": "IndiMount",
    "indi_name": "Test Mount",
    "indi_client": {"indi_host": "localhost", "indi_port": 7624},
}

FOCUSER_DRIVER = {
    "role": "focuser",
    "module": "focuser",
    "module_dir": "rosace.hardware_indi.devices",
    "class_name": "IndiFocuser",
    "indi_name": "Test Focuser",
    "port": "/dev/ttyUSB0",
    "indi_client": {"indi_host": "localhost", "indi_port": 7624},
}

UNREGISTERED_ROLE_DRIVER = {
    "role": "not_yet_supported",  # deliberately not in _ADAPTERS_BY_ROLE
    "module": "does_not_matter",
    "module_dir": "rosace.hardware_indi.devices",
    "class_name": "DoesNotMatter",
    "indi_name": "Unregistered",
    "indi_client": {"indi_host": "localhost", "indi_port": 7624},
}

SPECTROSCOPE_DRIVER = {
    "role": "spectroscope",
    "module": "spectroscope_placeholder",
    "module_dir": "rosace.hardware_indi.devices",
    "class_name": "SpectroscopePlaceholder",
    "indi_name": "Spectroscope Placeholder",
    "indi_client": {"indi_host": "localhost", "indi_port": 7624},
}

INCOMPLETE_DRIVER = {
    "role": "mount",
    "module": "mount",
    "module_dir": "rosace.hardware_indi.devices",
    "class_name": "IndiMount",
    # indi_client and indi_name deliberately missing
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


class TestExtractDeviceParams:

    def test_strips_plumbing_keys_only(self):
        result = hc._extract_device_params("focuser", FOCUSER_DRIVER)

        assert result == {"port": "/dev/ttyUSB0"}

    def test_device_with_only_plumbing_fields_returns_empty_dict(self):
        result = hc._extract_device_params("mount", MOUNT_DRIVER)

        assert result == {}

    def test_business_fields_pass_through_unfiltered(self):
        device_cfg = {**MOUNT_DRIVER, "some_future_param": 42, "another_one": "x"}

        result = hc._extract_device_params("mount", device_cfg)

        assert result == {"some_future_param": 42, "another_one": "x"}

    def test_raises_naming_every_missing_plumbing_key(self):
        with pytest.raises(ValueError, match="indi_client.*indi_name|indi_name.*indi_client"):
            hc._extract_device_params("mount", INCOMPLETE_DRIVER)


class TestConnectSessionHardware:

    async def test_all_succeed_returns_adapters_and_params_keyed_by_device_name(self, monkeypatch, short_timeout):
        monkeypatch.setattr(hc, "_load_hardware_config", lambda: {
            "INDI_drivers": {"mount": MOUNT_DRIVER, "focuser": FOCUSER_DRIVER},
            "PHD2_config": {"host": "127.0.0.1", "port": 4400, "settle": {"pixels": 1.5, "time": 3, "timeout": 30}},
        })
        monkeypatch.setattr(hc, "_connect_device_sync", MagicMock(return_value=MagicMock()))
        fake_phd2_client = MagicMock()
        fake_phd2_client.connect = AsyncMock(return_value=None)
        monkeypatch.setattr(hc, "Phd2Client", MagicMock(return_value=fake_phd2_client))

        result = await hc.connect_session_hardware()

        assert set(result.adapters) == {"mount", "focuser", "phd2"}
        assert isinstance(result.adapters["mount"], hc.MountAdapter)
        assert isinstance(result.adapters["focuser"], hc.FocuserAdapter)
        assert result.adapters["phd2"] is fake_phd2_client
        # PHD2 isn't an INDI_drivers entry, its own config shape doesn't match --
        # no hardware_config entry for it. mount has no business fields at all;
        # focuser's "port" is the one non-plumbing field it declares.
        assert set(result.hardware_config) == {"mount", "focuser"}
        assert result.hardware_config["mount"] == {}
        assert result.hardware_config["focuser"] == {"port": "/dev/ttyUSB0"}

    async def test_unknown_role_is_connected_but_not_returned(self, monkeypatch, short_timeout):
        monkeypatch.setattr(hc, "_load_hardware_config", lambda: {
            "INDI_drivers": {"some_device": UNREGISTERED_ROLE_DRIVER},
        })
        monkeypatch.setattr(hc, "_connect_device_sync", MagicMock(return_value=MagicMock()))

        result = await hc.connect_session_hardware()  # ne doit pas lever

        assert result.adapters == {}
        assert result.hardware_config == {}

    async def test_spectroscope_role_returns_spectroscope_adapter(self, monkeypatch, short_timeout):
        monkeypatch.setattr(hc, "_load_hardware_config", lambda: {
            "INDI_drivers": {"spectroscope": SPECTROSCOPE_DRIVER},
        })
        monkeypatch.setattr(hc, "_connect_device_sync", MagicMock(return_value=MagicMock()))

        result = await hc.connect_session_hardware()

        assert isinstance(result.adapters["spectroscope"], hc.SpectroscopeAdapter)
        assert result.hardware_config["spectroscope"] == {}

    async def test_missing_plumbing_field_is_collected_and_raised_without_connecting(self, monkeypatch, short_timeout):
        monkeypatch.setattr(hc, "_load_hardware_config", lambda: {
            "INDI_drivers": {"mount": INCOMPLETE_DRIVER},
        })
        connect_spy = MagicMock(side_effect=AssertionError("should never be called"))
        monkeypatch.setattr(hc, "_connect_device_sync", connect_spy)

        with pytest.raises(RuntimeError, match="mount"):
            await hc.connect_session_hardware()

        connect_spy.assert_not_called()

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
            "PHD2_config": {"host": "127.0.0.1", "port": 4400, "settle": {"pixels": 1.5, "time": 3, "timeout": 30}},
        })
        fake_phd2_client = MagicMock()
        fake_phd2_client.connect = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        monkeypatch.setattr(hc, "Phd2Client", MagicMock(return_value=fake_phd2_client))

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


class TestRealHardwareYamlFiles:
    """Static, connection-free smoke test over every conf/hardware_indi_*.yaml
    actually shipped in the repo -- catches a malformed file or a device
    entry missing a plumbing field (_PLUMBING_KEYS) at test time, the same
    class of mistake --check-config already catches for config.yaml, instead
    of only surfacing at INIT_SESSION against real hardware."""

    @pytest.mark.parametrize("path", _HARDWARE_YAML_FILES, ids=lambda p: p.name)
    def test_parses_and_declares_every_plumbing_key(self, path):
        with open(path, encoding="utf-8") as f:
            hw_config = yaml.safe_load(f)

        devices = hw_config.get("INDI_drivers", {})
        assert devices, f"{path.name}: no INDI_drivers declared"

        for device_name, device_cfg in devices.items():
            hc._extract_device_params(device_name, device_cfg)

    @pytest.mark.parametrize("path", _HARDWARE_YAML_FILES, ids=lambda p: p.name)
    def test_phd2_config_declares_required_keys(self, path):
        with open(path, encoding="utf-8") as f:
            hw_config = yaml.safe_load(f)

        phd2_cfg = hw_config.get("PHD2_config")
        assert phd2_cfg, f"{path.name}: no PHD2_config declared"

        missing = {"host", "port", "settle"} - phd2_cfg.keys()
        assert not missing, f"{path.name}: PHD2_config missing {sorted(missing)}"

    def test_at_least_one_hardware_profile_found(self):
        """Guards against the glob silently matching nothing (e.g. a future
        conf/ layout change) and every test above trivially passing on an
        empty parametrize list."""
        assert _HARDWARE_YAML_FILES
