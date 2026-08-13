"""
SEQUENCER sub-plugin -- Science spectra acquisition
Triggers the exposures and collects the produced FITS files.
Backend "internal" -- internal simulation, no external dependency (switching
the spectroscope to SKY mode is logged but not hardware-simulated -- the
internal backend never connects any hardware, obs.hardware stays empty in
this mode).

In addition to its own casier (obs.sequencer_data["star_spectra_acquisition"]),
also appends every produced filename to the shared
obs.sequencer_data["star_spectra_images"] list -- see star_spectra_acquisition.py
(the indi backend) for why: REDUCTION reads this fixed, purpose-named key,
never the name of whichever SEQUENCER sub-plugin actually produced the files.
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
        if "slew" not in obs.sequencer_data:
            raise RuntimeError(
                "STAR_SPECTRA_ACQUISITION: telescope not pointed (sequencer_data['slew'] missing)"
            )

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
            filename = str(Path(obs.obs_dir) / f"{fits_prefix}_{i:04d}.fits")
            log.info(f"[STAR_SPECTRA_ACQUISITION] Exposure {i}/{num_exposures} -> {filename}")

            await asyncio.sleep(exposure_time * simulate_speed)
            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            Path(filename).touch()
            log.info(f"[STAR_SPECTRA_ACQUISITION] Simulation -- file created: {filename}")

            raw_files.append(filename)

        obs.sequencer_data["star_spectra_acquisition"] = {"raw_files": raw_files, "count": num_exposures}
        obs.sequencer_data.setdefault("star_spectra_images", []).extend(raw_files)

        log.info(
            f"[STAR_SPECTRA_ACQUISITION] {num_exposures} exposure(s) complete -- "
            f"{len(raw_files)} file(s) produced"
        )
        return obs
