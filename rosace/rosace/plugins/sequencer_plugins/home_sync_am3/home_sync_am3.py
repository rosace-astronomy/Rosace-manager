"""
SEQUENCER sub-plugin -- ZWO AM3 mount home/park recovery + measured sync,
"indi" backend only.

Re-parks then unparks the mount to recover a reliable mechanical position
reference, then syncs it to a *measured* (plate-solved) position rather
than a theoretically-computed one -- closing the gap between the mount's
mechanical zero and the real sky without ever trusting an unverified value
(the still-valid half of the 2026-08-26 finding, see HISTORY.md: never
sync to a value that hasn't been verified against the real sky).

Field-tested 2026-09-11 (see HISTORY.md / project memory): a computed sync
(RA=local sidereal time, DEC=+90) left a consistent ~25' residual on the
real AM3 -- confirming it merely reconfirms what unpark() already derives,
without correcting anything -- while a measured sync (slew to a
calibration point, plate-solve, sync to the solved position) brought the
residual down to 1-3'. This sub-plugin replaces the computed-sync approach
with the measured one.

A single plate-solve attempt can fail (a stuck slew, a cloud, too few
stars -- see the 2026-09-11 field test), so each homing attempt points at
a *different*, randomly chosen sky position rather than retrying the exact
same one, up to MAX_HOMING_ATTEMPTS times:

- Fixed altitude (HOMING_ALTITUDE_DEG, 65 deg -- 25 deg from zenith: high
  enough to keep atmospheric refraction small, low enough to stay well
  clear of the zenith's own azimuth-singularity/tracking-rate quirks).
- Random azimuth in [HOMING_AZIMUTH_MIN_DEG, HOMING_AZIMUTH_MAX_DEG] (5 to
  175 deg -- the whole eastern half of the sky, never closer than 5 deg to
  either meridian crossing (az=0/180)) -- keeps every attempt on the same
  side of the pier, avoiding a meridian flip mid-homing, per the user's own
  2026-09-12 request.

Alt/Az -> RA/Dec uses astro_math.py's own pure-trigonometry conversion
(equatorial_from_horizontal_deg), not AstroPy's AltAz transform -- same
reasoning as the scheduler's altitude check (see astro_math.py's own
docstring): no IERS/network dependency, plenty accurate for this purpose.

Every slew here already goes through IndiMount's own slew wait (see
hardware_indi/devices/mount.py's _wait_for_slew())
-- the 2026-09-11 field-test incident that motivated *this* retry loop was
itself caused by a slew silently returning before the mount had actually
gotten there, so the two fixes are complementary: the verification catches
a slew that lied about completing, the retry loop here absorbs a
plate-solve that still fails for some other reason (clouds, too few
stars...) even on a slew that genuinely completed.

Runs once per session, before the first real pointing (placed before
slew_72ed in the program YAML, via the active hardware profile's own
sequencer_prefix) -- not before every observation, since re-homing is
comparatively slow and the mount is expected to hold its position for the
rest of a session that stays connected throughout. Needs
guiding_dark_acquisition to have already run (capture_guide_image()'s own
dark-subtraction requires it) -- hence sequencer_prefix ordering it
[guiding_dark_acquisition, home_sync_am3], guiding_dark_acquisition first.

Tracked via a module-level "last session_id already homed" value: cheap,
in-process only (lost on a server restart -- same as every other in-memory
session state, e.g. main.py's own `state` singleton -- but *not* lost
across a plain stop/restart of the session within the same server
process, since sub-plugin modules are imported once via
importlib.import_module() and then reused from sys.modules, never
re-executed). Reset implicitly whenever session_id changes, rather than
through any explicit call from INIT_SESSION, so INIT_SESSION never needs
to know this sub-plugin -- or the AM3 -- exists.

Casier: unlike the computed-sync version this replaces, a measured sync
genuinely produces descriptive data worth recording (which method/how many
attempts/what position) -- see HomeSyncAm3Plugin.execute()'s own casier.
Only ever set on the one observation where homing actually ran (every
later observation in the same session returns early, before reaching it).

A homing attempt failing is caught and logged, never left to propagate:
running out of attempts falls back to park/unpark only, exactly as if
SYNC_HOME_VIA_PLATE_SOLVE were False -- this step must never turn into a
session-blocking failure over a homing sync it couldn't get.
SYNC_HOME_VIA_PLATE_SOLVE below is a one-line kill switch for the sync
attempts themselves (park/unpark always still happen): flip to False and
restart the server if the measured-sync approach itself ever turns out to
make pointing worse rather than better in the field.
"""

from __future__ import annotations

DISPLAY_NAME = "Home"

import logging
import random

