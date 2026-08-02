"""
FocuserAdapter — wraps an already-connected IndiFocuser, exposing
hardware.focuser.Focuser's async contract.
"""

from __future__ import annotations

import asyncio

from ...hardware.errors import DeviceCommandError
from ..devices.focuser import IndiFocuser


class FocuserAdapter:
    def __init__(self, indi_focuser: IndiFocuser) -> None:
        self._indi_focuser = indi_focuser

    async def move_to(self, position: int) -> None:
        try:
            await asyncio.to_thread(self._indi_focuser.move_to, position)
        except Exception as e:
            raise DeviceCommandError(f"move_to({position}) failed: {e}") from e

    async def get_position(self) -> int:
        try:
            return await asyncio.to_thread(self._indi_focuser.get_position)
        except Exception as e:
            raise DeviceCommandError(f"get_position() failed: {e}") from e
