"""
INDI mount device -- slew/park/coordinate handling.

Distant heritage: this file originates from the PANOPTES/POCS observatory
control software (github.com/panoptes/POCS, MIT license) -- see
THIRD_PARTY_LICENSES.md at the repository root for the full license text.
"""

# Basic stuff
import logging
import time

# Indi stuff
import PyIndi
from ..wrapper.indi_device import IndiDevice

# Rosace stuff
from ...astro_math import local_sidereal_time_deg

# Astropy stuff
from astropy import units as u
from astropy.time import Time
from astropy.coordinates import SkyCoord
from astropy.coordinates import ICRS
from astropy.coordinates import FK5

# A slew is over when the mount says so: the driver sets EQUATORIAL_EOD_COORD
# to BUSY when it starts moving and back to OK when it has arrived. The
# catch is the start: right after another command on the same property (a
# SYNC, or the previous slew), the property still reads OK, and a wait that
# only looks for OK can see that stale OK before the driver has switched to
# BUSY -- it then returns while the mount has not even left (field test
# 2026-09-11: a 50 deg slew "finished" 97 ms after a sync, the mount then
# moved during the next exposure). _wait_for_slew() below therefore waits
# for BUSY first, then for OK (see HISTORY.md, 2026-09-12 and 2026-09-27).
#
# If no BUSY is seen within SLEW_START_TIMEOUT_SECONDS (already on target,
# or a slew so short it started and ended between two reads), the position
# decides: on target is fine, anywhere else means the command was not
# carried out. The position is also checked once after OK, as a consistency
# check. Tolerance: real completed slews land within a few arcsec, and the
# smallest deliberate back-to-back slew is the 2 deg backlash approach
# (hardware YAML's backlash_offset_deg), so 10' cannot mistake one of those
# slews for another.
SLEW_POSITION_TOLERANCE_ARCMIN = 10.0
SLEW_START_TIMEOUT_SECONDS = 5
SLEW_FINISH_TIMEOUT_SECONDS = 60
SLEW_POLL_INTERVAL_SECONDS = 0.05

