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
