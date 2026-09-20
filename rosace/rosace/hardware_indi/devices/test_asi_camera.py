"""
Tests for IndiASICamera. Launch:
pytest hardware_indi/devices/test_asi_camera.py -v

Covers what makes this class different from the plain IndiCamera it
subclasses: ZWO ASI cameras expose gain/offset as elements of a single
combined CCD_CONTROLS number vector ("Gain"/"Offset"), not the separate
CCD_GAIN/CCD_OFFSET vectors IndiCamera assumes -- found 2026-09-20 when a
real-hardware test capture against the guiding camera (ASI120MM Mini)
timed out waiting for a CCD_GAIN property that this driver doesn't have.
IndiCamera.connect()'s own push-gain/offset-once behavior is covered by
test_camera.py and applies here unchanged (IndiASICamera doesn't override
connect()) -- this file only exercises the ASI-specific property names.
"""

from unittest.mock import MagicMock

from rosace.hardware_indi.devices.asi_camera import IndiASICamera

_CONFIG = dict(
    indi_name="ZWO CCD ASI120MM Mini",
    indi_client=dict(indi_host="192.168.144.50", indi_port=7624),
    gain=50,
    offset=20,
)


def _make_camera(monkeypatch):
    monkeypatch.setattr("rosace.hardware_indi.devices.camera.IndiDevice.connect", MagicMock())
    camera = IndiASICamera(config=_CONFIG, connect_on_create=False)
    camera.set_number = MagicMock()
    camera.get_number = MagicMock()
    return camera


class TestIndiASICameraGainOffset:

    def test_set_gain_uses_ccd_controls_gain_element(self, monkeypatch):
        camera = _make_camera(monkeypatch)

        camera.set_gain(50)

        camera.set_number.assert_called_once_with(
            "CCD_CONTROLS", {"Gain": 50}, sync=True, timeout=camera.timeout
        )

    def test_set_offset_uses_ccd_controls_offset_element(self, monkeypatch):
        camera = _make_camera(monkeypatch)

        camera.set_offset(20)

        camera.set_number.assert_called_once_with(
            "CCD_CONTROLS", {"Offset": 20}, sync=True, timeout=camera.timeout
        )

    def test_get_gain_reads_back_from_ccd_controls(self, monkeypatch):
        camera = _make_camera(monkeypatch)
        camera.get_number.return_value = {"Gain": 50, "Offset": 20, "BandWidth": 50}

        assert camera.get_gain() == 50
        camera.get_number.assert_called_once_with("CCD_CONTROLS")

    def test_get_offset_reads_back_from_ccd_controls(self, monkeypatch):
        camera = _make_camera(monkeypatch)
        camera.get_number.return_value = {"Gain": 50, "Offset": 20, "BandWidth": 50}

        assert camera.get_offset() == 20

    def test_connect_pushes_gain_and_offset_via_ccd_controls(self, monkeypatch):
        # End-to-end through the inherited IndiCamera.connect() override --
        # this is the exact real-hardware path that originally failed.
        camera = _make_camera(monkeypatch)

        camera.connect()

        assert camera.set_number.call_args_list == [
            (("CCD_CONTROLS", {"Gain": 50}), {"sync": True, "timeout": camera.timeout}),
            (("CCD_CONTROLS", {"Offset": 20}), {"sync": True, "timeout": camera.timeout}),
        ]
