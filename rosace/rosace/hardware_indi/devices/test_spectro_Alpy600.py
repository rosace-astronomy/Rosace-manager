"""
Tests for IndiSpectroAlpy600. Launch:
pytest hardware_indi/devices/test_spectro_Alpy600.py -v

Only the pure light-source <-> power-channel mapping is exercised here,
via a device instance whose set_switch()/get_switch() (inherited from
IndiDevice) are mocked -- no real INDI connection involved, matching the
"cover whatever is testable in isolation" rule for hardware wrapper code.
"""

from unittest.mock import MagicMock

import pytest

from rosace.hardware.spectroscope import LightSource
from rosace.hardware_indi.devices.spectro_Alpy600 import IndiSpectroAlpy600

_CONFIG = dict(
    indi_name="SM Pro",
    indi_client=dict(indi_host="192.0.2.48", indi_port=7624),
)


def _make_device():
    device = IndiSpectroAlpy600(config=_CONFIG, connect_on_create=False)
    device.set_switch = MagicMock()
    device.get_switch = MagicMock()
    return device


class TestIndiSpectroAlpy600:

    def test_set_light_source_sky_turns_both_channels_off(self):
        device = _make_device()

        device.set_light_source(LightSource.SKY)

        device.set_switch.assert_called_once_with(
            "POWER_CHANNELS", on_switches=[], off_switches=["POWER_CHANNEL_3", "POWER_CHANNEL_4"]
        )

    def test_set_light_source_dark_turns_both_channels_on(self):
        device = _make_device()

        device.set_light_source(LightSource.DARK)

        device.set_switch.assert_called_once_with(
            "POWER_CHANNELS", on_switches=["POWER_CHANNEL_3", "POWER_CHANNEL_4"], off_switches=[]
        )

    def test_set_light_source_calib_turns_channel_3_on_only(self):
        device = _make_device()

        device.set_light_source(LightSource.CALIB)

        device.set_switch.assert_called_once_with(
            "POWER_CHANNELS", on_switches=["POWER_CHANNEL_3"], off_switches=["POWER_CHANNEL_4"]
        )

    def test_set_light_source_flat_turns_channel_4_on_only(self):
        device = _make_device()

        device.set_light_source(LightSource.FLAT)

        device.set_switch.assert_called_once_with(
            "POWER_CHANNELS", on_switches=["POWER_CHANNEL_4"], off_switches=["POWER_CHANNEL_3"]
        )

    def test_get_light_source_reads_back_from_switch_state(self):
        device = _make_device()
        device.get_switch.return_value = {"POWER_CHANNEL_3": False, "POWER_CHANNEL_4": False}

        assert device.get_light_source() == LightSource.SKY

    def test_get_light_source_raises_on_unrecognized_state(self):
        device = _make_device()
        device.get_switch.return_value = {"POWER_CHANNEL_3": True, "POWER_CHANNEL_4": None}

        with pytest.raises(RuntimeError, match="unrecognized power-channel state"):
            device.get_light_source()
