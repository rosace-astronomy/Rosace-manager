"""
Sub-plugin SEQUENCER -- Guiding-camera dark acquisition, with current-session
reuse. Backend "indi" only -- the guiding camera is uncooled
(the 72ED hardware profile's guiding_camera.cooling: FALSE), so unlike
dark_acquisition (science camera, temperature-bucketed library), this is a
plain once-per-session series with no staleness check at all, same
reasoning as flat_acquisition: shoot GUIDING_DARK_COUNT raw frames with the
spectroscope on DARK, median-combine them into one file, and reuse that
median for the rest of the session.

Found 2026-09-05: the guiding camera has several fixed hot/defective
pixels, present in every exposure regardless of where the telescope is
pointed -- confirmed by comparing the same pixel coordinates across
unrelated fields the same night. These land among the brightest "sources"
solve-field's extractor ranks first, and were the actual cause of most of
that night's plate-solve timeouts (8 of 11 failures would have solved once
these were subtracted). A per-pixel median across several *real* guiding
exposures at different pointings cancels real stars (which move between
pointings) while keeping the fixed defects (which don't) -- exactly the
"master dark" this sub-plugin builds once per session.

Consumed by acquisition_helpers.py::capture_guide_image(), which reads
this sub-plugin's own casier (sequencer_data["guiding_dark_acquisition"]
["median_dark_file"]) to dark-correct every guide image it writes -- see
that function's own docstring for the raw/corrected file split.

Three origins:

- current_session: a usable median already sits in this session's own
  directory (e.g. made for an earlier observation tonight).
- camera: otherwise, switches the spectroscope to DARK and shoots a fresh
  GUIDING_DARK_COUNT-frame series with the guiding camera at
  subplugin_params.guiding.exposure_seconds (the same duration every real
  guide image uses -- not a separate, dedicated value of its own, to keep
  the dark's exposure time and the guide images it corrects permanently in
  sync), then median-combines them into one file.
- synthetic: when the active hardware profile's spectroscope declares
  obstructs_light: false (SpectroscopePlaceholder on the simulator profile
  -- see its own docstring: set_light_source() never physically blocks
  light, so a "DARK" shot there would just be another star exposure, not a
  real dark) -- writes a single all-zero frame instead of shooting
  anything, matching the guiding camera's current frame size. Subtracting
  an all-zero array is a true no-op, so every downstream guide image comes
  out exactly as if no correction had ever been requested -- without
  capture_guide_image() or any of its callers needing to know this
  equipment can't produce a real dark at all.

Casier: {"origin": "current_session"|"camera"|"synthetic", "dark_files":
[...raw frames, empty for synthetic...], "median_dark_file": "<combined or
synthetic filename>"}.
"""

from __future__ import annotations

DISPLAY_NAME = "Guid. dark"
TIME_CATEGORY = "calibrations"

import logging
from pathlib import Path

import numpy as np
from astropy.io import fits

from ....config import SUBPLUGIN_PARAMS
from ....hardware.spectroscope import LightSource
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import build_fits_root_name, capture_science_image
from ..dark_library_helpers import EXPOSURE_TIME_TOLERANCE_SECONDS, summarize_series_root_name

log = logging.getLogger("spectro.subplugin.guiding_dark_acquisition")

_params = SUBPLUGIN_PARAMS.get("guiding_dark_acquisition", {})
GUIDING_DARK_COUNT = _params.get("count", 7)


def _find_usable_guiding_dark_median(
    directory: Path,
    *,
    exposure_time_required: float,
    frame_size: tuple[int, int],
) -> str | None:
    """Looks for a usable guiding-dark median already sitting in `directory`
    (the session directory): the most recent GUIDINGDARK_*_median.fits file
    whose header (read alone, not the pixel data) reports the same frame
    size as frame_size (the guiding camera's currently configured
    CCD_FRAME) and an EXPTIME at least exposure_time_required minus
    EXPOSURE_TIME_TOLERANCE_SECONDS. No staleness check at all, same
    reasoning as flat_acquisition -- the guiding camera is uncooled, so
    there's no temperature to track, and the fixed sensor defects this
    exists to remove don't drift meaningfully within one session. Returns
    the filename, or None if no usable median was found."""
    candidates = sorted(directory.glob("GUIDINGDARK_*_median.fits")) if directory.is_dir() else []
    if not candidates:
        return None

    latest = candidates[-1]
    header = fits.getheader(latest)
    if (header.get("NAXIS1"), header.get("NAXIS2")) != tuple(frame_size):
        return None
    exptime = header.get("EXPTIME")
    if exptime is None or exptime < exposure_time_required - EXPOSURE_TIME_TOLERANCE_SECONDS:
        return None

    return latest.name


