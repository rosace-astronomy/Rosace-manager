"""
Generic Focuser interface. Method naming follows ASCOM's own Focuser
interface (IFocuserV3).

Deliberately does not include an "autofocus" operation: the underlying
INDI focuser (hardware_indi/devices/focuser.py::IndiFocuser) only exposes
move_to()/get_position(), nothing autofocus-specific. Autofocus is an
algorithm built from these plus camera feedback (a measure-move-remeasure
loop) — that belongs in the focus SEQUENCER sub-plugin, not in the device
layer itself.
"""

from typing import Protocol


class Focuser(Protocol):
    async def move_to(self, position: int) -> None:
        """Move the focuser to the given absolute position."""
        ...

    async def get_position(self) -> int:
        """Current absolute focuser position."""
        ...
