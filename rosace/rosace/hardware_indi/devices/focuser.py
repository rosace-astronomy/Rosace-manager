"""
INDI focuser device -- focus position control.

Distant heritage: this file originates from the PANOPTES/POCS observatory
control software (github.com/panoptes/POCS, MIT license) -- see
THIRD_PARTY_LICENSES.md at the repository root for the full license text.
"""

# Basic stuff
import logging

# Numerical stuff
import numpy as np

# Indi stuff
from ..wrapper.indi_device import IndiDevice

class IndiFocuser(IndiDevice):
    """

    """
    def __init__(self, config=None, logger=None, connect_on_create=False):
        self.logger = logger or logging.getLogger("spectro.indi.focuser")

        if config is None:
            config = dict(
                module="IndiFocuser",
                indi_name="Focuser Simulator",
                port="/dev/ttyUSB0",
                focus_range=dict(
                    min=1,
                    max=10000),
                autofocus_step=dict(
                    coarse=100,
                    fine=10),
                autofocus_range=dict(
                    coarse=3000,
                    fine=7000),
                indi_client=dict(
                    indi_host="localhost",
                    indi_port="7624"
                ))

        device_name=config['indi_name']
        indi_driver_name=config.get('indi_driver_name', None),

        self.port = config['port']
        self.focus_range = config['focus_range']
        self.autofocus_step = config['autofocus_step']
        self.autofocus_range = config['autofocus_range']

        self.logger.debug(f"Indi Focuser, focuser name is: {device_name}")

        # device related intialization
        IndiDevice.__init__(self,
                            device_name=device_name,
                            indi_driver_name=indi_driver_name,
                            indi_client_config=config["indi_client"])

        if connect_on_create:
            self.connect()

        # Finished configuring
        self.logger.debug('Indi Focuser configured successfully')

    def initialize(self):
        """
        This is not as simple a just connecting, because we must also set some
        specific values
        :return:
        """
        self.connect()
        self.set_port()

    def set_port(self):
        self.set_text("DEVICE_PORT", {"PORT": self.port}, sync=True, timeout=self.timeout)

    def on_emergency(self):
        self.logger.debug('Indi Focuser: on emergency routine started...')
        self.logger.debug('Indi Focuser: on emergency routine finished')

    def get_position(self):
        """ Current encoder position of the focuser """
        ret = self.get_number("ABS_FOCUS_POSITION")["FOCUS_ABSOLUTE_POSITION"]
        self.logger.debug(f"{self} : current position is {ret}")
        return ret

    def get_temperature(self):
        """ Ambient temperature reported by the focuser's own probe (INDI FOCUS_TEMPERATURE) """
        ret = self.get_number("FOCUS_TEMPERATURE")["TEMPERATURE"]
        self.logger.debug(f"{self} : current temperature is {ret}")
        return ret

    def move_to(self, position):
        """ Move focuser to new encoder position """
        self.logger.debug(f"{self}  moving to position {position}")
        self.set_number('ABS_FOCUS_POSITION',
                        {'FOCUS_ABSOLUTE_POSITION': np.float64(position)},
                        sync=True, timeout=self.timeout)
        new_position = self.get_position()
        self.logger.debug(f"{self} Now position is {new_position}")
        return new_position

    def __str__(self):
        return f"Focuser: {self.device_name}"

    def __repr__(self):
        return self.__str__()