from ....astro_math import equatorial_from_horizontal_deg
from ....config import SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG, SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import capture_guide_image
from ..plate_solve_helper import PlateSolveError, solve_result, solve_wcs

log = logging.getLogger("spectro.subplugin.home_sync_am3")

_last_homed_session_id: str | None = None

# One-line kill switch -- see module docstring.
SYNC_HOME_VIA_PLATE_SOLVE = True

HOMING_ALTITUDE_DEG = 65.0
HOMING_AZIMUTH_MIN_DEG = 5.0
HOMING_AZIMUTH_MAX_DEG = 175.0
MAX_HOMING_ATTEMPTS = 7


class HomeSyncAm3Plugin(SubPlugin):
    """Once per session, before the first real pointing: park the AM3 mount
    to its Home reference, unpark it, then (unless disabled, see
    SYNC_HOME_VIA_PLATE_SOLVE) attempt a measured sync -- slewing to up to
    MAX_HOMING_ATTEMPTS random points at a fixed altitude, plate-solving
    each, and syncing to the first one that solves."""

    async def execute(self, obs: Observation) -> Observation:
        global _last_homed_session_id
        if obs.session_id == _last_homed_session_id:
            return obs

        mount = obs.hardware["mount"]
        await mount.park()
        await mount.unpark()

        if SYNC_HOME_VIA_PLATE_SOLVE:
            if not obs.obs_dir:
                raise RuntimeError("HOME_SYNC_AM3: observation directory (obs_dir) not set")
            await self._sync_via_plate_solve(obs, mount)
        else:
            log.info(f"[HOME_SYNC_AM3] Mount re-homed (sync disabled) for session {obs.session_id}")

        _last_homed_session_id = obs.session_id
        return obs

    async def _sync_via_plate_solve(self, obs: Observation, mount) -> None:
        guiding_params = SUBPLUGIN_PARAMS.get("guiding", {})
        exposure_seconds = guiding_params["exposure_seconds"]
        sampling_arcsec = guiding_params.get("sampling_arcsec")

        for attempt in range(1, MAX_HOMING_ATTEMPTS + 1):
            if obs.stop_check and obs.stop_check():
                log.info(f"[HOME_SYNC_AM3] Stop requested -- interrupting after {attempt - 1} attempt(s)")
                return

            azimuth_deg = random.uniform(HOMING_AZIMUTH_MIN_DEG, HOMING_AZIMUTH_MAX_DEG)
            ra_deg, dec_deg = equatorial_from_horizontal_deg(
                HOMING_ALTITUDE_DEG, azimuth_deg, SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG,
            )

            try:
                await mount.slew_to(ra_deg, dec_deg)
                guide_image = await capture_guide_image(
                    obs, exposure_seconds,
                    ra_deg=ra_deg, dec_deg=dec_deg, target_name="HOMESYNC",
                    image_to_gui=True,
                )
                await solve_wcs(str(guide_image), sampling_arcsec=sampling_arcsec)
                measured = solve_result(str(guide_image))
            except PlateSolveError as e:
                log.warning(
                    f"[HOME_SYNC_AM3] Homing attempt {attempt}/{MAX_HOMING_ATTEMPTS} plate-solve failed "
                    f"(alt={HOMING_ALTITUDE_DEG:.1f}, az={azimuth_deg:.1f}): {e}"
                )
                continue
            except Exception as e:
                log.warning(
                    f"[HOME_SYNC_AM3] Homing attempt {attempt}/{MAX_HOMING_ATTEMPTS} slew/capture failed "
                    f"(alt={HOMING_ALTITUDE_DEG:.1f}, az={azimuth_deg:.1f}): {e}"
                )
                continue

            await mount.sync_to_coordinates(measured.ra.deg, measured.dec.deg)
            log.info(
                f"[HOME_SYNC_AM3] Mount synced to measured position RA={measured.ra.deg:.4f} "
                f"DEC={measured.dec.deg:.4f} after {attempt} attempt(s) for session {obs.session_id}"
            )
            obs.sequencer_data["home_sync_am3"] = {
                "method": "measured_sync",
                "attempts": attempt,
                "guide_image": guide_image.name,
                "synced_ra": measured.ra.deg,
                "synced_dec": measured.dec.deg,
            }
            return

        log.warning(
            f"[HOME_SYNC_AM3] Giving up after {MAX_HOMING_ATTEMPTS} failed homing attempts -- "
            f"continuing with park/unpark only for session {obs.session_id}"
        )
        obs.sequencer_data["home_sync_am3"] = {
            "method": "park_unpark_only",
            "attempts": MAX_HOMING_ATTEMPTS,
            "guide_image": None,
            "synced_ra": None,
            "synced_dec": None,
        }
