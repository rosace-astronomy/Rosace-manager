"""
CameraAdapter — wraps an IndiCamera instance for its whole lifecycle (connect,
business calls, disconnect), exposing hardware.camera.Camera's async contract.
expose() runs IndiCamera's whole blocking acquisition sequence (arm blob
reception, set exposure time, trigger, wait for the image, read it back) as
a single unit in a worker thread, since these calls must happen in strict
sequence on the same device.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...hardware.errors import DeviceCommandError
from ..devices.camera import IndiCamera


class CameraAdapter:
    def __init__(self, indi_camera: IndiCamera) -> None:
        self._indi_camera = indi_camera

    async def connect(self) -> None:
        await asyncio.to_thread(self._indi_camera.connect)

    async def disconnect(self) -> None:
        await asyncio.to_thread(self._indi_camera.disconnect)

    async def expose(self, duration_sec: float) -> Any:
        def _blocking_expose() -> Any:
            self._indi_camera.prepare_shoot()
            self._indi_camera.setExpTimeSec(duration_sec)
            self._indi_camera.shoot_async()
            self._indi_camera.synchronize_with_image_reception(duration_sec)
            return self._indi_camera.get_received_image()

        try:
            return await asyncio.to_thread(_blocking_expose)
        except Exception as e:
            raise DeviceCommandError(f"expose({duration_sec}) failed: {e}") from e

    async def set_temperature(self, temperature_deg_c: float) -> None:
        try:
            await asyncio.to_thread(self._indi_camera.set_temperature, temperature_deg_c)
        except Exception as e:
            raise DeviceCommandError(f"set_temperature({temperature_deg_c}) failed: {e}") from e

    async def get_temperature(self) -> float:
        try:
            return await asyncio.to_thread(self._indi_camera.get_temperature)
        except Exception as e:
            raise DeviceCommandError(f"get_temperature() failed: {e}") from e

    async def get_frame_size(self) -> tuple[int, int]:
        try:
            roi = await asyncio.to_thread(self._indi_camera.get_roi)
            return int(roi["WIDTH"]), int(roi["HEIGHT"])
        except Exception as e:
            raise DeviceCommandError(f"get_frame_size() failed: {e}") from e

    async def set_cooling_on(self) -> None:
        try:
            await asyncio.to_thread(self._indi_camera.set_cooling_on)
        except Exception as e:
            raise DeviceCommandError(f"set_cooling_on() failed: {e}") from e

    async def set_cooling_off(self) -> None:
        try:
            await asyncio.to_thread(self._indi_camera.set_cooling_off)
        except Exception as e:
            raise DeviceCommandError(f"set_cooling_off() failed: {e}") from e
