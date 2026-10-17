"""
SEQUENCER sub-plugin -- stops PHD2 guiding, "indi" backend only.

Must come after the science acquisition in a program's SEQUENCER steps,
not necessarily last (calibration series that need no guiding may follow
it): nothing else in the sequencer stops PHD2 once guiding has started --
without this, guiding
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

Once PHD2 has stopped, writes the observation's guiding section, rebuilt in
PHD2's GuideLog format by the PHD2 client (phd2/guide_log.py), to
"<final name>_PHD2_GuideLog.txt" in the session directory (file_names.
final_name(): the observation file's name plus the observation ID), under
the shared key sequencer_data["guide_log_file"]. Never blocking: no section
started during this observation, or a write error, is only a warning.
"""

from __future__ import annotations

DISPLAY_NAME = "Stop guiding"

import asyncio
import logging
from pathlib import Path

from astropy.coordinates import SkyCoord

from ....config import SUBPLUGIN_PARAMS
from ....file_names import final_name
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import capture_guide_image, slit_position
from ..plate_solve_helper import solve_wcs, world_to_pixel

log = logging.getLogger("spectro.subplugin.stop_phd2_guiding")

# Pause between PHD2's stop and our own guide exposure on the same camera.
# Found 2026-09-20: 3 of 11 post-science captures made right after
# stop_capture() failed twice in the INDI ZWO driver, which then reset the
# camera's USB port (all 3 events, nothing similar elsewhere) -- most likely
# PHD2's last in-flight exposure still using the camera. PHD2's own guide
# cycle is ~3.5s; 5s leaves a margin. An experiment: once a few nights show
# no more resets, try shortening it (see TODO.md).
SETTLE_AFTER_STOP_SECONDS = 5.0


class StopPhd2GuidingPlugin(SubPlugin):
    """Stops PHD2 guiding/looping at the end of an observation's SEQUENCER
    steps, then captures and plate-solves one post-science guide image to
    log the final residual vs. the slit."""

    async def execute(self, obs: Observation) -> Observation:
        phd2 = obs.hardware["phd2"]

        await phd2.stop_capture()
        log.info("[STOP_PHD2_GUIDING] PHD2 guiding stopped")
        try:
            self._write_guide_log(obs, phd2)
        except Exception as e:
            log.warning(f"[STOP_PHD2_GUIDING] Guide log not written ({type(e).__name__}: {e})")

        await asyncio.sleep(SETTLE_AFTER_STOP_SECONDS)
        log.info(
            f"[STOP_PHD2_GUIDING] Waited {SETTLE_AFTER_STOP_SECONDS:.1f}s after PHD2 stop, "
            f"taking the post-science guide image"
        )

        # The post-science check is a luxury: science is already on disk by now,
        # so a camera/plate-solve failure here must not fail the observation
        # (found 2026-09-20: 4 fully acquired observations were lost that way).
        try:
            await self._check_final_pointing(obs)
        except Exception as e:
            log.warning(
                f"[STOP_PHD2_GUIDING] Post-science pointing check failed, observation "
                f"continues ({type(e).__name__}: {e})"
            )
        return obs

    @staticmethod
    def _write_guide_log(obs: Observation, phd2) -> None:
        section = phd2.guide_log_section()
        # Received before the observation began: a previous observation's
        # section (the receipt time, not PHD2's: the demo replays recorded times)
        if section is None or (section.received_at or section.started_at) < obs.created_at:
            log.warning("[STOP_PHD2_GUIDING] Guide log not written: no guiding section started during this observation")
            return
        name = f"{final_name(obs)}_PHD2_GuideLog.txt"
        (Path(obs.obs_dir or "") / name).write_text(section.text, encoding="ascii", errors="replace")
        obs.sequencer_data["guide_log_file"] = name
        log.info(f"[STOP_PHD2_GUIDING] Guide log written: {name}")

    async def _check_final_pointing(self, obs: Observation) -> None:
        target = obs.scheduler_data.get("target")
        queried_name = obs.scheduler_data.get("queried_name")
        guiding_params = SUBPLUGIN_PARAMS.get("guiding", {})
        exposure_seconds = guiding_params["exposure_seconds"]
        sampling_arcsec = guiding_params.get("sampling_arcsec")
        # The slit in force for this observation: slit_xy's calculated position, or the default
        slit_x, slit_y = slit_position(obs)
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
            f"target at ({x:.1f}, {y:.1f})px, slit at ({slit_x:.2f}, {slit_y:.2f})px, "
            f"dx={dx:.1f}px dy={dy:.1f}px ({residual_arcsec:.1f}\")"
        )

        obs.sequencer_data["stop_phd2_guiding"] = {
            "guide_image": image.name, "residual_arcsec": residual_arcsec,
        }
