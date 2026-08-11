from unittest.mock import MagicMock

import pytest

from rosace.hardware.errors import DeviceCommandError
from rosace.hardware.spectroscope import LightSource
from rosace.hardware_indi.adapters.spectroscope import SpectroscopeAdapter


class TestSpectroscopeAdapter:

    async def test_connect_calls_device(self):
        device = MagicMock()
        adapter = SpectroscopeAdapter(device)

        await adapter.connect()

        device.connect.assert_called_once()

    async def test_disconnect_calls_device(self):
        device = MagicMock()
        adapter = SpectroscopeAdapter(device)

        await adapter.disconnect()

        device.disconnect.assert_called_once()

    async def test_set_light_source_calls_device(self):
        device = MagicMock()
        adapter = SpectroscopeAdapter(device)

        await adapter.set_light_source(LightSource.FLAT)

        device.set_light_source.assert_called_once_with(LightSource.FLAT)

    async def test_get_light_source_returns_device_value(self):
        device = MagicMock()
        device.get_light_source.return_value = LightSource.DARK
        adapter = SpectroscopeAdapter(device)

        assert await adapter.get_light_source() == LightSource.DARK

    async def test_set_light_source_wraps_underlying_error(self):
        device = MagicMock()
        device.set_light_source.side_effect = RuntimeError("spectro boom")
        adapter = SpectroscopeAdapter(device)

        with pytest.raises(DeviceCommandError, match="spectro boom"):
            await adapter.set_light_source(LightSource.CALIB)

    async def test_get_light_source_wraps_underlying_error(self):
        device = MagicMock()
        device.get_light_source.side_effect = RuntimeError("spectro boom")
        adapter = SpectroscopeAdapter(device)

        with pytest.raises(DeviceCommandError, match="spectro boom"):
            await adapter.get_light_source()
