"""
SpectroscopeAdapter -- wraps a spectroscope device instance for its whole
lifecycle (connect, business calls, disconnect), exposing
hardware.spectroscope.Spectroscope's async contract. Unlike the other
adapters here, the concrete device class it wraps varies by hardware
profile (SpectroscopePlaceholder today; a real Alpy/Stellarmate or
USIS-protocol device later) -- deliberately not type-hinted to one
specific device class, since this adapter itself stays generic and the
model-specific logic lives entirely in hardware_indi/devices/.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...hardware.errors import DeviceCommandError
from ...hardware.spectroscope import LightSource


class SpectroscopeAdapter:
    def __init__(self, spectroscope_device: Any) -> None:
        self._device = spectroscope_device

    async def connect(self) -> None:
        await asyncio.to_thread(self._device.connect)

    async def disconnect(self) -> None:
        await asyncio.to_thread(self._device.disconnect)

    async def set_light_source(self, source: LightSource) -> None:
        try:
            await asyncio.to_thread(self._device.set_light_source, source)
        except Exception as e:
            raise DeviceCommandError(f"set_light_source({source!r}) failed: {e}") from e

    async def get_light_source(self) -> LightSource:
        try:
            return await asyncio.to_thread(self._device.get_light_source)
        except Exception as e:
            raise DeviceCommandError(f"get_light_source() failed: {e}") from e
