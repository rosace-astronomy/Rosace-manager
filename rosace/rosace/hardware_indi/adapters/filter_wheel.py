"""
FilterWheelAdapter — wraps an already-connected IndiFilterWheel, exposing
hardware.filter_wheel.FilterWheel's async contract.
"""

from __future__ import annotations

import asyncio

from ...hardware.errors import DeviceCommandError
from ..devices.filter_wheel import IndiFilterWheel


class FilterWheelAdapter:
    def __init__(self, indi_filter_wheel: IndiFilterWheel) -> None:
        self._indi_filter_wheel = indi_filter_wheel

    async def set_filter(self, name: str) -> None:
        try:
            await asyncio.to_thread(self._indi_filter_wheel.set_filter, name)
        except Exception as e:
            raise DeviceCommandError(f"set_filter({name!r}) failed: {e}") from e

    async def current_filter(self) -> str:
        try:
            return await asyncio.to_thread(self._indi_filter_wheel.currentFilter)
        except Exception as e:
            raise DeviceCommandError(f"current_filter() failed: {e}") from e
