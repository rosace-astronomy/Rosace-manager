"""
Generic device error hierarchy. Every backend (hardware_indi/, future
hardware_ascom/) translates its own native errors into these, so calling
code (SEQUENCER sub-plugins, connectivity checks) never depends on a
protocol-specific exception type.
"""


class DeviceError(Exception):
    """Base class for all hardware/ device errors."""


class DeviceConnectionError(DeviceError):
    """The device could not be reached, or the connection was lost."""


class DeviceTimeoutError(DeviceError):
    """The device did not respond within the expected time."""


class DeviceCommandError(DeviceError):
    """The device rejected, or failed to execute, a command."""
