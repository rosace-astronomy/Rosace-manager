"""
SEQUENCER sub-plugin -- Science spectra acquisition
Triggers the exposures and collects the produced FITS files.
Backend "internal" -- internal simulation, no external dependency (switching
the spectroscope to SKY mode is logged but not hardware-simulated -- the
internal backend never connects any hardware, obs.hardware stays empty in
this mode).

No casier of its own: every produced filename is appended straight to the
shared obs.sequencer_data["star_spectra_images"] list -- see
star_spectra_acquisition.py (the indi backend) for why: REDUCTION reads
this fixed, purpose-named key, never the name of whichever SEQUENCER
sub-plugin actually produced the files.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.star_spectra_acquisition")


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

        exposure_time = exposure.exposure_time
        num_exposures = exposure.num_exposures
        binning = exposure.binning
        grating = exposure.grating

        log.info("[STAR_SPECTRA_ACQUISITION] Spectroscope set to SKY mode (simulated)")

        fits_prefix = "spectrum"
        simulate_speed = 0.05

        queried_name = obs.scheduler_data.get("queried_name") or "target"
        raw_files: list[str] = []

        log.info(
            f"[STAR_SPECTRA_ACQUISITION] Target={queried_name}, t={exposure_time}s x {num_exposures}, "
            f"binning={binning}x, grating={grating}"
        )

        for i in range(1, num_exposures + 1):
            basename = f"{fits_prefix}_{i:04d}.fits"
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
