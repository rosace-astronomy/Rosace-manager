"""
SEQUENCER sub-plugin -- Science spectra acquisition
Backend "indi" -- drives real hardware via obs.hardware["spectroscope"]
(light-source selection, see hardware/spectroscope.py) and
obs.hardware["science_camera"] (exposures, see hardware/camera.py). Switches
the spectroscope to SKY mode before exposing, then leaves it there (no
further action needed -- that's already the desired mode). Fully replaces
the old "expose" sub-plugin (a plain placeholder) -- same exposure-time/
sub-exposure-count policy (compute_exposure_time_and_count,
acquisition_helpers.py), now able to start from either the target's
magnitude or an explicit exposure time (program_stars.exposure_time in
targets.db). Checks obs.stop_check() between two sub-exposures (not during
one already running on the camera -- no real hardware abort here), same as
the old expose.py did.

No casier of its own: every produced filename is appended straight to the
shared obs.sequencer_data["star_spectra_images"] list -- a purpose-named
key, not this sub-plugin's own name, so that REDUCTION (and any other
downstream consumer) never needs to know which specific SEQUENCER
sub-plugin produced the science raw files, only that they land under this
fixed key. Any future alternative acquisition sub-plugin is expected to
append to the same key.
"""

from __future__ import annotations

import logging

from ....config import SUBPLUGIN_PARAMS
from ....hardware.spectroscope import LightSource
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import build_fits_root_name, capture_science_image, compute_exposure_time_and_count

log = logging.getLogger("spectro.subplugin.star_spectra_acquisition")

# Shared with dark_acquisition.py -- see conf/config.yaml's subplugin_params
# section for why these two keys sit at the root, not under one plugin name.
EXPOSURE_TIME_MAG0_SECONDS = SUBPLUGIN_PARAMS.get("exposure_time_mag0_seconds", 1.0)
MAX_EXPOSURE_TIME_SECONDS = SUBPLUGIN_PARAMS.get("max_exposure_time_seconds", 300)


class StarSpectraAcquisitionPlugin(SubPlugin):
    """Switches the spectroscope to SKY mode, then runs the science sub-exposure series."""

    async def execute(self, obs: Observation) -> Observation:
        if not obs.obs_dir:
            raise RuntimeError("STAR_SPECTRA_ACQUISITION: observation directory (obs_dir) not set")

        exposure = obs.scheduler_data.get("exposure")
        if not exposure:
            raise RuntimeError("STAR_SPECTRA_ACQUISITION: exposure missing from scheduler_data")

        spectroscope = obs.hardware["spectroscope"]
        await spectroscope.set_light_source(LightSource.SKY)
        log.info("[STAR_SPECTRA_ACQUISITION] Spectroscope set to SKY mode")

        if exposure.exposure_time is not None:
            exposure_time, count = compute_exposure_time_and_count(
                exposure_time=exposure.exposure_time, max_exposure_time=MAX_EXPOSURE_TIME_SECONDS
            )
        else:
            target = obs.scheduler_data.get("target")
            if not target or target.magnitude_v is None:
                raise RuntimeError(
                    "STAR_SPECTRA_ACQUISITION: exposure_time not set and magnitude missing "
                    "(scheduler_data['target'].magnitude_v) -- cannot compute it"
                )
            exposure_time, count = compute_exposure_time_and_count(
                magnitude=target.magnitude_v,
                exposure_time_mag0=EXPOSURE_TIME_MAG0_SECONDS,
                max_exposure_time=MAX_EXPOSURE_TIME_SECONDS,
            )

        log.info(f"[STAR_SPECTRA_ACQUISITION] {count} exposure(s) of {exposure_time}s")
        camera = obs.hardware["science_camera"]

        queried_name = obs.scheduler_data.get("queried_name")
        root_name = build_fits_root_name(
            "STAR", target_name=queried_name, exposure_time=exposure_time, num_exposures=count
        )

        raw_files = []
        for i in range(1, count + 1):
            suffix = f"-{i}" if count > 1 else ""
            path = await capture_science_image(
                camera, exposure_time, obs.obs_dir, f"{root_name}{suffix}.fits", image_to_gui=True
            )
            raw_files.append(path.name)

            if i < count and obs.stop_check and obs.stop_check():
                log.info(
                    f"[STAR_SPECTRA_ACQUISITION] Stop requested -- {i}/{count} sub-exposure(s) done, interrupting"
                )
                break

        # No sequencer_data["star_spectra_acquisition"] casier: everything descriptive this
        # step produces is already the shared star_spectra_images list below -- REDUCTION
        # (and anyone else) reads that fixed key, never this sub-plugin's own name.
        obs.sequencer_data.setdefault("star_spectra_images", []).extend(raw_files)
        log.info(f"[STAR_SPECTRA_ACQUISITION] Acquisition complete -- {raw_files}")
        return obs
