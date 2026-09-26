"""
MountAdapter — wraps an IndiMount instance for its whole lifecycle (connect,
business calls, disconnect), exposing hardware.mount.Mount's async contract
by running IndiMount's blocking INDI calls in a worker thread.
"""

from __future__ import annotations

import asyncio

from astropy import units as u
from astropy.coordinates import SkyCoord

from ...hardware.errors import DeviceCommandError
from ..devices.mount import IndiMount


class MountAdapter:
    def __init__(self, indi_mount: IndiMount) -> None:
        self._indi_mount = indi_mount

    async def connect(self) -> None:
        await asyncio.to_thread(self._indi_mount.connect)

    async def disconnect(self) -> None:
        await asyncio.to_thread(self._indi_mount.disconnect)

    async def slew_to(self, ra: float, dec: float) -> None:
        coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
        try:
            await asyncio.to_thread(self._indi_mount.slew_to_coord_and_track, coord)
        except Exception as e:
            raise DeviceCommandError(f"slew_to({ra}, {dec}) failed: {e}") from e

    async def get_position(self) -> tuple[float, float]:
        try:
            coord = await asyncio.to_thread(self._indi_mount.get_current_coordinates)
        except Exception as e:
            raise DeviceCommandError(f"get_position() failed: {e}") from e
        return float(coord.ra.deg), float(coord.dec.deg)

    async def get_pier_side(self) -> str | None:
        try:
            switches = await asyncio.to_thread(self._indi_mount.get_pier_side)
        except Exception as e:
            raise DeviceCommandError(f"get_pier_side() failed: {e}") from e
        if switches.get("PIER_EAST"):
            return "east"
        if switches.get("PIER_WEST"):
            return "west"
        return None

    async def park(self) -> None:
        try:
            await asyncio.to_thread(self._indi_mount.park)
        except Exception as e:
            raise DeviceCommandError(f"park() failed: {e}") from e

    async def unpark(self) -> None:
        try:
            await asyncio.to_thread(self._indi_mount.unpark)
        except Exception as e:
            raise DeviceCommandError(f"unpark() failed: {e}") from e

    async def sync_to_coordinates(self, ra: float, dec: float) -> None:
        coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
        try:
            await asyncio.to_thread(self._indi_mount.sync_to_coord, coord)
        except Exception as e:
            raise DeviceCommandError(f"sync_to_coordinates({ra}, {dec}) failed: {e}") from e

    async def get_home_position(self, longitude_deg: float) -> tuple[float, float]:
        try:
            coord = await asyncio.to_thread(self._indi_mount.get_home_position_icrs, longitude_deg)
        except Exception as e:
            raise DeviceCommandError(f"get_home_position({longitude_deg}) failed: {e}") from e
        return float(coord.ra.deg), float(coord.dec.deg)
