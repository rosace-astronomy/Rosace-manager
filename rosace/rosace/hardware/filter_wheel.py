"""
Generic FilterWheel interface. Method naming follows ASCOM's own
FilterWheel interface (IFilterWheelV2).
"""

from typing import Protocol


class FilterWheel(Protocol):
    async def set_filter(self, name: str) -> None:
        """Move the wheel to the filter with this name."""
        ...

    async def current_filter(self) -> str:
        """Name of the filter currently in place."""
        ...
