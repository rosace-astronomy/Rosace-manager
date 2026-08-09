from unittest.mock import MagicMock

import pytest
from astropy.coordinates import SkyCoord

from rosace.hardware.errors import DeviceCommandError
from rosace.hardware_indi.adapters.mount import MountAdapter


class TestMountAdapter:
    async def test_connect_calls_indi_mount(self):
        indi_mount = MagicMock()
        adapter = MountAdapter(indi_mount)

        await adapter.connect()

        indi_mount.connect.assert_called_once()

    async def test_disconnect_calls_indi_mount(self):
        indi_mount = MagicMock()
        adapter = MountAdapter(indi_mount)

        await adapter.disconnect()

        indi_mount.disconnect.assert_called_once()

    async def test_slew_to_calls_indi_mount_with_correct_coord(self):
        indi_mount = MagicMock()
        adapter = MountAdapter(indi_mount)

        await adapter.slew_to(ra=279.23, dec=38.78)

        indi_mount.slew_to_coord_and_track.assert_called_once()
        coord = indi_mount.slew_to_coord_and_track.call_args[0][0]
        assert isinstance(coord, SkyCoord)
        assert coord.ra.deg == pytest.approx(279.23)
        assert coord.dec.deg == pytest.approx(38.78)

    async def test_slew_to_wraps_underlying_error(self):
        indi_mount = MagicMock()
        indi_mount.slew_to_coord_and_track.side_effect = RuntimeError("indi boom")
        adapter = MountAdapter(indi_mount)

        with pytest.raises(DeviceCommandError, match="indi boom"):
            await adapter.slew_to(ra=0.0, dec=0.0)

    async def test_get_position_returns_ra_dec_from_indi_mount(self):
        indi_mount = MagicMock()
        indi_mount.get_current_coordinates.return_value = SkyCoord(ra=279.23, dec=38.78, unit="deg", frame="icrs")
        adapter = MountAdapter(indi_mount)

        ra, dec = await adapter.get_position()

        indi_mount.get_current_coordinates.assert_called_once()
        assert ra == pytest.approx(279.23)
        assert dec == pytest.approx(38.78)

    async def test_get_position_wraps_underlying_error(self):
        indi_mount = MagicMock()
        indi_mount.get_current_coordinates.side_effect = RuntimeError("indi boom")
        adapter = MountAdapter(indi_mount)

        with pytest.raises(DeviceCommandError, match="indi boom"):
            await adapter.get_position()
