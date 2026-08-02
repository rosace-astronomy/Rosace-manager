# --------------------------------------
# Indi USIS spectroscope device
# This is the generic device of an Indi USIS spectroscope
# --------------------------------------

# Basic stuff
import logging

# Indi stuff
from ..wrapper.indi_device import IndiDevice

class IndiUsisSpectro(IndiDevice):
    def __init__(self, config=None, logger=None, connect_on_create=False):
        self.logger = logger or logging.getLogger(__name__)

        if config is None:
            config = dict(
                module="IndiUsisSpectro",
                USIS_spectro_name="USIS Simulator",
                )

        device_name = config['USIS_spectro_name']
        indi_driver_name = config.get('indi_driver_name', None)

        self.logger.debug('Indi USIS spectro, name is: {}'.format(device_name))

        # device related intialization
        IndiDevice.__init__(self,
                            device_name=device_name,
                            indi_driver_name=indi_driver_name,
                            indi_client_config=config["indi_client"])
        if connect_on_create:
            self.connect()

        # Finished configuring
        self.logger.debug('USIS spectro configured successfully')

    def __str__(self):
        return f"USIS Spectroscope: {self.device_name}"

    def __repr__(self):
        return self.__str__()
