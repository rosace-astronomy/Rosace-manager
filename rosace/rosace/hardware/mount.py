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

    async def get_position(self) -> tuple[float, float]:
        """Returns the mount's currently reported (ra, dec), J2000 degrees."""
        ...

    async def park(self) -> None:
        """Slew to the mount's home/reference position and lock it there --
        no further motion is possible until unpark() is called."""
        ...

    async def unpark(self) -> None:
        """Unlock the mount after park(), allowing motion again (position
        unchanged)."""
        ...
