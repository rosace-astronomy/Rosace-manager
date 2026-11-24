"""
SEQUENCER sub-plugin -- 72ED PHD2-guiding-precision pointing onto the slit, "indi" backend only.

Last of the three pointing steps of a 72ED program (slew_72ed, slit_xy,
pointing_72ed): slew_72ed has already put the mount on the plate-solve
corrected position, slit_xy has (possibly) measured where the slit really
is. This step starts PHD2 guiding, measures by plate solving where the
target star falls relative to the slit in force (acquisition_helpers.py::
slit_position() -- slit_xy's calculated position, or the configured default),
and nudges PHD2's own lock position by that residual -- PHD2 then drags the
whole field onto the slit with sub-pixel guiding precision, which the
mount's own slew precision can't reach.
"""

from __future__ import annotations

DISPLAY_NAME = "Pointing"
TIME_CATEGORY = "pointing"

import logging
import time

from astropy.coordinates import SkyCoord

from ....config import SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import capture_guide_image, slit_position
from ..guiding_chain_recovery import run_with_guiding_recovery
from ..plate_solve_helper import solve_wcs, world_to_pixel

log = logging.getLogger("spectro.subplugin.pointing_72ed")

# This solve already has a precise position hint (corrected_coord, just
# slewed to by slew_72ed and stamped into the image header) -- unlike
# slew_72ed's own solve, which has only the raw target position to search
# around, so it keeps solve_wcs()'s own wider default (5 deg). A tighter cone
# here means less for solve-field to search, directly cutting the real
# wall-clock time of this synchronous plate-solve -- found 2026-08-30 to be
# the actual cause of a multi-second gap observed live between PHD2's guiding
# starting and its lock position actually being corrected (see HISTORY.md).
_params = SUBPLUGIN_PARAMS.get("pointing_72ed", {})
PHASE2_SEARCH_RADIUS_DEG = _params.get("phase2_search_radius_deg", 1.0)


