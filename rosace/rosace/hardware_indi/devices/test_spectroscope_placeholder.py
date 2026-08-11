"""
Tests for SpectroscopePlaceholder. Launch:
pytest hardware_indi/devices/test_spectroscope_placeholder.py -v
"""

from rosace.hardware.spectroscope import LightSource
from rosace.hardware_indi.devices.spectroscope_placeholder import SpectroscopePlaceholder


class TestSpectroscopePlaceholder:

    def test_defaults_to_sky(self):
        device = SpectroscopePlaceholder()

        assert device.get_light_source() == LightSource.SKY

    def test_set_light_source_changes_state(self):
        device = SpectroscopePlaceholder()

        device.set_light_source(LightSource.FLAT)

        assert device.get_light_source() == LightSource.FLAT

    def test_connect_and_disconnect_do_not_raise(self):
        device = SpectroscopePlaceholder()

        device.connect()
        device.disconnect()

    def test_connect_on_create_does_not_raise(self):
        device = SpectroscopePlaceholder(connect_on_create=True)

        assert device.get_light_source() == LightSource.SKY
