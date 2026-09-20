"""
INDI camera device -- exposure control and image acquisition.

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
from ..wrapper.errors import ImageAcquisitionError

# Astropy
import astropy.units as u

class IndiCamera(IndiDevice):
    """ Indi Camera """

    UploadModeDict = {
        'local': 'UPLOAD_LOCAL',
        'client': 'UPLOAD_CLIENT',
        'both': 'UPLOAD_BOTH'}
    # sanitize_exp_time()'s fallback for an invalid or literal-zero requested
    # exposure -- was 5s (POCS heritage: 0 there meant "caller forgot to set
    # a value"), which silently turned every BIAS_ACQUISITION exposure
    # (0.0s by design -- see bias_acquisition.py) into a real 5s exposure.
    # 0.1s keeps a small nonzero floor (some camera drivers refuse a literal
    # 0s exposure) without materially delaying an actual BIAS series. Found
    # 2026-09-04, real BIAS frames on the 72ED coming out at 5s.
    DEFAULT_EXP_TIME_SEC = 0.1
    MAXIMUM_EXP_TIME_SEC = 3601
    READOUT_TIME_MARGIN = 300

    def __init__(self, logger=None, config=None, connect_on_create=True):
        logger = logger or logging.getLogger("spectro.indi.camera")

        if config is None:
            # TODO: dead fallback, never exercised in Rosace -- config is always
            # passed by _connect_device_sync() (hardware_connectivity.py). Leftover
            # PANOPTES/POCS-era defaults (pointing_seconds, adjust_*, autofocus_*,
            # nested "focuser") with no equivalent anywhere in Rosace's own hardware
            # YAMLs -- candidate for deletion, flagged 2026-08-11, not done now.
            config = dict(
                indi_name='CCD Simulator',
                pointing_seconds=30,
                default_exp_time_sec=5,
                gain=50,
                offset=30,
                adjust_center_x=400,
                adjust_center_y=400,
                adjust_roi_search_size=50,
                adjust_pointing_seconds=5,
                autofocus_seconds=5,
                autofocus_roi_size=500,
                autofocus_merit_function="half_flux_radius",
                focuser=dict(
                    module="IndiFocuser",
                    indi_name="Focuser Simulator",
                    port="/dev/ttyUSB0",
                    focus_range=dict(
                        min=1,
                        max=100000),
                    autofocus_step=dict(
                        coarse=10000,
                        fine=500),
                    autofocus_range=dict(
                        coarse=100000,
                        fine=20000),
                    indi_client=dict(
                        indi_host="localhost",
                        indi_port="7624")
                ),
                indi_client=dict(
                    indi_host="localhost",
                    indi_port="7624"
                ))
        device_name = config['indi_name']
        indi_driver_name = config.get('indi_driver_name', None)

        # Specific initialization -- set before connect() (below) can run,
        # since a connect_on_create=True caller would otherwise trigger our
        # own connect() override (which reads self.gain/self.offset) before
        # these attributes exist.
        self.default_exp_time_sec = config.get('default_exp_time_sec', 5)
        self.exp_time_sec = self.default_exp_time_sec
        self.gain = config.get('gain', 50)
        self.offset = config.get('offset', 30)
        # Gain/offset are only pushed to the real device (see connect()
        # below) when the hardware YAML declares them explicitly -- a CCD
        # (e.g. the Atik 314L) has no such properties at all and would
        # error out on CCD_GAIN/CCD_OFFSET; only a CMOS camera's YAML entry
        # is expected to set these keys. Falling back to 50/30 above is
        # just this class's own default *value*, not an implicit opt-in.
        self._push_gain_on_connect = 'gain' in config
        self._push_offset_on_connect = 'offset' in config

        # device related intialization
        IndiDevice.__init__(self,
                            device_name=device_name,
                            indi_driver_name=indi_driver_name,
                            indi_client_config=config["indi_client"])
        self.logger.debug(f"Indi camera, camera name is: {device_name}")
        if connect_on_create:
            self.connect()

        # Finished configuring
        self.logger.debug('Configured Indi Camera successfully')

    def connect(self, connect_device=True):
        """Same as IndiDevice.connect(), plus pushing gain/offset to the
        real device right after -- once, at connection time, never
        re-sent afterward. Only for a camera whose hardware YAML entry
        explicitly declares gain/offset (see __init__) -- a CCD with no
        such INDI properties (e.g. the Atik 314L) is never touched.
        Found 2026-09-20: with nothing ever calling set_gain()/
        set_offset(), a CMOS guiding camera was silently running on
        whatever gain/offset it last happened to have (0/0 in one real
        case), which starved its effective dynamic range."""
        IndiDevice.connect(self, connect_device=connect_device)
        if connect_device:
            if self._push_gain_on_connect:
                self.set_gain(self.gain)
            if self._push_offset_on_connect:
                self.set_offset(self.offset)

    @property
    def dynamic(self):
        return 2**self.get_dynamic()

    def on_emergency(self):
        self.logger.debug('on emergency routine started...')
        self.abort_shoot(sync=False)
        self.logger.debug('on emergency routine finished')

    '''
      Indi CCD related stuff
    '''
    def prepare_shoot(self):
        '''
          We should inform the indi server that we want to receive the
          "CCD1" blob from this device
        '''
        self.logger.debug('Indi client will register to server in order to receive blob CCD1 when it is ready')
        self.enable_blob()

    def disable_shoot(self):
        self.logger.debug('Indi client will unregister blob subscription')
        self.disable_blob()

    def synchronize_with_image_reception(self, exp_time_sec=None):
        try:
            if exp_time_sec is None:
                exp_time_sec = self.exp_time_sec
            # A 0s exposure (BIAS) still delivers a real blob -- it must still be waited
            # for, just with no exposure time added to the margin below.
            self.logger.debug(f"synchronize_with_image_reception: Start waiting for " \
                              f"{exp_time_sec}s with margin {self.READOUT_TIME_MARGIN}s")
            self.wait_for_incoming_blob_vector(timeout=exp_time_sec + self.READOUT_TIME_MARGIN)
            self.logger.debug('synchronize_with_image_reception: Done')
        except Exception as e:
            raise ImageAcquisitionError(f"Indi Camera Error in synchronize_with_image_reception: {e}")

    def get_received_image(self):
        try:
            self.logger.debug(f"Indicamera {self.device_name} about to read blob")
            blob = self.get_last_incoming_blob_vector()
            image = blob.get_fits()
            return image
        except Exception as e:
            self.logger.error(f"Indi Camera Error in get_received_image: {e}")

    def shoot_async(self):
        try:
            self.logger.info(f"launching acquisition with {self.exp_time_sec} sec exposure time")
            self.set_number('CCD_EXPOSURE',
                            {'CCD_EXPOSURE_VALUE': self.sanitize_exp_time(self.exp_time_sec)},
                            sync=False)
        except Exception as e:
            self.logger.error(f"Indi Camera Error in shoot: {e}")

    def get_remaining_exposure_time(self):
        return self.get_number('CCD_EXPOSURE')['CCD_EXPOSURE_VALUE']

    def get_thumbnail(self, exp_time_sec, thumbnail_size):
        """
            There are 4 cases:
            -ccd size is even, thumb size is even
            2-2 = 0 ok
            2-1 = 1 ok
            -ccd size is even, thumb size is odd
            2-1.5 = 0.5 floor
            2-0.5 = 1.5 floor
            -ccd size is odd, thumb size is even
            2.5-2 = 0.5 floor
            2.5-1 = 1.5 floor
            -ccd size is odd, thumb size is odd
            2.5-2.5 = 0 ok
            2.5-1.5 = 1 ok
        """
        sensor_size = self.get_sensor_size()
        thumbnail_size = min(thumbnail_size, sensor_size["CCD_MAX_X"])
        thumbnail_size = min(thumbnail_size, sensor_size["CCD_MAX_Y"])
        center_x = sensor_size["CCD_MAX_X"] / 2
        center_y = sensor_size["CCD_MAX_Y"] / 2
        left_most = np.floor(center_x - thumbnail_size / 2)
        top_most = np.floor(center_y - thumbnail_size / 2)
        roi = {'X': left_most, 'Y': top_most, 'WIDTH': thumbnail_size,
                     'HEIGHT': thumbnail_size}
        self.logger.debug(f"Setting camera {self.name} roi to {roi}")
        self.set_roi(roi)
        old_exp_time_sec = self.exp_time_sec
        self.exp_time_sec = exp_time_sec
        self.shoot_async()
        self.synchronize_with_image_reception()
        fits = self.get_received_image()
        roi = {'X': 0, 'Y': 0, 'WIDTH': sensor_size["CCD_MAX_X"],
                     'HEIGHT': sensor_size["CCD_MAX_Y"]}
        self.logger.debug(f"Resetting camera {self.name} roi to {roi}")
        self.set_roi(roi)
        self.exp_time_sec = old_exp_time_sec
        return fits

    def abort_shoot(self, sync=True):
        self.set_number('CCD_ABORT_EXPOSURE', {'ABORT': 1}, sync=sync, timeout=self.timeout)

    def launch_streaming(self):
        self.set_switch('VIDEO_STREAM', ['ON'])

    def set_upload_path(self, path, prefix = 'IMAGE_XXX'):
        self.set_text('UPLOAD_SETTINGS', {'UPLOAD_DIR': path, 'UPLOAD_PREFIX': prefix})

    def get_binning(self):
        return self.get_number('CCD_BINNING')

    def set_binning(self, hbin, vbin = None):
        if vbin == None:
            vbin = hbin
        self.set_number('CCD_BINNING', {'HOR_BIN': hbin, 'VER_BIN': vbin})

    def get_roi(self):
        """"
            X: Left-most pixel position
            Y: Top-most pixel position
            WIDTH: Frame width in pixels
            HEIGHT: Frame width in pixels
            ex: {'X':256, 'Y':480, 'WIDTH':512, 'HEIGHT':640}
        """
        number_vector = self.get_number('CCD_FRAME')
        return {k: number_vector[k] for k in ["X", "Y", "WIDTH", "HEIGHT"]}

    def set_roi(self, roi):
        """"
            X: Left-most pixel position
            Y: Top-most pixel position
            WIDTH: Frame width in pixels
            HEIGHT: Frame width in pixels
            ex: cam.set_roi({'X':256, 'Y':480, 'WIDTH':512, 'HEIGHT':640})
        """
        self.set_number('CCD_FRAME', roi, sync=True, timeout=self.timeout)

    def get_dynamic(self):
        return self.get_number('CCD_INFO')['CCD_BITSPERPIXEL']

    def get_maximum_dynamic(self):
        return self.get_dynamic()

    def get_sensor_size(self):
        number_vector = self.get_number('CCD_INFO')
        return {k: number_vector[k] for k in ["CCD_MAX_X", "CCD_MAX_Y"]}

    def get_pixel_size(self):
        number_vector = self.get_number('CCD_INFO')
        return {k: number_vector[k] for k in ["CCD_PIXEL_SIZE_X", "CCD_PIXEL_SIZE_Y"]}

    def get_temperature(self):
        return self.get_number('CCD_TEMPERATURE')['CCD_TEMPERATURE_VALUE']

    def set_temperature(self, temperature):
        """ It may take time to lower the temperature of a ccd """
        if isinstance(temperature, u.Quantity):
            temperature = temperature.to(u.deg_C).value
        if np.isfinite(temperature):
            # No sync: waiting here for the driver's own OK state would block
            # for the whole cooldown with zero visibility -- the caller
            # (camera_cooldown.py's cool_science_camera_to_target()) already
            # polls get_temperature() against its own tolerance and logs
            # progress every 5s, which needs this call to return immediately.
            self.set_number('CCD_TEMPERATURE',
                           {'CCD_TEMPERATURE_VALUE': temperature},
                           sync=False)

    def set_cooling_on(self):
        # No sync, because that's the way it works on indi for the CCD_COOLER property, it stays yellow in the interface
        self.set_switch('CCD_COOLER', ['COOLER_ON'], sync=False)

    def set_cooling_off(self):
        self.set_switch('CCD_COOLER', ['COOLER_OFF'], sync=True, timeout=self.timeout)

    def set_gain(self, value):
        self.set_number('CCD_GAIN', {'GAIN': value}, sync=True, timeout=self.timeout)

    def get_gain(self):
        gain = self.get_number('CCD_GAIN')
        return gain["GAIN"]

    def set_offset(self, value):
        self.set_number('CCD_OFFSET', {'OFFSET': value}, sync=True, timeout=self.timeout)

    def get_offset(self):
        offset = self.get_number('CCD_OFFSET')
        return offset["OFFSET"]

    def get_frame_type(self):
        # This is LIGHT or BIAS or DARK or FLAT
        return self.get_switch('CCD_FRAME_TYPE')

    def set_frame_type(self, frame_type):
        """
        FRAME_LIGHT Take a light frame exposure
        FRAME_BIAS Take a bias frame exposure
        FRAME_DARK Take a dark frame exposure
        FRAME_FLAT Take a flat field frame exposure
        """
        self.set_switch('CCD_FRAME_TYPE', [frame_type], sync=True, timeout=self.timeout)

    def setUploadTo(self, upload_to='local'):
        uploadTo = IndiCamera.UploadModeDict[upload_to]
        self.set_switch('UPLOAD_MODE', [uploadTo], sync=True, timeout=self.timeout)

    def sanitize_exp_time(self, exp_time_sec):
        if isinstance(exp_time_sec, u.Quantity):
            exp_time_sec = exp_time_sec.to(u.s).value
        if not isinstance(exp_time_sec, float):
            try:
                float_exp_time_sec = float(exp_time_sec)
            except Exception as e:
                float_exp_time_sec = self.DEFAULT_EXP_TIME_SEC
        elif exp_time_sec < 0:
            float_exp_time_sec = abs(exp_time_sec)
        elif exp_time_sec == 0:
            float_exp_time_sec = self.DEFAULT_EXP_TIME_SEC
        elif exp_time_sec > self.MAXIMUM_EXP_TIME_SEC:
            float_exp_time_sec = self.MAXIMUM_EXP_TIME_SEC
        else:
            float_exp_time_sec = exp_time_sec
        # Show warning if needed
        if float_exp_time_sec != exp_time_sec:
            self.logger.warning(f"Sanitizing exposition time: cannot accept "
                                f" {exp_time_sec}, using {float_exp_time_sec} "
                                f"instead")
        return float_exp_time_sec

    def getExpTimeSec(self):
        return self.sanitize_exp_time(self.exp_time_sec)

    def setExpTimeSec(self, exp_time_sec):
        self.exp_time_sec = self.sanitize_exp_time(exp_time_sec)

    def __str__(self):
        return f"INDI Camera {self.device_name}"

    def __repr__(self):
        return self.__str__()
