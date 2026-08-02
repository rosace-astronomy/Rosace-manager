import pytest

from rosace.hardware.errors import (
    DeviceCommandError,
    DeviceConnectionError,
    DeviceError,
    DeviceTimeoutError,
)


class TestDeviceErrorHierarchy:
    @pytest.mark.parametrize(
        "exc_cls", [DeviceConnectionError, DeviceTimeoutError, DeviceCommandError]
    )
    def test_all_specific_errors_are_device_errors(self, exc_cls):
        assert issubclass(exc_cls, DeviceError)

    def test_device_error_is_exception(self):
        assert issubclass(DeviceError, Exception)
