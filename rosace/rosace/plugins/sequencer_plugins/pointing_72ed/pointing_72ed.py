"""
SEQUENCER sub-plugin -- 72ED telescope pointing + PHD2 guiding, "indi" backend only.

Points in two phases, each refining the previous one's precision: first a
mount-precision pointing correction (measured by plate solving), then a
PHD2-guiding-precision refinement (by nudging PHD2's own lock position by
the measured residual) -- only the astrometric measurement is precise
enough to put the star exactly in the slit, hence this second pass.

Every mount slew happens in two steps (see _slew_with_backlash_approach
below): a preliminary slew offset by backlash_offset_deg in both RA and
DEC, then the exact slew -- always approaching the target from the same
mechanical side makes backlash reproducible from one slew to the next,
instead of varying with slew direction.
"""

from __future__ import annotations

DISPLAY_NAME = "Pointing"

import logging
import time

from astropy.coordinates import SkyCoord

from ....config import SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import capture_guide_image
from ..guiding_chain_recovery import run_with_guiding_recovery
from ..plate_solve_helper import solve_result, solve_wcs, world_to_pixel

log = logging.getLogger("spectro.subplugin.pointing_72ed")

# Phase 2's solve already has a precise position hint (corrected_coord, just
# slewed to and stamped into the image header) -- unlike Phase 1, which has
# only the raw target position to search around, so it keeps solve_wcs()'s
# own wider default (5 deg). A tighter cone here means less for solve-field
# to search, directly cutting the real wall-clock time of this synchronous
# plate-solve -- found 2026-08-30 to be the actual cause of a multi-second
# gap observed live between PHD2's guiding starting and its lock position
# actually being corrected (see HISTORY.md).
_params = SUBPLUGIN_PARAMS.get("pointing_72ed", {})
PHASE2_SEARCH_RADIUS_DEG = _params.get("phase2_search_radius_deg", 1.0)


async def _slew_with_backlash_approach(mount, coord: SkyCoord, offset_deg: float) -> None:
    """Two-step slew toward coord: first coord offset by +offset_deg in RA
    and DEC, then the exact coord."""
    await mount.slew_to(coord.ra.deg + offset_deg, coord.dec.deg + offset_deg)
    await mount.slew_to(coord.ra.deg, coord.dec.deg)