def _median_combine(directory: Path, filenames: list[str]) -> tuple[np.ndarray, fits.Header]:
    """Reads each named FITS file from directory and returns their
    per-pixel median (cast back to the frames' own dtype) alongside the
    first frame's header (a real copy, safe to mutate) -- the pixel data
    only, not the WCS/pointing meaning: a dark has none. Computed in
    float64 regardless of the source dtype, to avoid integer-median
    rounding artifacts."""
    arrays = [fits.getdata(directory / f) for f in filenames]
    stack = np.stack([a.astype(np.float64) for a in arrays])
    median_data = np.median(stack, axis=0).astype(arrays[0].dtype)
    header = fits.getheader(directory / filenames[0]).copy()
    return median_data, header


class GuidingDarkAcquisitionPlugin(SubPlugin):
    """Resolves a usable guiding-camera dark median for this observation --
    reused from the session directory when one already exists, freshly
    captured and combined with the guiding camera otherwise."""

    async def execute(self, obs: Observation) -> Observation:
        if not obs.obs_dir:
            raise RuntimeError("GUIDING_DARK_ACQUISITION: observation directory (obs_dir) not set")

        camera = obs.hardware["guiding_camera"]
        exposure_time = SUBPLUGIN_PARAMS.get("guiding", {})["exposure_seconds"]
        frame_size = await camera.get_frame_size()
        obs_dir = Path(obs.obs_dir)

        median_file = _find_usable_guiding_dark_median(
            obs_dir, exposure_time_required=exposure_time, frame_size=frame_size,
        )
        origin = "current_session"
        dark_files: list[str] = []

        if median_file is None:
            obstructs_light = obs.hardware_config.get("spectroscope", {}).get("obstructs_light", True)

            if not obstructs_light:
                root_name = build_fits_root_name("GUIDINGDARK", exposure_time=exposure_time, num_exposures=1)
                median_data = np.zeros((frame_size[1], frame_size[0]), dtype=np.uint16)
                median_header = fits.Header()
                median_header["EXPTIME"] = exposure_time
                median_header["NCOMBINE"] = (0, "Synthetic dark, stamped by Rosace")
                median_file = f"{root_name}_median.fits"
                fits.writeto(obs_dir / median_file, median_data, median_header, overwrite=True)
                origin = "synthetic"
            else:
                spectroscope = obs.hardware["spectroscope"]
                await spectroscope.set_light_source(LightSource.DARK)
                root_name = build_fits_root_name(
                    "GUIDINGDARK", exposure_time=exposure_time, num_exposures=GUIDING_DARK_COUNT,
                )
                for i in range(1, GUIDING_DARK_COUNT + 1):
                    suffix = f"-{i}" if GUIDING_DARK_COUNT > 1 else ""
                    path = await capture_science_image(
                        camera, exposure_time, obs.obs_dir, f"{root_name}{suffix}.fits",
                    )
                    dark_files.append(path.name)

                    if i < GUIDING_DARK_COUNT and obs.stop_check and obs.stop_check():
                        log.info(
                            f"[GUIDING_DARK_ACQUISITION] Stop requested -- "
                            f"{i}/{GUIDING_DARK_COUNT} dark(s) captured, interrupting"
                        )
                        break

                median_data, median_header = _median_combine(obs_dir, dark_files)
                median_header["NCOMBINE"] = (len(dark_files), "Frames combined, stamped by Rosace")
                median_file = f"{root_name}_median.fits"
                fits.writeto(obs_dir / median_file, median_data, median_header, overwrite=True)
                origin = "camera"

        obs.sequencer_data["guiding_dark_acquisition"] = {
            "origin": origin, "dark_files": dark_files, "median_dark_file": median_file,
        }
        log.info(f"[GUIDING_DARK_ACQUISITION] median dark = '{median_file}'")
        if dark_files:
            log.info(f"[GUIDING_DARK_ACQUISITION] Raw series root name = '{summarize_series_root_name(dark_files)}'")
        return obs
