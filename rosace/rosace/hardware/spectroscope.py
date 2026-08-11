"""
Generic Spectroscope interface -- what future SKY/DARK/FLAT/CALIB
acquisition SEQUENCER sub-plugins need to select a light source,
independent of the underlying protocol (INDI, Ascom...) or the specific
spectrograph model (an Alpy module driven by a Stellarmate's own two
On/Off switches, a USIS-protocol device like UVEX, ...).
"""

from enum import Enum
from typing import Protocol


class LightSource(Enum):
    """The four light sources a spectrograph can be pointed at. A fixed,
    closed set -- unlike filter names (open, config-driven), this doesn't
    vary by installation."""
    SKY = "SKY"
    DARK = "DARK"
    FLAT = "FLAT"
    CALIB = "CALIB"


class Spectroscope(Protocol):
    async def set_light_source(self, source: LightSource) -> None:
        """Selects which light source reaches the spectrograph."""
        ...

    async def get_light_source(self) -> LightSource:
        """Returns the light source currently selected."""
        ...
