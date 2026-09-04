"""
Generic Camera interface — what a SEQUENCER sub-plugin needs to
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

    async def set_temperature(self, temperature_deg_c: float) -> None:
        """Command the sensor cooler to a target temperature. May take time to
        reach it -- this call only issues the setpoint, it does not wait."""
        ...

    async def get_temperature(self) -> float:
        """Current sensor temperature, in degrees Celsius."""
        ...

    async def get_frame_size(self) -> tuple[int, int]:
        """(width, height) in pixels of the currently configured frame (the
        driver's own ROI/CCD_FRAME) -- the NAXIS1/NAXIS2 an image acquired
        right now would have. Not necessarily the full sensor: a sub-framed
        (windowed) acquisition is a legitimate, deliberate configuration,
        not an error condition -- see acquisition sub-plugins that validate
        an existing dark/bias/calib/flat series against this value."""
        ...

    async def set_cooling_on(self) -> None:
        """Enables the sensor cooler. On many cameras this is a separate
        switch from the temperature setpoint (set_temperature) -- the
        setpoint alone has no effect until the cooler is actually turned
        on. Only meaningful for a camera whose hardware YAML entry declares
        cooling: true; callers are responsible for checking that first."""
        ...

    async def set_cooling_off(self) -> None:
        """Disables the sensor cooler."""
        ...
