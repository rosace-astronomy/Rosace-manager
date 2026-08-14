"""
Real Spectroscope device for the Alpy 600 module on the 72ED instrument,
driven through a Stellarmate SM Pro's two relay-controlled power channels
(INDI device "SM Pro", switch vector POWER_CHANNELS, elements
POWER_CHANNEL_3 / POWER_CHANNEL_4) rather than any dedicated spectroscope
protocol -- there is no INDI driver for the Alpy600 itself, only for the
Stellarmate powerbox switching its lamps/shutter on and off.

Fully confirmed on the real instrument (2026-08-14): both channels OFF is
SKY, both ON is DARK, channel 3 alone ON lights the calibration lamp
(CALIB), channel 4 alone ON lights the flat-field lamp (FLAT).
"""

from __future__ import annotations

import logging

from ...hardware.spectroscope import LightSource
from ..wrapper.indi_device import IndiDevice

_POWER_VECTOR = "POWER_CHANNELS"
_CHANNEL_3 = "POWER_CHANNEL_3"
_CHANNEL_4 = "POWER_CHANNEL_4"

# (channel_3_on, channel_4_on) for each light source -- confirmed against
# the real instrument, see the module docstring.
_STATE_BY_SOURCE = {
    LightSource.SKY: (False, False),
    LightSource.CALIB: (True, False),
    LightSource.FLAT: (False, True),
    LightSource.DARK: (True, True),
}
_SOURCE_BY_STATE = {state: source for source, state in _STATE_BY_SOURCE.items()}


class IndiSpectroAlpy600(IndiDevice):
    def __init__(self, config=None, logger=None, connect_on_create=False):
        self.logger = logger or logging.getLogger(__name__)

        if config is None:
            config = dict(
                module="spectro_Alpy600",
                indi_name="SM Pro",
                indi_client=dict(indi_host="localhost", indi_port="7624"),
            )

        device_name = config["indi_name"]
        indi_driver_name = config.get("indi_driver_name", None)

        self.logger.debug(f"Indi Spectro Alpy600, device name is: {device_name}")

        IndiDevice.__init__(
            self,
            device_name=device_name,
            indi_driver_name=indi_driver_name,
            indi_client_config=config["indi_client"],
        )
        if connect_on_create:
            self.connect()

        self.logger.debug("Indi Spectro Alpy600 configured successfully")

    def set_light_source(self, source: LightSource) -> None:
        channel_3_on, channel_4_on = _STATE_BY_SOURCE[source]
        channels = ((_CHANNEL_3, channel_3_on), (_CHANNEL_4, channel_4_on))
        on_switches = [name for name, on in channels if on]
        off_switches = [name for name, on in channels if not on]
        self.set_switch(_POWER_VECTOR, on_switches=on_switches, off_switches=off_switches)
        self.logger.debug(f"IndiSpectroAlpy600: light source -> {source.value}")

    def get_light_source(self) -> LightSource:
        states = self.get_switch(_POWER_VECTOR)
        state = (states.get(_CHANNEL_3, False), states.get(_CHANNEL_4, False))
        try:
            return _SOURCE_BY_STATE[state]
        except KeyError as e:
            raise RuntimeError(
                f"IndiSpectroAlpy600: unrecognized power-channel state {state} on {_POWER_VECTOR}"
            ) from e

    def __str__(self):
        return f"Alpy600 Spectroscope: {self.device_name}"

    def __repr__(self):
        return self.__str__()
