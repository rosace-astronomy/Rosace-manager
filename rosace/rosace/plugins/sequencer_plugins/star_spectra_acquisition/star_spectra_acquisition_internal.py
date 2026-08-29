"""
SEQUENCER sub-plugin -- Science spectra acquisition
Triggers the exposures and collects the produced FITS files.
Backend "internal" -- internal simulation, no external dependency (switching
the spectroscope to SKY mode is logged but not hardware-simulated -- the
internal backend never connects any hardware, obs.hardware stays empty in
this mode).

Exposure-time/sub-exposure-count policy shared with the indi backend
(compute_exposure_time_and_count, acquisition_helpers.py) -- starts from
either the target's magnitude or an explicit exposure time
(program_stars.exposure_time in targets.db), same as star_spectra_acquisition.py.
exposure.num_exposures is not honored here either (same deferred limitation
as the indi backend, see its own docstring).

No casier of its own: every produced filename is appended straight to the
shared obs.sequencer_data["star_spectra_images"] list -- see
star_spectra_acquisition.py (the indi backend) for why: REDUCTION reads
this fixed, purpose-named key, never the name of whichever SEQUENCER
sub-plugin actually produced the files.
"""

from __future__ import annotations

DISPLAY_NAME = "Science"

import asyncio
import logging
from pathlib import Path

from ....config import SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import build_fits_root_name, compute_exposure_time_and_count

log = logging.getLogger("spectro.subplugin.star_spectra_acquisition")

# Shared with dark_acquisition.py / star_spectra_acquisition.py (indi
# backend) -- see conf/config.yaml's subplugin_params section.
EXPOSURE_TIME_MAG0_SECONDS = SUBPLUGIN_PARAMS.get("exposure_time_mag0_seconds", 1.0)
MAX_EXPOSURE_TIME_SECONDS = SUBPLUGIN_PARAMS.get("max_exposure_time_seconds", 300)


class StarSpectraAcquisitionPlugin(SubPlugin):
    """Runs the science sub-exposure series and produces the raw FITS files -- internal simulation."""

    async def execute(self, obs: Observation) -> Observation:
        # No precondition check on a previous step's casier: if pointing had failed,
        # the orchestrator's own try/except would already have stopped the sequence
        # before ever reaching this sub-plugin -- not this sub-plugin's job to re-verify.
        if not obs.obs_dir:
            raise RuntimeError("STAR_SPECTRA_ACQUISITION: observation directory (obs_dir) not set")

        exposure = obs.scheduler_data.get("exposure")
        if not exposure:
            raise RuntimeError("STAR_SPECTRA_ACQUISITION: exposure missing from scheduler_data")

        if exposure.exposure_time is not None:
            exposure_time, num_exposures = compute_exposure_time_and_count(
                exposure_time=exposure.exposure_time, max_exposure_time=MAX_EXPOSURE_TIME_SECONDS
            )
        else:
            target = obs.scheduler_data.get("target")
            if not target or target.magnitude_v is None:
                raise RuntimeError(
                    "STAR_SPECTRA_ACQUISITION: exposure_time not set and magnitude missing "
                    "(scheduler_data['target'].magnitude_v) -- cannot compute it"
                )
            exposure_time, num_exposures = compute_exposure_time_and_count(
                magnitude=target.magnitude_v,
                exposure_time_mag0=EXPOSURE_TIME_MAG0_SECONDS,
                max_exposure_time=MAX_EXPOSURE_TIME_SECONDS,
            )
        binning = exposure.binning
        grating = exposure.grating

        log.info("[STAR_SPECTRA_ACQUISITION] Spectroscope set to SKY mode (simulated)")

        simulate_speed = 0.05

        queried_name = obs.scheduler_data.get("queried_name")
        root_name = build_fits_root_name(
            "STAR", target_name=queried_name, exposure_time=exposure_time, num_exposures=num_exposures
        )
        raw_files: list[str] = []

        log.info(
            f"[STAR_SPECTRA_ACQUISITION] Target={queried_name or 'target'}, t={exposure_time}s x {num_exposures}, "
            f"binning={binning}x, grating={grating}"
        )

        for i in range(1, num_exposures + 1):
            suffix = f"-{i}" if num_exposures > 1 else ""
            basename = f"{root_name}{suffix}.fits"
            filepath = Path(obs.obs_dir) / basename
            log.info(f"[STAR_SPECTRA_ACQUISITION] Exposure {i}/{num_exposures} -> {basename}")

            await asyncio.sleep(exposure_time * simulate_speed)
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.touch()
            log.info(f"[STAR_SPECTRA_ACQUISITION] Simulation -- file created: {basename}")

            raw_files.append(basename)

        obs.sequencer_data.setdefault("star_spectra_images", []).extend(raw_files)

        log.info(
            f"[STAR_SPECTRA_ACQUISITION] {num_exposures} exposure(s) complete -- "
            f"{len(raw_files)} file(s) produced"
        )
        return obs
