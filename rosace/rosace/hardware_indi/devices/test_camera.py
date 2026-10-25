"""
Tests for IndiCamera. Launch:
pytest hardware_indi/devices/test_camera.py -v

Only IndiCamera's own connect() override is exercised here (whether it
pushes gain/offset to the real device) -- IndiDevice.connect() itself is
mocked out, no real INDI connection involved, same "cover whatever is
testable in isolation" rule as test_spectro_Alpy600.py.
"""

from unittest.mock import MagicMock

from rosace.hardware_indi.devices.camera import IndiCamera

_BASE_CONFIG = dict(
    indi_name="Test Camera",
    indi_client=dict(indi_host="192.0.2.48", indi_port=7624),
)


def _make_camera(monkeypatch, config):
    monkeypatch.setattr("rosace.hardware_indi.devices.camera.IndiDevice.connect", MagicMock())
    camera = IndiCamera(config=config, connect_on_create=False)
    camera.set_gain = MagicMock()
    camera.set_offset = MagicMock()
    return camera


class TestIndiCameraGainOffset:

    def test_connect_pushes_gain_and_offset_when_both_declared(self, monkeypatch):
        config = dict(_BASE_CONFIG, gain=50, offset=20)
        camera = _make_camera(monkeypatch, config)

        camera.connect()

        camera.set_gain.assert_called_once_with(50)
        camera.set_offset.assert_called_once_with(20)

    def test_connect_pushes_nothing_when_not_declared(self, monkeypatch):
        camera = _make_camera(monkeypatch, dict(_BASE_CONFIG))

        camera.connect()

        camera.set_gain.assert_not_called()
        camera.set_offset.assert_not_called()

    def test_connect_pushes_only_the_declared_one(self, monkeypatch):
        config = dict(_BASE_CONFIG, gain=50)
        camera = _make_camera(monkeypatch, config)

        camera.connect()

        camera.set_gain.assert_called_once_with(50)
        camera.set_offset.assert_not_called()

    def test_connect_device_false_pushes_nothing(self, monkeypatch):
        config = dict(_BASE_CONFIG, gain=50, offset=20)
        camera = _make_camera(monkeypatch, config)

        camera.connect(connect_device=False)

        camera.set_gain.assert_not_called()
        camera.set_offset.assert_not_called()

    def test_ccd_without_gain_offset_config_is_never_touched(self, monkeypatch):
        # e.g. the Atik 314L -- a CCD with no CCD_GAIN/CCD_OFFSET property
        # at all -- must never have set_gain()/set_offset() called on it.
        camera = _make_camera(monkeypatch, dict(_BASE_CONFIG))

        camera.connect()
        camera.connect(connect_device=True)

        camera.set_gain.assert_not_called()
        camera.set_offset.assert_not_called()
