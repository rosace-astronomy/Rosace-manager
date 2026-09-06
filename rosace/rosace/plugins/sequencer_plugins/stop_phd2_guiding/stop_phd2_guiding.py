"""
SEQUENCER sub-plugin -- stops PHD2 guiding, "indi" backend only.

Meant to always be the last SEQUENCER step in a program: nothing else in
the sequencer stops PHD2 once guiding has started -- without this, guiding
keeps running past the whole observation and into the next one, where it
can fight that next observation's own mount slews.

Also captures and plate-solves one post-science guide image, to log where
the target actually ended up relative to the slit at the end of the
observation -- deliberately never with record_error=True: the
pointing_errors table only ever records the mount's own *initial* pointing
error, measured right after its first slew of the night, meant for
long-term polar-alignment drift detection; a plate-solve-corrected
post-science position would only pollute that signal, not extend it.

Because of that capture, this step is no longer usable as a bare, no-risk
"stop guiding" drop-in for any program: it needs subplugin_params.guiding
(exposure_seconds/sampling_arcsec/slit_x/slit_y) configured, and it needs
guiding_dark_acquisition to have already run earlier in the same session
(capture_guide_image()'s own default dark-subtraction raises RuntimeError
otherwise). Any program declaring this step must also declare
guiding_dark_acquisition -- already true of every program that starts PHD2
guiding in the first place, since that's the same requirement its own
guide-image captures already have.
"""

from __future__ import annotations

DISPLAY_NAME = "Stop guiding"

import logging

from astropy.coordinates import SkyCoord

from ....config import SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import capture_guide_image
from ..plate_solve_helper import solve_wcs, world_to_pixel

log = logging.getLogger("spectro.subplugin.stop_phd2_guiding")


class StopPhd2GuidingPlugin(SubPlugin):
    """Stops PHD2 guiding/looping at the end of an observation's SEQUENCER
    steps, then captures and plate-solves one post-science guide image to
    log the final residual vs. the slit."""

    async def execute(self, obs: Observation) -> Observation:
        phd2 = obs.hardware["phd2"]

        await phd2.stop_capture()
        log.info("[STOP_PHD2_GUIDING] PHD2 guiding stopped")

        target = obs.scheduler_data.get("target")
        queried_name = obs.scheduler_data.get("queried_name")
        guiding_params = SUBPLUGIN_PARAMS.get("guiding", {})
        exposure_seconds = guiding_params["exposure_seconds"]
        sampling_arcsec = guiding_params.get("sampling_arcsec")
        slit_x = guiding_params["slit_x"]
        slit_y = guiding_params["slit_y"]
        target_coord = SkyCoord(ra=target.ra, dec=target.dec, unit="deg", frame="icrs")

        image = await capture_guide_image(
            obs, exposure_seconds,
            ra_deg=target.ra, dec_deg=target.dec, target_name=queried_name,
            image_to_gui=True,
        )
        await solve_wcs(str(image), sampling_arcsec=sampling_arcsec)
        x, y = world_to_pixel(str(image), target_coord)
        dx, dy = x - slit_x, y - slit_y
        residual_arcsec = (dx**2 + dy**2) ** 0.5 * (sampling_arcsec or 0.0)
        log.info(
            f"[STOP_PHD2_GUIDING] Post-science residual for {queried_name} ({image.name}): "
            f"target at ({x:.1f}, {y:.1f})px, slit at ({slit_x:.1f}, {slit_y:.1f})px, "
            f"dx={dx:.1f}px dy={dy:.1f}px ({residual_arcsec:.1f}\")"
        )

        obs.sequencer_data["stop_phd2_guiding"] = {
            "guide_image": image.name, "residual_arcsec": residual_arcsec,
        }
        return obs
