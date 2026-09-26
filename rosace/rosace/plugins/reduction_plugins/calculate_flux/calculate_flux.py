"""
REDUCTION sub-plugin — Spectrum flux of every raw science sub-exposure.
Measures each file of sequencer_data["star_spectra_images"] with
spectrum_flux_helpers.measure_spectrum_flux() (raw frame, flux above the
per-column background, peak raw pixel) and records it both in this
observation's casier and in rosace.db's spectrum_flux table, to follow
spectrum intensity over time (transparency, exposure tuning).

Never blocking: a sub-exposure that cannot be measured (no trace, unreadable
file, database error) is logged as a warning and skipped, and execute() never
raises -- a diagnostic measurement must not stop the reduction that follows.
Reads only sequencer_data, so its position in a program's reduction: list
does not matter.

Sequential on purpose (about 0.5 s of computation per frame, no await
inside): REDUCTION currently shares the sequencer's event loop, so this
sub-plugin is kept out of real-hardware programs until post-observation
stages run in their own thread (TODO.md).
"""

from __future__ import annotations

DISPLAY_NAME = "Flux"

import dataclasses
import logging
from pathlib import Path

from astropy.io import fits

from ....database import db_insert_spectrum_flux
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..spectrum_flux_helpers import METHOD_VERSION, measure_spectrum_flux

log = logging.getLogger("spectro.subplugin.calculate_flux")


def _measure_file(path: Path):
    """FITS read + measurement (about 0.5 s of computation per frame)."""
    with fits.open(path) as hdul:
        data = hdul[0].data
        exposure = hdul[0].header.get("EXPTIME")
    if data is None:
        raise ValueError("no image data in the primary HDU")
    return measure_spectrum_flux(data), (float(exposure) if exposure is not None else None)


class CalculateFluxPlugin(SubPlugin):
    """Measures and records the spectrum flux of every raw science sub-exposure."""

    async def execute(self, obs: Observation) -> Observation:
        try:
            await self._measure_all(obs)
        except Exception as e:
            log.warning(f"[CALCULATE_FLUX] Flux measurement abandoned: {e}")
        return obs

    async def _measure_all(self, obs: Observation) -> None:
        raw_files = obs.sequencer_data.get("star_spectra_images") or []
        if not raw_files:
            log.warning("[CALCULATE_FLUX] No science sub-exposure to measure (sequencer_data['star_spectra_images'] empty)")
            return
        if not obs.obs_dir:
            log.warning("[CALCULATE_FLUX] Observation directory (obs_dir) not set -- nothing measured")
            return

        target = obs.scheduler_data.get("target")
        target_name = obs.scheduler_data.get("queried_name") or (target.name if target else "")
        magnitude_v = target.magnitude_v if target else None

        images: dict[str, dict] = {}
        for name in raw_files:
            try:
                result, exposure_s = _measure_file(Path(obs.obs_dir) / name)
            except Exception as e:
                log.warning(f"[CALCULATE_FLUX] {name}: not measured ({e})")
                continue
            for note in result.notes:
                log.warning(f"[CALCULATE_FLUX] {name}: {note}")

            measurement = dataclasses.asdict(result)
            measurement.pop("notes")
            log.info(
                f"[CALCULATE_FLUX] {name}: total {result.total_adu:.4g} ADU, "
                f"column max {result.column_max_adu:.0f} at x={result.column_max_x}, "
                f"peak pixel {result.peak_pixel_adu:.0f}{' (SATURATED)' if result.saturated else ''}, "
                f"background {result.background_adu:.0f}, sigma {result.trace_sigma_px:.2f} px ({result.trace_method})"
            )
            try:
                await db_insert_spectrum_flux(obs.id, target_name, magnitude_v, name, exposure_s, measurement)
            except Exception as e:
                log.warning(f"[CALCULATE_FLUX] {name}: measured but not recorded in the database ({e})")

            measurement.pop("method_version")
            images[name] = {"exposure_s": exposure_s, **measurement}

        if images:
            obs.reduction_data["calculate_flux"] = {"method_version": METHOD_VERSION, "images": images}
