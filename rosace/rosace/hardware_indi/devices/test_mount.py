"""
Tests for IndiMount's slew wait (_wait_for_slew): wait for the driver to
report the slew started (EQUATORIAL_EOD_COORD BUSY), then finished (OK),
then check the position once -- see mount.py's own module-level comment for
why a plain wait for OK is not enough (2026-09-11 field test: a stale OK
left by a sync made a 50 deg slew "finish" before the mount had moved).

Everything else on IndiMount (adapted from POCS, thin INDI property
wrappers) isn't independently unit-tested -- only the logic added on top is
exercised here. set_coord() itself is mocked out rather than exercised for
real: it isn't the subject of these tests, and letting it run for real
means a genuine ICRS->FK5(equinox=Time.now()) astropy transform on every
test, which can block on an IERS/leap-second table refresh with no network
available. set_switch/get_switch/get_current_coordinates/_coord_state are
mocked directly on the instance too, no real INDI connection involved (same
pattern as test_spectro_Alpy600.py). Timeouts are forced by setting the
timeout constants to -1 rather than by faking time.time(), which logging's
own timestamping also uses process-wide.
"""

from unittest.mock import MagicMock

import PyIndi
import pytest
from astropy.coordinates import SkyCoord

from rosace.hardware_indi.devices.mount import IndiMount, SLEW_POSITION_TOLERANCE_ARCMIN

_CONFIG = dict(
    indi_name="Mount",
    indi_client=dict(indi_host="localhost", indi_port=7624),
)

_TARGET = SkyCoord(ra=279.23, dec=38.78, unit="deg", frame="icrs")
_FAR_AWAY = SkyCoord(ra=_TARGET.ra.deg - 50, dec=_TARGET.dec.deg, unit="deg", frame="icrs")

OK, BUSY, ALERT = PyIndi.IPS_OK, PyIndi.IPS_BUSY, PyIndi.IPS_ALERT


def _make_device(states=(OK, BUSY, BUSY, OK), position=_TARGET, parked=False):
    """`states` is the sequence of EQUATORIAL_EOD_COORD states the driver
    reports, one per read; the first OK is the stale one left by the
    previous command."""
    device = IndiMount(config=_CONFIG, connect_on_create=False)
    device.set_switch = MagicMock()
    device.set_coord = MagicMock()
    device.get_switch = MagicMock(return_value={"PARK": parked})
    device.get_current_coordinates = MagicMock(return_value=position)
    device._coord_state = MagicMock(side_effect=list(states))
    return device


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("rosace.hardware_indi.devices.mount.time.sleep", MagicMock())


def _dec_offset_coord(arcmin):
    """A coordinate offset purely in declination by `arcmin` from RA=0/DEC=0
    -- angular separation is then exactly `arcmin`, ideal for a boundary
    test."""
    return SkyCoord(ra=0.0, dec=arcmin / 60.0, unit="deg", frame="icrs")


class TestSlewWait:
    def test_waits_past_a_stale_ok_for_busy_then_ok(self):
        device = _make_device(states=(OK, OK, BUSY, BUSY, OK))

        device.slew_to_coord_and_track(_TARGET)

        assert device._coord_state.call_count == 5
        device.get_current_coordinates.assert_called_once()

    def test_sends_the_coordinate_without_the_plain_ok_wait(self):
        device = _make_device()

        device.slew_to_coord_and_track(_TARGET)

        device.set_coord.assert_called_once_with(_TARGET, wait=False)

    def test_accepts_arrival_just_under_the_tolerance(self):
        target = SkyCoord(ra=0.0, dec=0.0, unit="deg", frame="icrs")
        device = _make_device(position=_dec_offset_coord(SLEW_POSITION_TOLERANCE_ARCMIN * 0.99))

        device.slew_to_coord_and_track(target)

    def test_raises_if_mount_says_arrived_but_is_elsewhere(self):
        target = SkyCoord(ra=0.0, dec=0.0, unit="deg", frame="icrs")
        device = _make_device(position=_dec_offset_coord(SLEW_POSITION_TOLERANCE_ARCMIN * 1.01))

        with pytest.raises(RuntimeError, match="reports the slew complete but is"):
            device.slew_to_coord_and_track(target)

    def test_no_busy_seen_but_already_on_target_is_accepted(self, monkeypatch):
        """Already at the requested position, or a slew so short it started
        and ended between two reads."""
        monkeypatch.setattr("rosace.hardware_indi.devices.mount.SLEW_START_TIMEOUT_SECONDS", -1)
        device = _make_device(states=(OK,))

        device.slew_to_coord_and_track(_TARGET)

        device.get_current_coordinates.assert_called_once()

    def test_raises_if_mount_never_starts_and_is_not_on_target(self, monkeypatch):
        monkeypatch.setattr("rosace.hardware_indi.devices.mount.SLEW_START_TIMEOUT_SECONDS", -1)
        device = _make_device(states=(OK,), position=_FAR_AWAY)

        with pytest.raises(RuntimeError, match="never started slewing"):
            device.slew_to_coord_and_track(_TARGET)

    def test_raises_if_slew_never_finishes(self, monkeypatch):
        monkeypatch.setattr("rosace.hardware_indi.devices.mount.SLEW_FINISH_TIMEOUT_SECONDS", -1)
        device = _make_device(states=(BUSY, BUSY))

        with pytest.raises(RuntimeError, match="not finished"):
            device.slew_to_coord_and_track(_TARGET)

    def test_raises_on_driver_alert(self):
        device = _make_device(states=(BUSY, ALERT))

        with pytest.raises(RuntimeError, match="alert"):
            device.slew_to_coord_and_track(_TARGET)

    def test_skips_the_wait_when_parked(self):
        device = _make_device(parked=True)

        device.slew_to_coord_and_track(_TARGET)

        device._coord_state.assert_not_called()
        device.get_current_coordinates.assert_not_called()

    def test_slew_to_coord_and_stop_waits_too(self):
        device = _make_device(position=_FAR_AWAY)

        with pytest.raises(RuntimeError, match="reports the slew complete but is"):
            device.slew_to_coord_and_stop(_TARGET)

        device.set_coord.assert_called_once_with(_TARGET, wait=False)


class TestCoordState:
    def test_reads_the_state_of_the_coordinate_property(self):
        device = IndiMount(config=_CONFIG, connect_on_create=False)
        prop = MagicMock()
        prop.getState.return_value = BUSY
        device.get_prop = MagicMock(return_value=prop)

        assert device._coord_state() == BUSY
        device.get_prop.assert_called_once_with("EQUATORIAL_EOD_COORD", "number")
