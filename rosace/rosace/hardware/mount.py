"""
Generic Mount interface — what SEQUENCER's slew sub-plugin needs to drive a
telescope mount, independent of the underlying protocol (INDI, Ascom...).

Method naming follows ASCOM's own Telescope interface (ITelescopeV3/V4).
"""

from typing import Protocol


class Mount(Protocol):
    async def slew_to(self, ra: float, dec: float) -> None:
        """Slew to (and track) the given J2000 equatorial coordinates, in degrees."""
        ...
