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

Exposure suggestion: from the brightest raw pixel of the series, the
sub-exposure duration that would put it at target_peak_fraction
(subplugin_params.calculate_flux, 0.7 by default) of the saturation level --
an aid for setting program_stars.exposure_time by hand. Recorded in the casier
and as one line per observation (with its ID) in exposure_suggestions.txt, in
the session directory. When a frame is saturated, the true peak is unknown and
the suggestion is only an upper bound (written "<=").

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

from ....config import SUBPLUGIN_PARAMS
from ....database import db_insert_spectrum_flux
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..spectrum_flux_helpers import METHOD_VERSION, SATURATION_ADU, measure_spectrum_flux

log = logging.getLogger("spectro.subplugin.calculate_flux")

TARGET_PEAK_FRACTION = SUBPLUGIN_PARAMS.get("calculate_flux", {}).get("target_peak_fraction", 0.7)
SUGGESTIONS_FILENAME = "exposure_suggestions.txt"
SUGGESTION_COLUMNS = ("obs_id", "utc", "star", "mag", "frames", "exposure_s", "peak_pixel_max",
                      "column_max_max", "background_adu", "saturated", "suggested_exposure_s")


def _measure_file(path: Path):
    """FITS read + measurement (about 0.5 s of computation per frame)."""
    with fits.open(path) as hdul:
        data = hdul[0].data
        exposure = hdul[0].header.get("EXPTIME")
    if data is None:
        raise ValueError("no image data in the primary HDU")
    return measure_spectrum_flux(data), (float(exposure) if exposure is not None else None)


def exposure_suggestion(images: dict[str, dict], target_fraction: float = TARGET_PEAK_FRACTION) -> dict:
    """Suggested sub-exposure duration from the series' brightest raw pixel:
    exposure x (target - background) / (peak - background), target =
    target_fraction x SATURATION_ADU, rounded to 0.1 s. None when the duration
    is unknown or the peak does not rise above the background. A saturated
    frame makes the suggestion an upper bound (the true peak is higher)."""
    brightest = max(images.values(), key=lambda m: m["peak_pixel_adu"])
    exposure_s = brightest["exposure_s"]
    peak, background = brightest["peak_pixel_adu"], brightest["background_adu"]
    target = target_fraction * SATURATION_ADU
    suggested = None
    if exposure_s and peak > background:
        suggested = round(exposure_s * (target - background) / (peak - background), 1)
    saturated = sum(1 for m in images.values() if m["saturated"])
    return {
        "frames": len(images),
        "exposure_s": exposure_s,
        "peak_pixel_max": peak,
        "column_max_max": max(m["column_max_adu"] for m in images.values()),
        "background_adu": background,
        "saturated": saturated,
        "target_peak_adu": target,
        "suggested_exposure_s": suggested,
        "upper_bound": saturated > 0,
    }


def _append_suggestion_line(path: Path, obs: Observation, star: str, magnitude_v, suggestion: dict) -> None:
    """One tab-separated line per observation; the header is written with the file."""
    suggested = suggestion["suggested_exposure_s"]
    suggested_text = "-" if suggested is None else f"{'<=' if suggestion['upper_bound'] else ''}{suggested:g}"
    values = (
        obs.id, f"{obs.created_at:%Y-%m-%dT%H:%MZ}", star,
        "-" if magnitude_v is None else f"{magnitude_v:g}",
        suggestion["frames"], "-" if suggestion["exposure_s"] is None else f"{suggestion['exposure_s']:g}",
        f"{suggestion['peak_pixel_max']:.0f}", f"{suggestion['column_max_max']:.0f}",
        f"{suggestion['background_adu']:.0f}", suggestion["saturated"], suggested_text,
    )
    new_file = not path.exists()
    with open(path, "a", encoding="utf-8") as f:
        if new_file:
            f.write("\t".join(SUGGESTION_COLUMNS) + "\n")
        f.write("\t".join(str(v) for v in values) + "\n")


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

        if not images:
            return
        suggestion = exposure_suggestion(images)
        obs.reduction_data["calculate_flux"] = {
            "method_version": METHOD_VERSION, "images": images, "exposure_suggestion": suggestion,
        }
        suggested = suggestion["suggested_exposure_s"]
        log.info(
            f"[CALCULATE_FLUX] Exposure suggestion: "
            f"{'none' if suggested is None else ('<=' if suggestion['upper_bound'] else '') + f'{suggested:g} s'} "
            f"per sub-exposure (peak {suggestion['peak_pixel_max']:.0f} ADU in "
            f"{'?' if suggestion['exposure_s'] is None else format(suggestion['exposure_s'], '.1f')} s, "
            f"target {suggestion['target_peak_adu']:.0f} ADU, {suggestion['saturated']} saturated frame(s))"
        )
        try:
            _append_suggestion_line(Path(obs.obs_dir) / SUGGESTIONS_FILENAME, obs, target_name, magnitude_v, suggestion)
        except Exception as e:
            log.warning(f"[CALCULATE_FLUX] Exposure suggestion not written to {SUGGESTIONS_FILENAME} ({e})")
