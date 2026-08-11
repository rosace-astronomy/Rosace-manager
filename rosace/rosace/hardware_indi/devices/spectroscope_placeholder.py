"""
Placeholder Spectroscope device -- stands in for a real INDI spectroscope
driver while none exists as an INDI simulator (unlike the mount/camera/
focuser simulators, which are real INDI Simulator drivers). Purely an
in-memory Python object: no INDI protocol involved at all, connect()/
disconnect() are no-ops. Exposes the same small surface every real
spectroscope device class will (set_light_source/get_light_source), so
SpectroscopeAdapter (hardware_indi/adapters/spectroscope.py) works
identically regardless of which concrete device class a given hardware
profile declares.
"""

from __future__ import annotations

import logging

from ...hardware.spectroscope import LightSource


class SpectroscopePlaceholder:
    def __init__(self, config=None, logger=None, connect_on_create: bool = False) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self._light_source = LightSource.SKY
        if connect_on_create:
            self.connect()

    def connect(self) -> None:
        self.logger.debug("SpectroscopePlaceholder: connect() (no-op, no real device)")

    def disconnect(self) -> None:
        self.logger.debug("SpectroscopePlaceholder: disconnect() (no-op, no real device)")

    def set_light_source(self, source: LightSource) -> None:
        self.logger.debug(f"SpectroscopePlaceholder: light source -> {source.value}")
        self._light_source = source

    def get_light_source(self) -> LightSource:
        return self._light_source

    def __str__(self) -> str:
        return f"Spectroscope placeholder (light source: {self._light_source.value})"

    def __repr__(self) -> str:
        return self.__str__()
