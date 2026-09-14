"""
Tests for IndiMount's post-slew position verification (_verify_slew_reached)
-- the "did the mount actually get there" guard added after the 2026-09-11
field-test incident (see mount.py's own module-level comment for the full
story: a slew issued right after a sync returned in under 100ms while the
mount was still at the pre-sync position, then physically slewed during the
following exposure, trailing the star across the frame).

Everything else on IndiMount (adapted from POCS, thin INDI property
wrappers) isn't independently unit-tested -- only the guard logic added on
top is exercised here. set_coord() itself is mocked out rather than
exercised for real: it isn't the subject of these tests, and letting it
run for real means a genuine ICRS->FK5(equinox=Time.now()) astropy
transform on every test, which can block on an IERS/leap-second table
refresh with no network available. set_switch/get_switch/
get_current_coordinates are mocked directly on the instance too, no real
INDI connection involved (same pattern as test_spectro_Alpy600.py).
"""

from unittest.mock import MagicMock

import pytest
from astropy.coordinates import SkyCoord

from rosace.hardware_indi.devices.mount import IndiMount, SLEW_POSITION_TOLERANCE_ARCMIN

_CONFIG = dict(
    indi_name="Mount",
    indi_client=dict(indi_host="localhost", indi_port=7624),
)

_TARGET = SkyCoord(ra=279.23, dec=38.78, unit="deg", frame="icrs")


def _make_device(parked=False):
    device = IndiMount(config=_CONFIG, connect_on_create=False)
    device.set_switch = MagicMock()
    device.set_coord = MagicMock()
    device.get_switch = MagicMock(return_value={"PARK": parked})
    device.get_current_coordinates = MagicMock(return_value=_TARGET)
    return device


def _dec_offset_coord(arcmin):
    """A coordinate at DEC=0 offset purely in declination by `arcmin` from
    a DEC=0/RA=0 target -- angular separation is then exactly `arcmin`,
    with no RA*cos(dec) scaling to account for, ideal for a precise
    boundary test."""
    return SkyCoord(ra=0.0, dec=arcmin / 60.0, unit="deg", frame="icrs")


class TestSlewVerification:
    def test_accepts_position_within_tolerance(self):
        device = _make_device()
        close_enough = SkyCoord(ra=279.235, dec=38.782, unit="deg", frame="icrs")  # a few arcsec off
        device.get_current_coordinates.return_value = close_enough

        device.slew_to_coord_and_track(_TARGET)

        device.get_current_coordinates.assert_called_once()

    def test_accepts_position_just_under_tolerance_boundary(self):
        device = _make_device()
        target = SkyCoord(ra=0.0, dec=0.0, unit="deg", frame="icrs")
        device.get_current_coordinates.return_value = _dec_offset_coord(SLEW_POSITION_TOLERANCE_ARCMIN * 0.99)

        device.slew_to_coord_and_track(target)

        device.get_current_coordinates.assert_called_once()

    def test_waits_when_just_over_tolerance_boundary(self, monkeypatch):
        device = _make_device()
        target = SkyCoord(ra=0.0, dec=0.0, unit="deg", frame="icrs")
        just_over = _dec_offset_coord(SLEW_POSITION_TOLERANCE_ARCMIN * 1.01)
        device.get_current_coordinates.side_effect = [just_over, target]
        monkeypatch.setattr("rosace.hardware_indi.devices.mount.time.sleep", MagicMock())

        device.slew_to_coord_and_track(target)

        assert device.get_current_coordinates.call_count == 2

    def test_waits_out_a_stale_property_return_then_succeeds(self, monkeypatch):
        device = _make_device()
        far_away = SkyCoord(ra=_TARGET.ra.deg - 50, dec=_TARGET.dec.deg, unit="deg", frame="icrs")
        device.get_current_coordinates.side_effect = [far_away, far_away, _TARGET]
        monkeypatch.setattr("rosace.hardware_indi.devices.mount.time.sleep", MagicMock())

        device.slew_to_coord_and_track(_TARGET)

        assert device.get_current_coordinates.call_count == 3

    def test_raises_if_mount_never_arrives(self, monkeypatch):
        device = _make_device()
        far_away = SkyCoord(ra=_TARGET.ra.deg - 50, dec=_TARGET.dec.deg, unit="deg", frame="icrs")
        device.get_current_coordinates.return_value = far_away
        # A negative timeout makes the loop's very first check fail (real
        # elapsed time is always > a negative number) without ever calling
        # time.sleep() -- avoids both a real ~180s wait and the pitfall of
        # monkeypatching time.time() itself, which logging's own internal
        # timestamping also relies on process-wide.
        monkeypatch.setattr("rosace.hardware_indi.devices.mount.SLEW_VERIFY_TIMEOUT_SECONDS", -1)

        with pytest.raises(RuntimeError, match="never reached target"):
            device.slew_to_coord_and_track(_TARGET)

    def test_skips_verification_when_parked(self):
        device = _make_device(parked=True)

        device.slew_to_coord_and_track(_TARGET)

        device.get_current_coordinates.assert_not_called()

    def test_slew_to_coord_and_stop_also_verifies(self, monkeypatch):
        device = _make_device()
        far_away = SkyCoord(ra=_TARGET.ra.deg - 50, dec=_TARGET.dec.deg, unit="deg", frame="icrs")
        device.get_current_coordinates.return_value = far_away
        monkeypatch.setattr("rosace.hardware_indi.devices.mount.SLEW_VERIFY_TIMEOUT_SECONDS", -1)

        with pytest.raises(RuntimeError, match="never reached target"):
            device.slew_to_coord_and_stop(_TARGET)
