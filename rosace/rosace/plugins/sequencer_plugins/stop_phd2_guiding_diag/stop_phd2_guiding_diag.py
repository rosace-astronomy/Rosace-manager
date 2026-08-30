"""
SEQUENCER sub-plugin -- TEMPORARY diagnostic variant of stop_phd2_guiding,
"indi" backend only. Added 2026-08-30 while investigating why pointing_72ed's
Phase 2 (PHD2 lock-position correction) doesn't reliably land the star in the
slit -- swapped in for stop_phd2_guiding in Be_stars.yaml only, for the
duration of that investigation. Remove this whole directory and switch
Be_stars.yaml back to stop_phd2_guiding once it's resolved.

Same first action as stop_phd2_guiding (stop PHD2 capture), then captures
SHOT_COUNT successive guide images instead of one, each plate-solved with
record_error=True (feeds the same pointing_errors table pointing_72ed's own
Phase 1 solve does, for the same trend analysis) plus the WCS-based residual
vs. the slit -- meant to be compared, by eye, against the star's own visible
position in each image: automated centroiding was considered and rejected,
since the star sits in or right against the spectrograph slit, which
distorts its PSF enough that a centroid measurement there isn't trustworthy.
"""

from __future__ import annotations

DISPLAY_NAME = "Stop guiding (diag)"

import logging

from astropy.coordinates import SkyCoord

from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import build_fits_root_name, capture_guide_image
from ..plate_solve_helper import solve_wcs, world_to_pixel

log = logging.getLogger("spectro.subplugin.stop_phd2_guiding_diag")

# Number of successive guide images captured after stopping PHD2, for a
# statistical read on the final slit residual -- a diagnostic-only knob,
# not an installation setting, hence not in subplugin_params.
SHOT_COUNT = 5


class StopPhd2GuidingDiagPlugin(SubPlugin):
    """TEMPORARY: stops PHD2 guiding, then captures/solves several guide
    images in a row to statistically characterize the final slit residual."""

    async def execute(self, obs: Observation) -> Observation:
        phd2 = obs.hardware["phd2"]

        await phd2.stop_capture()
        log.info("[STOP_PHD2_GUIDING_DIAG] PHD2 guiding stopped")

        # DEBUG only: PHD2's own calibration (axis angles/rates/parity) -- if these don't
        # match the guiding camera's expected orientation, that's an axis problem inside
        # PHD2 itself, independent of the WCS-vs-PHD2 pixel-frame mismatch hypothesis.
        calibration = await phd2.get_calibration_data()
        log.debug(f"[STOP_PHD2_GUIDING_DIAG] PHD2 calibration data: {calibration}")

        target = obs.scheduler_data.get("target")
        queried_name = obs.scheduler_data.get("queried_name")
        guiding_camera = obs.hardware["guiding_camera"]
        camera_config = obs.hardware_config["guiding_camera"]
        exposure_seconds = camera_config["exposure_seconds"]
        sampling_arcsec = camera_config.get("sampling_arcsec")
        slit_x = camera_config["slit_x"]
        slit_y = camera_config["slit_y"]
        target_coord = SkyCoord(ra=target.ra, dec=target.dec, unit="deg", frame="icrs")

        root_name = build_fits_root_name("GUIDING", target_name=queried_name, exposure_time=exposure_seconds)
        shots = []
        for i in range(1, SHOT_COUNT + 1):
            image = await capture_guide_image(
                guiding_camera, exposure_seconds, obs.obs_dir,
                filename=f"{root_name}-{i}.fits",
                ra_deg=target.ra, dec_deg=target.dec, target_name=queried_name,
                image_to_gui=True,
            )
            await solve_wcs(str(image), sampling_arcsec=sampling_arcsec, record_error=True, observation_id=obs.id)
            # Absolute pixel position the WCS solution assigns to the target -- not just
            # the residual vs the slit -- so this can be compared against the star's own
            # visible position in the image (read by eye): the two disagreeing would mean
            # the astrometric solve itself mislocates the star, not just that guiding held
            # it in the wrong place.
            x, y = world_to_pixel(str(image), target_coord)
            dx, dy = x - slit_x, y - slit_y
            residual_arcsec = (dx**2 + dy**2) ** 0.5 * (sampling_arcsec or 0.0)
            log.info(
                f"[STOP_PHD2_GUIDING_DIAG] Shot {i}/{SHOT_COUNT} ({image.name}): "
                f"target at ({x:.1f}, {y:.1f})px, slit at ({slit_x:.1f}, {slit_y:.1f})px, "
                f"dx={dx:.1f}px dy={dy:.1f}px ({residual_arcsec:.1f}\")"
            )
            shots.append({
                "image": image.name, "target_x_px": x, "target_y_px": y,
                "dx_px": dx, "dy_px": dy, "residual_arcsec": residual_arcsec,
            })

        mean_residual = sum(s["residual_arcsec"] for s in shots) / len(shots)
        stdev_residual = (sum((s["residual_arcsec"] - mean_residual) ** 2 for s in shots) / len(shots)) ** 0.5
        log.info(
            f"[STOP_PHD2_GUIDING_DIAG] Final slit residual for {queried_name} over {SHOT_COUNT} shots: "
            f"mean={mean_residual:.1f}\" stdev={stdev_residual:.1f}\""
        )

        obs.sequencer_data["stop_phd2_guiding_diag"] = {
            "shots": shots,
            "mean_residual_arcsec": mean_residual,
            "stdev_residual_arcsec": stdev_residual,
        }
        return obs