class Pointing72edPlugin(SubPlugin):
    """Points the 72ED telescope in two phases (mount correction, then PHD2
    guiding refinement) and starts guiding."""

    async def execute(self, obs: Observation) -> Observation:
        target = obs.scheduler_data.get("target")
        if not target:
            raise RuntimeError("POINTING_72ED: target missing from scheduler_data")

        if not obs.obs_dir:
            raise RuntimeError("POINTING_72ED: observation directory (obs_dir) not set")

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
        # fight the Phase 1 slews below. Guarded (see guiding_chain_recovery.py): this is the
        # first PHD2 call of every observation, so it is where a dead guiding camera shows up
        # first -- 28 of the 30 failures of the 2026-09-24 night failed right here.
        log.info(f"[POINTING_72ED] Stopping any leftover PHD2 capture before pointing to {queried_name}")
        await run_with_guiding_recovery(obs, phd2.stop_capture, "PHD2 stop_capture")

        # ── Phase 1: initial pointing + mount-precision correction ──

        # Step 1: two-step slew toward the target
        target_coord = SkyCoord(ra=target.ra, dec=target.dec, unit="deg", frame="icrs")
        log.info(f"[POINTING_72ED] Initial pointing toward {queried_name}")
        await _slew_with_backlash_approach(mount, target_coord, backlash_offset_deg)
        # DEBUG only, added 2026-08-30 while investigating a large initial pointing miss --
        # distinguishes the mount executing the GOTO inaccurately (reported position would
        # then differ from commanded) from the commanded position itself already being off
        # (reported would match commanded, but the real sky position still wouldn't).
        # Briefly promoted to INFO 2026-09-07, reverted to DEBUG the same day (user's own
        # call): useful while actively debugging, not something an observer needs every night.
        reported_ra, reported_dec = await mount.get_position()
        log.debug(
            f"[POINTING_72ED] Initial slew: commanded ({target_coord.ra.deg:.5f}, {target_coord.dec.deg:.5f}), "
            f"mount reports ({reported_ra:.5f}, {reported_dec:.5f})"
        )

        # Step 2: guide image, taken directly (not through PHD2)
        initial_guide_image = await capture_guide_image(
            obs, exposure_seconds,
            ra_deg=target_coord.ra.deg, dec_deg=target_coord.dec.deg, target_name=queried_name,
            image_to_gui=True,
        )
        log.info(f"[POINTING_72ED] Guiding image acquisition: {initial_guide_image.name}")

        # Step 3: plate solve + record the mount's pointing error, with the pier
        # side the mount reports -- best effort: a diagnostic value, never worth
        # failing the pointing over
        try:
            pier_side = await mount.get_pier_side()
        except Exception as e:
            log.warning(f"[POINTING_72ED] Could not read the pier side, recorded as unknown: {e}")
            pier_side = None
        await solve_wcs(
            str(initial_guide_image), sampling_arcsec=sampling_arcsec,
            record_error=True, observation_id=obs.id, pier_side=pier_side,
        )

        # Step 4: actual sky position at the slit
        real_position = solve_result(str(initial_guide_image), slit_x, slit_y)

        # Step 5: offset between the actual (at the slit) and the aimed-at position
        d_lon, d_lat = target_coord.spherical_offsets_to(real_position)

        # Step 6: corrected target position
        corrected_coord = target_coord.spherical_offsets_by(-d_lon, -d_lat)

        # Step 7: two-step slew toward the corrected target
        log.info(f"[POINTING_72ED] Corrected re-pointing toward {queried_name}")
        await _slew_with_backlash_approach(mount, corrected_coord, backlash_offset_deg)
        reported_ra, reported_dec = await mount.get_position()
        log.debug(
            f"[POINTING_72ED] Corrected slew: commanded ({corrected_coord.ra.deg:.5f}, {corrected_coord.dec.deg:.5f}), "
            f"mount reports ({reported_ra:.5f}, {reported_dec:.5f})"
        )

        # ── Phase 2: PHD2-guiding-precision refinement ──

        # Step 8: new guide image, taken directly (not through PHD2), at the corrected position
        corrected_guide_image = await capture_guide_image(
            obs, exposure_seconds,
            ra_deg=corrected_coord.ra.deg, dec_deg=corrected_coord.dec.deg, target_name=queried_name,
            image_to_gui=True,
        )
        log.info(f"[POINTING_72ED] Guiding image acquisition: {corrected_guide_image.name}")

        # Step 9: start PHD2 guiding (auto-selects a guide star), waits for settle.
        # Timing added 2026-09-02 while investigating the real residual: this settle
        # is on the *current* position (no new mount target commanded), so it's expected to
        # be fast -- logged to confirm that against real timings rather than assume it.
        # INFO since 2026-09-07 (user's own call): unlike the other diagnostics added the
        # same day, settle timing stays operationally useful beyond the investigation itself.
        log.info(f"[POINTING_72ED] Starting PHD2 guiding on {queried_name}")
        # Guarded, unlike the second start_guiding() below: replaying this one after a camera
        # recovery is safe (nothing depends on PHD2's previous state yet), whereas the second
        # one follows set_lock_position() -- a recovery in between would lose that offset, and
        # replaying would then guide on the wrong position without any error.
        settle_start = time.monotonic()
        await run_with_guiding_recovery(obs, phd2.start_guiding, "PHD2 start_guiding")
        log.info(f"[POINTING_72ED] PHD2 first settle took {time.monotonic() - settle_start:.1f}s")

        # Step 10: lock position PHD2 chose (its own guide star, generally not the target itself).
        # DEBUG only, added 2026-09-02: needed to later cross-check PHD2's own pixel-coordinate
        # convention against the plate-solve's (astropy, zero-indexed) -- see
        # project field-test notes for the 3-way comparison (PHD2 / Demetra / plate-solve)
        # this value feeds. Briefly promoted to INFO 2026-09-07, reverted the same day.
        lock_x, lock_y = await phd2.get_lock_position()
        log.debug(f"[POINTING_72ED] PHD2 lock position for {queried_name}: ({lock_x:.2f}, {lock_y:.2f})")

        # Step 11: plate solve this 2nd image, without recording the error this time
        # (the pointing is already corrected, mount accuracy isn't what's being measured here) --
        # a tighter search radius than Phase 1's (see PHASE2_SEARCH_RADIUS_DEG above).
        # DEBUG only, timing added 2026-09-02, same investigation: this synchronous solve is
        # the other candidate for where the real wall-clock time between capturing this image
        # and actually applying its residual to PHD2 goes (see HISTORY.md's 2026-08-30 entry).
        # Briefly promoted to INFO 2026-09-07, reverted the same day.
        solve_start = time.monotonic()
        await solve_wcs(
            str(corrected_guide_image), sampling_arcsec=sampling_arcsec, radius_deg=PHASE2_SEARCH_RADIUS_DEG,
        )
        log.debug(f"[POINTING_72ED] Phase 2 plate-solve took {time.monotonic() - solve_start:.1f}s")

        # Step 12: pixel position, in this 2nd image, of the real target star (target_coord,
        # not corrected_coord -- corrected_coord is only the artificial point we aimed the
        # mount at; using it here instead of the real star was a real bug, found 2026-08-12
        # by comparing against a live observation: it measured the residual against the aim
        # point, not the star, feeding PHD2 a spurious correction roughly the size of Phase
        # 1's own pointing-error correction) -- then residual vs. the slit
        target_pixel_x, target_pixel_y = world_to_pixel(str(corrected_guide_image), target_coord)
        dx, dy = target_pixel_x - slit_x, target_pixel_y - slit_y
        # INFO since 2026-09-07 (was DEBUG): kept permanently, not just for the current
        # investigation -- the operationally useful "before" counterpart to
        # stop_phd2_guiding's own post-science residual (the "after").
        log.info(f"[POINTING_72ED] Phase 2 residual vs slit for {queried_name}: dx={dx:.1f}px dy={dy:.1f}px")

        # Step 13: shift PHD2's lock position by the same offset as the desired field shift --
        # PHD2 will itself move the mount until its own star reaches this new position,
        # dragging the whole field (including the target) with it, onto the slit
        await phd2.set_lock_position(lock_x - dx, lock_y - dy, exact=True)

        # Step 14: wait for the new settle around the corrected lock position
        log.info(f"[POINTING_72ED] Refining PHD2 lock position on {queried_name}")
        settle_start = time.monotonic()
        await phd2.start_guiding()
        log.info(f"[POINTING_72ED] PHD2 refine settle took {time.monotonic() - settle_start:.1f}s")

        # Step 15: done -- star in the slit, guiding active. No "target" (already in
        # scheduler_data) or "guiding_started" flag (process info, not descriptive --
        # that's the log's job) -- only the two images this step actually produced.
        obs.sequencer_data["pointing_72ed"] = {
            "initial_guide_image": initial_guide_image.name,
            "corrected_guide_image": corrected_guide_image.name,
        }
        log.info(f"[POINTING_72ED] Star in the slit for {queried_name}, guiding active")
        return obs
