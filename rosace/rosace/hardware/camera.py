"""
Generic Camera interface — what SEQUENCER's expose sub-plugin needs to
acquire an image, independent of the underlying protocol. Covers any
camera role (science, guiding...): the role is a matter of which named
device instance is configured (e.g. "science_camera", "guiding_camera" in
the hardware YAML), not a distinct interface.

Method naming follows ASCOM's own Camera interface (ICameraV2/V4).
"""

from typing import Any, Protocol


class Camera(Protocol):
    async def expose(self, duration_sec: float) -> Any:
        """Start an exposure of the given duration and return the acquired image
        once available. Image type is backend-defined for now (FITS data)."""
        ...
