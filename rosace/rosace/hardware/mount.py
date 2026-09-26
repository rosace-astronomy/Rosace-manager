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

    async def get_pier_side(self) -> str | None:
        """Returns which side of the pier the mount is on right now: "east"
        or "west", or None if the mount can't tell. Same convention as
        ASCOM's SideOfPier and INDI's TELESCOPE_PIER_SIDE: "east" means
        the mount sits east of the pier with the tube pointing west (hour
        angle > 0), "west" the reverse."""
        ...

    async def park(self) -> None:
        """Slew to the mount's home/reference position and lock it there --
        no further motion is possible until unpark() is called."""
        ...

    async def unpark(self) -> None:
        """Unlock the mount after park(), allowing motion again (position
        unchanged)."""
        ...

    async def sync_to_coordinates(self, ra: float, dec: float) -> None:
        """Tell the mount it is currently pointed at the given J2000
        equatorial coordinates (degrees), without moving it -- recalibrates
        its internal position reference to match reality. Reinstated
        2026-09-06 for field-testing on the real AM3 (see HISTORY.md) --
        removed 2026-08-26 after a distorting side effect found on the INDI
        Telescope Simulator specifically, which real-hardware field testing
        (2026-09-11/14) confirmed does not apply to the real AM3. Used by
        home_sync_am3.py's measured-sync homing, always with a plate-solved
        (not computed) position -- see that module's own docstring."""
        ...

    async def get_home_position(self, longitude_deg: float) -> tuple[float, float]:
        """Returns the (ra, dec) J2000 equatorial coordinates the mount's
        home/reference position corresponds to right now, for the given
        site longitude (degrees, east-positive). A GEM mount's home
        position (RA axis parallel to the polar axis) points at the
        celestial pole at hour angle 0 -- both of-date concepts, resolved
        to J2000 by the implementation."""
        ...
