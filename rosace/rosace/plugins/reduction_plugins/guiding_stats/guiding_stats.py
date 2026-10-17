"""
REDUCTION sub-plugin -- Guiding statistics
Guiding RMS and largest error, in RA and Dec, from the observation's PHD2 guide log.

Input: the guide log written by stop_phd2_guiding (shared key
guide_log_file, PHD2's GuideLog format, read by phd2/guide_log.py).
Output: reduction_data["guiding_stats"] (shared key, no casier), read by
write_spectrum (FITS keywords) and summary:

    ra_rms_arcsec, dec_rms_arcsec, total_rms_arcsec, ra_max_arcsec,
    dec_max_arcsec, ra_rms_px, dec_rms_px, total_rms_px, frames, dropped,
    duration_s, pixel_scale

Measured from the end of the last settling ("Settling complete") to the end
of the section: the frames before it include the lock position change that
puts the star on the slit, a deliberate jump of several arcseconds, not a
guiding error. The focus step's longer frame comes later and is included.
A section without a completed settling (PHD2 never settled: a failed
pointing) is not measured. The RMS of an axis is the standard deviation of
its raw error, as PHD2 shows it; the total is the root of the sum of both
squares; the largest error is the largest absolute raw error. Frames where
the star was lost (DROP) are counted, left out of the values; frames,
dropped and duration_s describe the measured part only.

Arcseconds use subplugin_params.guiding.sampling_arcsec -- the guiding
camera's plate scale measured by plate solving, the same value the pointing
steps use -- never the scale in the guide log, which PHD2 derives from the
focal length typed in its profile (2.88"/px against 2.69 measured, 7 %).

Never blocking: no guide log, an unreadable one, no completed settling or
no guided frame is a warning.
"""

from __future__ import annotations

DISPLAY_NAME = "Guidage"

import logging
from pathlib import Path

import numpy as np

from ....config import SUBPLUGIN_PARAMS
from ....models import Observation
from ....phd2.guide_log import GuideSection, parse_guide_log, settled_frame
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import find_shared_input

log = logging.getLogger("spectro.subplugin.guiding_stats")


def guiding_statistics(section: GuideSection, pixel_scale: float) -> dict | None:
    """The statistics of one guiding section after its last completed
    settling (see module docstring); None when it never settled or holds no
    guided frame after it."""
    settled = settled_frame(section)
    if settled is None:
        return None
    measured = [s for s in section.steps if s.frame > settled]
    steps = [s for s in measured if not s.dropped and s.ra_raw_px is not None and s.dec_raw_px is not None]
    if not steps:
        return None
    start = next((s.time_s for s in section.steps if s.frame == settled), 0.0)
    ra = np.array([s.ra_raw_px for s in steps])
    dec = np.array([s.dec_raw_px for s in steps])
    ra_rms, dec_rms = float(np.std(ra)), float(np.std(dec))
    total = float(np.hypot(ra_rms, dec_rms))
    return {
        "ra_rms_arcsec": round(ra_rms * pixel_scale, 2),
        "dec_rms_arcsec": round(dec_rms * pixel_scale, 2),
        "total_rms_arcsec": round(total * pixel_scale, 2),
        "ra_max_arcsec": round(float(np.max(np.abs(ra))) * pixel_scale, 2),
        "dec_max_arcsec": round(float(np.max(np.abs(dec))) * pixel_scale, 2),
        "ra_rms_px": round(ra_rms, 3),
        "dec_rms_px": round(dec_rms, 3),
        "total_rms_px": round(total, 3),
        "frames": len(measured),
        "dropped": sum(s.dropped for s in measured),
        "duration_s": round(measured[-1].time_s - start, 1),
        "pixel_scale": pixel_scale,
    }


class GuidingStatsPlugin(SubPlugin):
    """Computes the guiding RMS and largest error, in RA and Dec, from the observation's PHD2 guide log."""

    async def execute(self, obs: Observation) -> Observation:
        try:
            path = Path(obs.obs_dir or "") / find_shared_input(obs, "guide_log_file")
            sections = parse_guide_log(path)
        except Exception as e:
            log.warning(f"[GUIDING_STATS] Not measured: {e}")
            return obs
        if not sections:
            log.warning(f"[GUIDING_STATS] Not measured: no guiding section in {path.name}")
            return obs
        section = sections[-1]
        pixel_scale = SUBPLUGIN_PARAMS.get("guiding", {}).get("sampling_arcsec")
        if not pixel_scale:
            log.warning("[GUIDING_STATS] Not measured: subplugin_params.guiding.sampling_arcsec not set")
            return obs
        if settled_frame(section) is None:
            log.warning(f"[GUIDING_STATS] Not measured: PHD2 never completed its settling in {path.name}")
            return obs
        stats = guiding_statistics(section, float(pixel_scale))
        if stats is None:
            log.warning(f"[GUIDING_STATS] Not measured: no guided frame after settling in {path.name}")
            return obs

        obs.reduction_data["guiding_stats"] = stats
        log.info(
            f"[GUIDING_STATS] Guiding RMS {stats['total_rms_arcsec']:.2f}\" (RA {stats['ra_rms_arcsec']:.2f}\", "
            f"Dec {stats['dec_rms_arcsec']:.2f}\"), largest error RA {stats['ra_max_arcsec']:.2f}\" "
            f"Dec {stats['dec_max_arcsec']:.2f}\", {stats['frames']} frames ({stats['dropped']} lost) "
            f"over {stats['duration_s']:.0f} s after settling, {pixel_scale:g}\"/px"
        )
        return obs
