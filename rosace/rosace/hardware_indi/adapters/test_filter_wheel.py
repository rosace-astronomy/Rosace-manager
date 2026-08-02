from unittest.mock import MagicMock

import pytest

from rosace.hardware.errors import DeviceCommandError
from rosace.hardware_indi.adapters.filter_wheel import FilterWheelAdapter


class TestFilterWheelAdapter:
    async def test_connect_calls_indi_filter_wheel(self):
        indi_filter_wheel = MagicMock()
        adapter = FilterWheelAdapter(indi_filter_wheel)

        await adapter.connect()

        indi_filter_wheel.connect.assert_called_once()

    async def test_disconnect_calls_indi_filter_wheel(self):
        indi_filter_wheel = MagicMock()
        adapter = FilterWheelAdapter(indi_filter_wheel)

        await adapter.disconnect()

        indi_filter_wheel.disconnect.assert_called_once()

    async def test_set_filter_calls_indi_filter_wheel(self):
        indi_filter_wheel = MagicMock()
        adapter = FilterWheelAdapter(indi_filter_wheel)

        await adapter.set_filter("Ha")

        indi_filter_wheel.set_filter.assert_called_once_with("Ha")

    async def test_current_filter_returns_indi_value(self):
        indi_filter_wheel = MagicMock()
        indi_filter_wheel.currentFilter.return_value = "OIII"
        adapter = FilterWheelAdapter(indi_filter_wheel)

        assert await adapter.current_filter() == "OIII"

    async def test_set_filter_wraps_underlying_error(self):
        indi_filter_wheel = MagicMock()
        indi_filter_wheel.set_filter.side_effect = RuntimeError("wheel boom")
        adapter = FilterWheelAdapter(indi_filter_wheel)

        with pytest.raises(DeviceCommandError, match="wheel boom"):
            await adapter.set_filter("Ha")