class Pointing72edPlugin(SubPlugin):
    """Starts PHD2 guiding and nudges its lock position so the target star
    lands on the slit in force."""

    async def execute(self, obs: Observation) -> Observation:
        target = obs.scheduler_data.get("target")
        if not target:
            raise RuntimeError("POINTING_72ED: target missing from scheduler_data")

        if not obs.obs_dir:
            raise RuntimeError("POINTING_72ED: observation directory (obs_dir) not set")

        slew_data = obs.sequencer_data.get("slew_72ed")
        if not slew_data or "corrected_ra_deg" not in slew_data:
            raise RuntimeError(
                "POINTING_72ED: sequencer_data['slew_72ed'] corrected position missing -- "
                "slew_72ed must run before pointing_72ed"
            )

        queried_name = obs.scheduler_data.get("queried_name")
        phd2 = obs.hardware["phd2"]

        guiding_params = SUBPLUGIN_PARAMS.get("guiding", {})
        exposure_seconds = guiding_params["exposure_seconds"]
        sampling_arcsec = guiding_params.get("sampling_arcsec")
        slit_x, slit_y = slit_position(obs)
        slit_origin = obs.sequencer_data.get("slit_xy", {}).get("status", "default")

        target_coord = SkyCoord(ra=target.ra, dec=target.dec, unit="deg", frame="icrs")
        corrected_coord = SkyCoord(
            ra=slew_data["corrected_ra_deg"], dec=slew_data["corrected_dec_deg"], unit="deg", frame="icrs"
        )

        # Step 1: guide image, taken directly (not through PHD2), at the corrected position
        guide_image = await capture_guide_image(
            obs, exposure_seconds,
            ra_deg=corrected_coord.ra.deg, dec_deg=corrected_coord.dec.deg, target_name=queried_name,
            image_to_gui=True,
        )
        log.info(f"[POINTING_72ED] Guiding image acquisition: {guide_image.name}")

        # Step 2: start PHD2 guiding (auto-selects a guide star), waits for settle.
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
        first_settle_s = time.monotonic() - settle_start
        log.info(f"[POINTING_72ED] PHD2 first settle took {first_settle_s:.1f}s")

        # Step 3: lock position PHD2 chose (its own guide star, generally not the target itself).
        # DEBUG only, added 2026-09-02: needed to later cross-check PHD2's own pixel-coordinate
        # convention against the plate-solve's (astropy, zero-indexed) -- see
        # project field-test notes for the 3-way comparison (PHD2 / Demetra / plate-solve)
        # this value feeds. Briefly promoted to INFO 2026-09-07, reverted the same day.
        lock_x, lock_y = await phd2.get_lock_position()
        log.debug(f"[POINTING_72ED] PHD2 lock position for {queried_name}: ({lock_x:.2f}, {lock_y:.2f})")

        # Step 4: plate solve this image, without recording the error
        # (the pointing is already corrected, mount accuracy isn't what's being measured here) --
        # a tighter search radius than slew_72ed's (see PHASE2_SEARCH_RADIUS_DEG above).
        # DEBUG only, timing added 2026-09-02, same investigation: this synchronous solve is
        # the other candidate for where the real wall-clock time between capturing this image
        # and actually applying its residual to PHD2 goes (see HISTORY.md's 2026-08-30 entry).
        # Briefly promoted to INFO 2026-09-07, reverted the same day.
        solve_start = time.monotonic()
        await solve_wcs(
            str(guide_image), sampling_arcsec=sampling_arcsec, radius_deg=PHASE2_SEARCH_RADIUS_DEG,
        )
        log.debug(f"[POINTING_72ED] Phase 2 plate-solve took {time.monotonic() - solve_start:.1f}s")

        # Step 5: pixel position, in this image, of the real target star (target_coord,
        # not corrected_coord -- corrected_coord is only the artificial point we aimed the
        # mount at; using it here instead of the real star was a real bug, found 2026-08-12
        # by comparing against a live observation: it measured the residual against the aim
        # point, not the star, feeding PHD2 a spurious correction roughly the size of
        # slew_72ed's own pointing-error correction) -- then residual vs. the slit in force
        target_pixel_x, target_pixel_y = world_to_pixel(str(guide_image), target_coord)
        dx, dy = target_pixel_x - slit_x, target_pixel_y - slit_y
        # INFO since 2026-09-07 (was DEBUG): kept permanently, not just for the current
        # investigation -- the operationally useful "before" counterpart to
        # stop_phd2_guiding's own post-science residual (the "after").
        log.info(
            f"[POINTING_72ED] Phase 2 residual vs slit ({slit_x:.2f}, {slit_y:.2f}, {slit_origin}) "
            f"for {queried_name}: dx={dx:.1f}px dy={dy:.1f}px"
        )

        # Step 6: shift PHD2's lock position by the same offset as the desired field shift --
        # PHD2 will itself move the mount until its own star reaches this new position,
        # dragging the whole field (including the target) with it, onto the slit
        await phd2.set_lock_position(lock_x - dx, lock_y - dy, exact=True)

        # Step 7: wait for the new settle around the corrected lock position
        log.info(f"[POINTING_72ED] Refining PHD2 lock position on {queried_name}")
        settle_start = time.monotonic()
        await phd2.start_guiding()
        refine_settle_s = time.monotonic() - settle_start
        log.info(f"[POINTING_72ED] PHD2 refine settle took {refine_settle_s:.1f}s")
        # PHD2 settling time of this step, for the night's time budget: the rest of
        # the step's duration (recorded by the orchestrator) is fine pointing.
        obs.measurements["guiding_settle_s"] = round(first_settle_s + refine_settle_s, 1)

        # Step 8: done -- star in the slit, guiding active. Only the image this step
        # actually produced (the target is already in scheduler_data, the slit in
        # force in slit_xy's own casier).
        obs.sequencer_data["pointing_72ed"] = {"guide_image": guide_image.name}
        log.info(f"[POINTING_72ED] Star in the slit for {queried_name}, guiding active")
        return obs
