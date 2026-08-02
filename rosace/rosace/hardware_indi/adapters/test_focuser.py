from unittest.mock import MagicMock

import pytest

from rosace.hardware.errors import DeviceCommandError
from rosace.hardware_indi.adapters.focuser import FocuserAdapter


class TestFocuserAdapter:
    async def test_connect_calls_indi_focuser(self):
        indi_focuser = MagicMock()
        adapter = FocuserAdapter(indi_focuser)

        await adapter.connect()

        indi_focuser.connect.assert_called_once()

    async def test_disconnect_calls_indi_focuser(self):
        indi_focuser = MagicMock()
        adapter = FocuserAdapter(indi_focuser)

        await adapter.disconnect()

        indi_focuser.disconnect.assert_called_once()

    async def test_move_to_calls_indi_focuser(self):
        indi_focuser = MagicMock()
        adapter = FocuserAdapter(indi_focuser)

        await adapter.move_to(1234)

        indi_focuser.move_to.assert_called_once_with(1234)

    async def test_get_position_returns_indi_value(self):
        indi_focuser = MagicMock()
        indi_focuser.get_position.return_value = 4321
        adapter = FocuserAdapter(indi_focuser)

        assert await adapter.get_position() == 4321

    async def test_move_to_wraps_underlying_error(self):
        indi_focuser = MagicMock()
        indi_focuser.move_to.side_effect = RuntimeError("focuser boom")
        adapter = FocuserAdapter(indi_focuser)

        with pytest.raises(DeviceCommandError, match="focuser boom"):
            await adapter.move_to(100)
