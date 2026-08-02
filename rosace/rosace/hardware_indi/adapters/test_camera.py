from unittest.mock import MagicMock, call

import pytest

from rosace.hardware.errors import DeviceCommandError
from rosace.hardware_indi.adapters.camera import CameraAdapter


class TestCameraAdapter:
    async def test_connect_calls_indi_camera(self):
        indi_camera = MagicMock()
        adapter = CameraAdapter(indi_camera)

        await adapter.connect()

        indi_camera.connect.assert_called_once()

    async def test_disconnect_calls_indi_camera(self):
        indi_camera = MagicMock()
        adapter = CameraAdapter(indi_camera)

        await adapter.disconnect()

        indi_camera.disconnect.assert_called_once()

    async def test_expose_runs_full_acquisition_sequence_in_order(self):
        indi_camera = MagicMock()
        indi_camera.get_received_image.return_value = "fake_fits_data"
        adapter = CameraAdapter(indi_camera)

        result = await adapter.expose(duration_sec=2.5)

        assert result == "fake_fits_data"
        assert indi_camera.method_calls == [
            call.prepare_shoot(),
            call.setExpTimeSec(2.5),
            call.shoot_async(),
            call.synchronize_with_image_reception(2.5),
            call.get_received_image(),
        ]

    async def test_expose_wraps_underlying_error(self):
        indi_camera = MagicMock()
        indi_camera.shoot_async.side_effect = RuntimeError("camera boom")
        adapter = CameraAdapter(indi_camera)

        with pytest.raises(DeviceCommandError, match="camera boom"):
            await adapter.expose(duration_sec=1.0)
