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

import logging

from astropy.coordinates import SkyCoord

from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import capture_guide_image
from ..plate_solve_helper import solve_result, solve_wcs, world_to_pixel

log = logging.getLogger("spectro.subplugin.pointing_72ed")


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
        guiding_camera = obs.hardware["guiding_camera"]
        phd2 = obs.hardware["phd2"]

        mount_config = obs.hardware_config["mount"]
        camera_config = obs.hardware_config["guiding_camera"]
        backlash_offset_deg = mount_config["backlash_offset_deg"]
        exposure_seconds = camera_config["exposure_seconds"]
        sampling_arcsec = camera_config.get("sampling_arcsec")
        slit_x = camera_config["slit_x"]
        slit_y = camera_config["slit_y"]

        # Step 0: stop any leftover PHD2 guiding/looping from the previous observation
        # (nothing else stops it explicitly) -- otherwise its own corrections would
        # fight the Phase 1 slews below.
        log.info(f"[POINTING_72ED] Stopping any leftover PHD2 capture before pointing to {queried_name}")
        await phd2.stop_capture()

        # ── Phase 1: initial pointing + mount-precision correction ──

        # Step 1: two-step slew toward the target
        target_coord = SkyCoord(ra=target.ra, dec=target.dec, unit="deg", frame="icrs")
        log.info(f"[POINTING_72ED] Initial pointing toward {queried_name}")
        await _slew_with_backlash_approach(mount, target_coord, backlash_offset_deg)

        # Step 2: guide image, taken directly (not through PHD2)
        initial_guide_image = await capture_guide_image(
            guiding_camera, exposure_seconds, obs.obs_dir,
            ra_deg=target_coord.ra.deg, dec_deg=target_coord.dec.deg, target_name=queried_name,
        )
        log.info(f"[POINTING_72ED] Guiding image acquisition: {initial_guide_image.name}")

        # Step 3: plate solve + record the mount's pointing error
        await solve_wcs(
            str(initial_guide_image), sampling_arcsec=sampling_arcsec,
            record_error=True, observation_id=obs.id,
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

        # ── Phase 2: PHD2-guiding-precision refinement ──

        # Step 8: new guide image, taken directly (not through PHD2), at the corrected position
        corrected_guide_image = await capture_guide_image(
            guiding_camera, exposure_seconds, obs.obs_dir,
            ra_deg=corrected_coord.ra.deg, dec_deg=corrected_coord.dec.deg, target_name=queried_name,
        )
        log.info(f"[POINTING_72ED] Guiding image acquisition: {corrected_guide_image.name}")

        # Step 9: start PHD2 guiding (auto-selects a guide star), waits for settle
        log.info(f"[POINTING_72ED] Starting PHD2 guiding on {queried_name}")
        await phd2.start_guiding()

        # Step 10: lock position PHD2 chose (its own guide star, generally not the target itself)
        lock_x, lock_y = await phd2.get_lock_position()

        # Step 11: plate solve this 2nd image, without recording the error this time
        # (the pointing is already corrected, mount accuracy isn't what's being measured here)
        await solve_wcs(str(corrected_guide_image), sampling_arcsec=sampling_arcsec)

        # Step 12: pixel position, in this 2nd image, of the real target star (target_coord,
        # not corrected_coord -- corrected_coord is only the artificial point we aimed the
        # mount at; using it here instead of the real star was a real bug, found 2026-08-12
        # by comparing against a live observation: it measured the residual against the aim
        # point, not the star, feeding PHD2 a spurious correction roughly the size of Phase
        # 1's own pointing-error correction) -- then residual vs. the slit
        target_pixel_x, target_pixel_y = world_to_pixel(str(corrected_guide_image), target_coord)
        dx, dy = target_pixel_x - slit_x, target_pixel_y - slit_y

        # Step 13: shift PHD2's lock position by the same offset as the desired field shift --
        # PHD2 will itself move the mount until its own star reaches this new position,
        # dragging the whole field (including the target) with it, onto the slit
        await phd2.set_lock_position(lock_x - dx, lock_y - dy, exact=True)

        # Step 14: wait for the new settle around the corrected lock position
        log.info(f"[POINTING_72ED] Refining PHD2 lock position on {queried_name}")
        await phd2.start_guiding()

        # Step 15: done -- star in the slit, guiding active. No "target" (already in
        # scheduler_data) or "guiding_started" flag (process info, not descriptive --
        # that's the log's job) -- only the two images this step actually produced.
        obs.sequencer_data["pointing_72ed"] = {
            "initial_guide_image": initial_guide_image.name,
            "corrected_guide_image": corrected_guide_image.name,
        }
        log.info(f"[POINTING_72ED] Star in the slit for {queried_name}, guiding active")
        return obs
