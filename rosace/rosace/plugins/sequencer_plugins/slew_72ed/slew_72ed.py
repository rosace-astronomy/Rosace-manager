"""
SEQUENCER sub-plugin -- 72ED telescope slew with mount-precision correction, "indi" backend only.

First of the three pointing steps of a 72ED program (slew_72ed, slit_xy,
pointing_72ed): slews to the target, measures the mount's pointing error by
plate solving a guide image (recorded in the pointing_errors table), then
slews to the corrected position. After this step the mount no longer moves
on its own -- only PHD2 guiding nudges it afterwards -- so slit_xy can
measure the slit with the telescope in its final position, and
pointing_72ed then puts the star exactly on that slit with PHD2.

Every mount slew happens in two steps (see _slew_with_backlash_approach
below): a preliminary slew offset by backlash_offset_deg in both RA and
DEC, then the exact slew -- always approaching the target from the same
mechanical side makes backlash reproducible from one slew to the next,
instead of varying with slew direction.

Uses the default slit position (subplugin_params.guiding.slit_x/slit_y),
never slit_xy's measurement (which doesn't exist yet at this point): this
correction is only accurate to about an arcminute anyway, and
pointing_72ed corrects the rest against the slit actually in force.
"""

from __future__ import annotations

DISPLAY_NAME = "Slew"

import logging

from astropy.coordinates import SkyCoord

from ....config import SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import capture_guide_image
from ..guiding_chain_recovery import run_with_guiding_recovery
from ..plate_solve_helper import solve_result, solve_wcs

log = logging.getLogger("spectro.subplugin.slew_72ed")


async def _slew_with_backlash_approach(mount, coord: SkyCoord, offset_deg: float) -> None:
    """Two-step slew toward coord: first coord offset by +offset_deg in RA
    and DEC, then the exact coord."""
    await mount.slew_to(coord.ra.deg + offset_deg, coord.dec.deg + offset_deg)
    await mount.slew_to(coord.ra.deg, coord.dec.deg)


class Slew72edPlugin(SubPlugin):
    """Slews the 72ED telescope to the target, then to the position corrected
    by a plate-solved pointing-error measurement."""

    async def execute(self, obs: Observation) -> Observation:
        target = obs.scheduler_data.get("target")
        if not target:
            raise RuntimeError("SLEW_72ED: target missing from scheduler_data")

        if not obs.obs_dir:
            raise RuntimeError("SLEW_72ED: observation directory (obs_dir) not set")

        queried_name = obs.scheduler_data.get("queried_name")
        mount = obs.hardware["mount"]
        phd2 = obs.hardware["phd2"]

        mount_config = obs.hardware_config["mount"]
        guiding_params = SUBPLUGIN_PARAMS.get("guiding", {})
        backlash_offset_deg = mount_config["backlash_offset_deg"]
        exposure_seconds = guiding_params["exposure_seconds"]
        sampling_arcsec = guiding_params.get("sampling_arcsec")
        slit_x = guiding_params["slit_x"]
        slit_y = guiding_params["slit_y"]

        # Step 0: stop any leftover PHD2 guiding/looping from the previous observation
        # (nothing else stops it explicitly) -- otherwise its own corrections would
        # fight the slews below. Guarded (see guiding_chain_recovery.py): this is the
        # first PHD2 call of every observation, so it is where a dead guiding camera shows up
        # first -- 28 of the 30 failures of the 2026-09-24 night failed right here.
        log.info(f"[SLEW_72ED] Stopping any leftover PHD2 capture before pointing to {queried_name}")
        await run_with_guiding_recovery(obs, phd2.stop_capture, "PHD2 stop_capture")

        # Step 1: two-step slew toward the target
        target_coord = SkyCoord(ra=target.ra, dec=target.dec, unit="deg", frame="icrs")
        log.info(f"[SLEW_72ED] Initial pointing toward {queried_name}")
        await _slew_with_backlash_approach(mount, target_coord, backlash_offset_deg)
        # DEBUG only, added 2026-08-30 while investigating a large initial pointing miss --
        # distinguishes the mount executing the GOTO inaccurately (reported position would
        # then differ from commanded) from the commanded position itself already being off
        # (reported would match commanded, but the real sky position still wouldn't).
        # Briefly promoted to INFO 2026-09-07, reverted to DEBUG the same day (user's own
        # call): useful while actively debugging, not something an observer needs every night.
        reported_ra, reported_dec = await mount.get_position()
        log.debug(
            f"[SLEW_72ED] Initial slew: commanded ({target_coord.ra.deg:.5f}, {target_coord.dec.deg:.5f}), "
            f"mount reports ({reported_ra:.5f}, {reported_dec:.5f})"
        )

        # Step 2: guide image, taken directly (not through PHD2)
        guide_image = await capture_guide_image(
            obs, exposure_seconds,
            ra_deg=target_coord.ra.deg, dec_deg=target_coord.dec.deg, target_name=queried_name,
            image_to_gui=True,
        )
        log.info(f"[SLEW_72ED] Guiding image acquisition: {guide_image.name}")

        # Step 3: plate solve + record the mount's pointing error, with the pier
        # side the mount reports -- best effort: a diagnostic value, never worth
        # failing the pointing over
        try:
            pier_side = await mount.get_pier_side()
        except Exception as e:
            log.warning(f"[SLEW_72ED] Could not read the pier side, recorded as unknown: {e}")
            pier_side = None
        await solve_wcs(
            str(guide_image), sampling_arcsec=sampling_arcsec,
            record_error=True, observation_id=obs.id, pier_side=pier_side,
        )

        # Step 4: actual sky position at the slit
        real_position = solve_result(str(guide_image), slit_x, slit_y)

        # Step 5: offset between the actual (at the slit) and the aimed-at position
        d_lon, d_lat = target_coord.spherical_offsets_to(real_position)

        # Step 6: corrected target position
        corrected_coord = target_coord.spherical_offsets_by(-d_lon, -d_lat)

        # Step 7: two-step slew toward the corrected target
        log.info(f"[SLEW_72ED] Corrected re-pointing toward {queried_name}")
        await _slew_with_backlash_approach(mount, corrected_coord, backlash_offset_deg)
        reported_ra, reported_dec = await mount.get_position()
        log.debug(
            f"[SLEW_72ED] Corrected slew: commanded ({corrected_coord.ra.deg:.5f}, {corrected_coord.dec.deg:.5f}), "
            f"mount reports ({reported_ra:.5f}, {reported_dec:.5f})"
        )

        # The corrected position is what the mount is aimed at from now on --
        # pointing_72ed reads it to stamp its own guide image and to narrow its
        # plate-solve search around it.
        obs.sequencer_data["slew_72ed"] = {
            "guide_image": guide_image.name,
            "corrected_ra_deg": corrected_coord.ra.deg,
            "corrected_dec_deg": corrected_coord.dec.deg,
        }
        log.info(f"[SLEW_72ED] Mount on target for {queried_name}")
        return obs
