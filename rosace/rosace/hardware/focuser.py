"""
Generic Focuser interface. Method naming follows ASCOM's own Focuser
interface (IFocuserV3).

Deliberately does not include an "autofocus" operation: the underlying
INDI focuser (hardware_indi/devices/focuser.py::IndiFocuser) only exposes
move_to()/get_position(), nothing autofocus-specific. Autofocus is an
algorithm built from these plus camera feedback (a measure-move-remeasure
loop) — that would belong in its own SEQUENCER sub-plugin, not in the
device layer itself. (The `focus` sub-plugin itself is unrelated to this
focuser: it measures the guiding camera's own focus sharpness, not this
telescope's science-side focuser -- see CLAUDE.md.)
"""

from typing import Protocol


class Focuser(Protocol):
    async def move_to(self, position: int) -> None:
        """Move the focuser to the given absolute position."""
        ...

    async def get_position(self) -> int:
        """Current absolute focuser position."""
        ...

    async def get_temperature(self) -> float:
        """Ambient temperature reported by the focuser's own probe, in degrees
        Celsius -- used as the observatory's ambient temperature reference for
        the science camera's cooldown target (see plugins/camera_cooldown.py).
        Not every focuser has a probe; a real INDI focuser without one will
        raise from the device layer rather than silently returning a bogus
        value."""
        ...