class IndiMount(IndiDevice):
    """
        We recall that, with indi, telescopes can be adressed using JNow/J2000
        coordinates, see:
        http://indilib.org/develop/developer-manual/101-standard-properties.html#h3-telescopes

        EQUATORIAL_EOD_COORD:
        Equatorial astrometric epoch of date coordinate
        RA JNow RA, hours
        DEC JNow Dec, degrees +N

        EQUATORIAL_COORD:
        Equatorial astrometric J2000 coordinate:
        RA  J2000 RA, hours
        DEC J2000 Dec, degrees +N

        HORIZONTAL_COORD:
        topocentric coordinate
        ALT Altitude, degrees above horizon
        AZ Azimuth, degrees E of N

        There is also another important switch we need to use: pier side

        TELESCOPE_PIER_SIDE : GEM Pier Side
            PIER_EAST : Mount on the East side of pier (Pointing West).
            PIER_WEST : Mount on the West side of pier (Pointing East).
    """
    def __init__(self, config=None, logger=None, connect_on_create=False):
        self.logger = logger or logging.getLogger("spectro.indi.mount")

        assert (config is not None) and (type(config) == dict), ("Please provide config as dictionary, with indi_name")
        device_name = config['indi_name']
        indi_driver_name = config.get('indi_driver_name', None)

        self.logger.debug(f"Indi Mount, mount name is: {device_name}")

        # device related intialization
        IndiDevice.__init__(self,
                            device_name=device_name,
                            indi_driver_name=indi_driver_name,
                            indi_client_config=config['indi_client'])

        if connect_on_create:
            self.connect(connect_device=True)

        # Finished configuring
        self.logger.debug('Indi Mount configured successfully')

    def on_emergency(self):
        self.logger.debug('on emergency routine started...')
        self.abort_motion()
        self.park()
        self.logger.debug('on emergency routine finished')

    def slew_to_coord_and_stop(self, coord):
        """
            Slew to a coordinate and stop upon receiving coordinates.
        """
        self.on_coord_set('SLEW')
        self.set_coord(coord, wait=False)
        if not self.is_parked:
            self._wait_for_slew(coord)

    def slew_to_coord_and_track(self, coord):
        """
            Slew to a coordinate and track upon receiving coordinates.
        """
        self.on_coord_set('TRACK')
        self.set_coord(coord, wait=False)
        if not self.is_parked:
            self._wait_for_slew(coord)

    def _wait_for_slew(self, target_coord):
        """Waits for the mount to report the slew started (BUSY), then
        finished (OK) -- see the module-level comment above
        SLEW_POSITION_TOLERANCE_ARCMIN. Raises RuntimeError if the command
        was not carried out, the slew does not finish in time, the driver
        reports an alert, or the mount says it has arrived somewhere else."""
        started = time.time()
        while self._coord_state() != PyIndi.IPS_BUSY:
            if time.time() - started > SLEW_START_TIMEOUT_SECONDS:
                separation_arcmin = self._separation_arcmin(target_coord)
                if separation_arcmin <= SLEW_POSITION_TOLERANCE_ARCMIN:
                    self.logger.info(
                        f"No slew seen, mount already on target ({separation_arcmin:.1f}' from commanded)"
                    )
                    return
                raise RuntimeError(
                    f"Mount never started slewing: still {separation_arcmin:.1f}' from target "
                    f"(commanded RA={target_coord.ra.deg:.4f} DEC={target_coord.dec.deg:.4f}) "
                    f"after {SLEW_START_TIMEOUT_SECONDS}s"
                )
            time.sleep(SLEW_POLL_INTERVAL_SECONDS)

        started = time.time()
        while (state := self._coord_state()) == PyIndi.IPS_BUSY:
            if time.time() - started > SLEW_FINISH_TIMEOUT_SECONDS:
                raise RuntimeError(
                    f"Mount slew not finished after {SLEW_FINISH_TIMEOUT_SECONDS}s "
                    f"(commanded RA={target_coord.ra.deg:.4f} DEC={target_coord.dec.deg:.4f})"
                )
            time.sleep(SLEW_POLL_INTERVAL_SECONDS)
        if state == PyIndi.IPS_ALERT:
            raise RuntimeError(
                f"Mount driver reported an alert during the slew "
                f"(commanded RA={target_coord.ra.deg:.4f} DEC={target_coord.dec.deg:.4f})"
            )

        separation_arcmin = self._separation_arcmin(target_coord)
        if separation_arcmin > SLEW_POSITION_TOLERANCE_ARCMIN:
            raise RuntimeError(
                f"Mount reports the slew complete but is {separation_arcmin:.1f}' from target "
                f"(commanded RA={target_coord.ra.deg:.4f} DEC={target_coord.dec.deg:.4f})"
            )

    def _coord_state(self):
        """Current INDI state of EQUATORIAL_EOD_COORD (PyIndi.IPS_*): BUSY
        while the driver is carrying out a slew."""
        return self.get_prop('EQUATORIAL_EOD_COORD', 'number').getState()

    def _separation_arcmin(self, target_coord):
        return target_coord.separation(self.get_current_coordinates()).arcmin

    def sync_to_coord(self, coord):
        """
           Accept current coordinate as correct upon receiving coordinates.
        """
        self.on_coord_set('SYNC')
        self.set_coord(coord)
        self.on_coord_set('TRACK')

    def set_coord(self, coord, wait=True):
        """
        Subtleties here for INDILIB: coord should be given as FK% frame with Equatorial astrometric epoch
        of date coordinate (eod):  RA JNow RA, hours,  DEC JNow Dec, degrees +N

        As our software only manipulates ICRS J2000. We decided to convert to FK5 jnow for the generic case

        wait=False sends the coordinate and returns at once: the slew methods
        wait with _wait_for_slew() instead, since a plain wait for OK can see
        the OK left by the previous command (see the module-level comment).
        """
        fk5_now = FK5(equinox=Time.now())
        coord_now = coord.transform_to(fk5_now)
        rahour_decdeg = {'RA': coord_now.ra.hour,
                         'DEC': coord_now.dec.degree}
        if self.is_parked:
            self.logger.warning(f"Cannot set coord: {rahour_decdeg} because "
                                f"mount is parked")
        else:
            coord_formatted = coord_now.to_string(style="hmsdms", sep=":", precision=1)
            self.logger.info(f"Now setting JNow coord: {rahour_decdeg} = {coord_formatted}")
            self.set_number('EQUATORIAL_EOD_COORD', rahour_decdeg, sync=wait,
                            timeout=180)

    def on_coord_set(self, what_to_do='TRACK'):
        """ What do to with the new set of given coordinates
            Can be either:
            SLEW: Slew to a coordinate and stop upon receiving coordinates.
            TRACK: Slew to a coordinate and track upon receiving coordinates.
            SYNC: Accept current coordinate as correct upon receiving coordinates
        """
        self.logger.debug('Setting ON_COORD_SET behaviour: {}'.format(
                          what_to_do))
        self.set_switch('ON_COORD_SET', [what_to_do], sync=True, timeout=self.timeout)

    def abort_motion(self):
        """ This is to immediately stop any motion
        """
        self.logger.debug('Stop Motion')
        self.set_switch('TELESCOPE_ABORT_MOTION', ['ABORT'], sync=True, timeout=self.timeout)

    def park(self):
        """
        Timeout is much higher here, because the telescope might need to move to its parking position at a low speed
        :return:
        """
        self.logger.debug('Slewing to Park')
        self.set_switch('TELESCOPE_PARK', ['PARK'], sync=True, timeout=180) #It can take some time to park a mount

    def unpark(self):
        self.logger.debug('Unparking with indi mount semantic')
        self.set_switch('TELESCOPE_PARK', ['UNPARK'], sync=True, timeout=self.timeout)

    def get_guide_rate(self):
        """
            GUIDE_RATE number should look like this:
            {'GUIDE_RATE_WE': {
                 'name': 'GUIDE_RATE_WE',
                 'label': 'W/E Rate',
                 'value': 0.5,
                 'min': 0.0,
                 'max': 1.0,
                 'step': 0.1,
                 'format': '%g'},
             'GUIDE_RATE_NS': {
                 'name': 'GUIDE_RATE_NS',
                 'label': 'N/S Rate',
                 'value': 0.5,
                 'min': 0.0,
                 'max': 1.0,
                 'step': 0.1,
                 'format': '%g'},
             'state': 'OK'}
        """
        guide_dict = self.get_number('GUIDE_RATE')
        guide_rate = {}
        guide_rate['NS'] = guide_dict['GUIDE_RATE_NS']
        guide_rate['WE'] = guide_dict['GUIDE_RATE_WE']
        return guide_rate

    def set_guide_rate(self, guide_rate={'NS':0.5,'WE':0.5}):
        """
        """
        assert(all([v<=1 for k,v in guide_rate.items()]))
        guide_dict = {'GUIDE_RATE_NS': guide_rate['NS'],
                      'GUIDE_RATE_WE': guide_rate['WE']}
        self.set_number('GUIDE_RATE', guide_dict, sync=True)

    def get_slew_rate(self):
        """
            Slew rate switch looks like this:
            {'1x': {'name': '1x', 'label': 'Guide', 'value': False},
             '2x': {'name': '2x', 'label': 'Centering', 'value': False},
             '3x': {'name': '3x', 'label': 'Find', 'value': True},
             '4x': {'name': '4x', 'label': 'Max', 'value': False},
             'state': 'IDLE'}
        """
        slew_dict = self.get_switch('TELESCOPE_SLEW_RATE')
        if len(slew_dict) > 0:
            return [k for k, v in slew_dict.items() if v == True][0]
        else:
            return None

    def set_slew_rate(self, slew_rate='3x'):
        """
        Should be one of (by order of speed):
            -1x (equiv to SLEW_GUIDE)
            -2x (equiv to SLEW_CENTERING)
            -3x (equiv to SLEW_FIND)
            -4x (equiv to SLEW_MAX)
        """
        slew_dict = self.get_switch('TELESCOPE_SLEW_RATE')
        self.logger.debug(f"Setting mount slewing rate: {slew_rate} while "
                          f"dictionary is {slew_dict}")
        if slew_rate in slew_dict:
            self.set_switch('TELESCOPE_SLEW_RATE', [slew_rate], sync=True, timeout=self.timeout)
        else:
            msg = f"Trying to set mount slewing rate: {slew_rate} while "\
                  f"dictionary is {slew_dict}"
            raise RuntimeError(msg)

    def get_pier_side(self):
        ''' GEM Pier Side
            PIER_EAST Mount on the East side of pier (Pointing West).
            PIER_WEST Mount on the West side of pier (Pointing East).
        '''
        pier_side = self.get_switch('TELESCOPE_PIER_SIDE')
        return pier_side

    def get_track_mode(self):
        ''' Track mode switch looks like this
         {'TRACK_SIDEREAL':
              {'name': 'TRACK_SIDEREAL', 'label': 'Sidereal', 'value': False},
          'TRACK_CUSTOM':
              {'name': 'TRACK_CUSTOM', 'label': 'Custom', 'value': False},
          'state': 'OK'}
        '''
        track_dict = self.get_switch('TELESCOPE_TRACK_MODE')
        self.logger.debug(f"Got mount tracking rate dict: {track_dict}")
        if len(track_dict) > 0:
            return [k for k, v in track_dict.items() if v == True][0]
        else:
            return None

    # This does not work with simulator
    def set_track_mode(self, track_mode='TRACK_SIDEREAL'):
        ''' Available track mode
            TELESCOPE_TRACK_MODE Switch:
                TRACK_SIDEREAL: Track at sidereal rate.
                TRACK_SOLAR: Track at solar rate.
                TRACK_LUNAR: Track at lunar rate.
                TRACK_CUSTOM: custom
        '''
        track_dict = self.get_switch('TELESCOPE_TRACK_MODE')
        if track_mode in track_dict:
            self.set_switch('TELESCOPE_TRACK_MODE', [track_mode])
        else:
            msg = f"Trying to set mount tracking rate: {track_mode} while "\
                  f"dictionary is {track_dict}"
            raise RuntimeError(msg)

    @property
    def is_parked(self):
        status = self.get_switch('TELESCOPE_PARK')
        self.logger.debug(f'Got TELESCOPE_PARK status: {status}')
        return status['PARK']

    def get_home_position_icrs(self, longitude_deg):
        """
        A German Equatorial Mount's home position has its RA axis parallel
        to the polar axis, i.e. dec=+90 at hour angle=0 right now -- both
        epoch-of-date (JNow) concepts, resolved here since this is the one
        place JNow is allowed (see CLAUDE.md's J2000/ICRS-everywhere
        convention). Returns an ICRS SkyCoord, ready to hand to
        sync_to_coord() the same way any other J2000 target would be.
        """
        lst_deg = local_sidereal_time_deg(longitude_deg)
        fk5_now = FK5(equinox=Time.now())
        home_now = SkyCoord(ra=lst_deg * u.degree, dec=90.0 * u.degree, frame=fk5_now)
        return home_now.transform_to(ICRS())

    def get_current_coordinates(self):
        """
        Subtleties here for INDILIB: coord are retrieved as FK5 frame with Equatorial astrometric epoch
        of date coordinate (eod):  RA JNow RA, hours,  DEC JNow Dec, degrees +N

        As our software only manipulates ICRS J2000. We decided to convert FROM FK5 jnow for the generic case
        """
        rahour_decdeg = self.get_number('EQUATORIAL_EOD_COORD')
        fk5_now = FK5(equinox=Time.now())
        ret = SkyCoord(ra=rahour_decdeg['RA']*u.hourangle,
                       dec=rahour_decdeg['DEC']*u.degree,
                       frame=fk5_now,
                       obstime=Time.now())
        icrs_j2k = ICRS()
        ret = ret.transform_to(icrs_j2k)
        return ret

    def __str__(self):
        return 'Mount {}'.format(self.device_name)

    def __repr__(self):
        return self.__str__()
