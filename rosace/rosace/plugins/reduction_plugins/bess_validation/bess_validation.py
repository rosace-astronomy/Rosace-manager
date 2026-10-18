"""
REDUCTION sub-plugin — BeSS validation
Adds the final spectrum to the BeSS validation list and prepares what the observer sees to accept or reject it.

Every spectrum meant for BeSS is first checked by the observer on the BeSS
validation web page (gui/src/be_validation.ts, served at /be/). This step:
1. queues the spectrum there: one row of rosace.db's bess_validation table,
   status 'pending'. A row that already exists (a reduction run again) is
   left untouched, so a decision already taken is never undone;
2. writes the page's data next to the final spectrum, in the session
   directory (names from file_names.validation_file_name()):
   "<final>_validation.json" -- header (star, date, program, IDs,
   exposures), quality indicators (resolving power, SNR per zone, guiding
   statistics, star-to-slit offset, frames rejected by stack), the final
   spectrum and its overlay (law coverage, Balmer and He I lines), the
   post-science guide image and its overlay (measured slit, slit position
   in force, star from the astrometric solution), the last corrected 2D
   target frame with extract's spectrum window and sky zones, the FRIPON
   all-sky image as fripon_image annotated it, the two plots below, the
   observation's warnings and errors, the summary PDF's name, and "files",
   the only files the page may fetch; and two plots,
   "<final>_validation_guiding.png" (PHD2 guiding, RA and Dec) and
   "<final>_validation_frames.png" (star flux and sky level frame by frame).

The data come from the same functions as the summary PDF (summary.py), so
the page and the PDF always agree. The page is for deciding right after the
night; the PDF stays the long-term record of the observation.

Input: the shared key final_spectrum and the queried name of the target --
a missing one is an error that stops the reduction, like any step of the
chain. Every other part is never blocking: a part that cannot be prepared
is null in the JSON, with its reason under "unavailable", and a warning.
No casier: the table and the files are the record.

build_validation_data() is also called by the --build-validation-data
command line option, for spectra reduced before this step existed.
"""

from __future__ import annotations

DISPLAY_NAME = "Valid."

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from astropy.coordinates import SkyCoord
from astropy.io import fits
from matplotlib.figure import Figure

from ....config import DB_PATH
from ....database import db_queue_bess_validation
from ....file_names import validation_file_name
from ....models import Observation, Target
from ...sequencer_plugins.acquisition_helpers import slit_position
from ...sequencer_plugins.plate_solve_helper import world_to_pixel
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import find_shared_input, spectrum_overlay
from ..summary.summary import (
    HELIUM_LINES_A,
    SummaryContext,
    draw_frame_series,
    draw_guiding_axes,
    guide_image,
    log_events,
    slit_segment,
)

log = logging.getLogger("spectro.subplugin.bess_validation")

RUNNING_MAX_AGE_HOURS = 24           # a "running" session older than this is stale (no night lasts that long)
PLOT_DPI = 100
GUIDING_FIGURE_INCHES = (12.0, 5.5)   # two panels, RA above Dec
FRAMES_FIGURE_INCHES = (6.5, 4.5)     # shown next to the guide image


def _header(ctx: SummaryContext) -> dict:
    obs, h = ctx.obs, ctx.header
    return {
        "star": obs.scheduler_data.get("queried_name"),
        "magnitude_v": h.get("BSS_VMAG"),
        "spectral_type": h.get("BSS_STYP"),
        "date_obs": h.get("DATE-OBS"),
        "date_end": h.get("DATE-END"),
        "exposures": h.get("EXPTIME2"),
        "exptime_s": h.get("EXPTIME"),
        "program": obs.scheduler_data.get("program"),
        "priority": obs.scheduler_data.get("priority"),
        "session_id": obs.session_id,
        "observation_id": obs.id,
        "final_spectrum": ctx.final_path.name,
    }


def _quality(ctx: SummaryContext) -> dict:
    """Read from the final FITS where the summary reads them, so the page
    shows what the delivered file says."""
    h, reduction = ctx.header, ctx.obs.reduction_data
    snr, k = [], 1
    while f"SNR{k}" in h:
        snr.append({"wavelength_min": h[f"SNR{k}LO"], "wavelength_max": h[f"SNR{k}HI"], "snr": h[f"SNR{k}"]})
        k += 1
    rejected = [Path(r["file"]).name for r in (reduction.get("stack") or {}).get("rejected", [])]
    residual = (ctx.obs.sequencer_data.get("stop_phd2_guiding") or {}).get("residual_arcsec")
    return {
        "unit": "nm" if str(h.get("CUNIT1", "")).lower() == "nm" else "A",
        "resolving_power": h.get("BSS_ESRP"),
        "resolving_power_wavelength": h.get("BSS_SRPW"),
        "snr": snr,
        "guiding": reduction.get("guiding_stats"),
        "star_slit_offset_arcsec": residual,
        "rejected_frames": rejected,
        "frames": len(reduction.get("extract_target_spectra") or []),
    }


def _guide(ctx: SummaryContext) -> dict:
    path, _ = guide_image(ctx)
    if not path.exists():
        raise FileNotFoundError(f"guide image not found: {path.name}")
    slit_x, slit_y = slit_position(ctx.obs)
    target = ctx.obs.scheduler_data.get("target")
    star = None
    try:
        star = [float(v) for v in world_to_pixel(str(path), SkyCoord(target.ra, target.dec, unit="deg"))]
    except Exception as error:
        log.warning(f"[bess_validation] Star position not available on {path.name}: {error}")
    return {
        "file": str(path.relative_to(ctx.session_dir)),
        "overlay": {"line": slit_segment(ctx), "point": [float(slit_x), float(slit_y)], "star": star},
    }


def _frame2d(ctx: SummaryContext) -> dict:
    """The last corrected 2D target frame -- the one extract measured on, once
    preprocessed and corrected for tilt and smile -- with extract's spectrum
    window and sky zones: rows as outer pixel edges, the whole width."""
    reduction = ctx.obs.reduction_data
    name = reduction["smile_target_images"][-1]
    path = ctx.session_dir / name
    if not path.exists():
        raise FileNotFoundError(f"corrected frame not found: {path.name}")
    width = fits.getheader(path)["NAXIS1"]
    rows = lambda first, last: [-0.5, first - 0.5, width - 0.5, last + 0.5]
    return {
        "file": name,
        "overlay": {"window": rows(*reduction["extract"]["window"]),
                    "zones": [rows(*zone) for zone in reduction["extract"]["sky_zones"]]},
    }


def _sky(ctx: SummaryContext) -> dict:
    """The FRIPON image of the observation, as fripon_image annotated it
    (shared key sky_camera); the wide-field image will sit beside it."""
    sky = ctx.obs.reduction_data.get("sky_camera")
    if not sky:
        raise ValueError("no FRIPON image for this observation")
    if not (ctx.session_dir / sky["annotated_file"]).exists():
        raise FileNotFoundError(f"{sky['annotated_file']} not found")
    return {"fripon": {"file": sky["annotated_file"], "station": sky["station"], "capture_time": sky["capture_time"],
                       "offset_s": sky["offset_s"], "model": sky.get("model"), "matched_stars": sky.get("matched_stars")}}


def _save(figure: Figure, path: Path) -> None:
    figure.savefig(path, dpi=PLOT_DPI)


def _guiding_plot(ctx: SummaryContext, name: str) -> str:
    figure = Figure(figsize=GUIDING_FIGURE_INCHES, layout="constrained")
    grid = {"RA": 211, "Dec": 212}
    draw_guiding_axes(lambda axis: figure.add_subplot(grid[axis]), ctx)
    _save(figure, ctx.session_dir / name)
    return name


def _frames_plot(ctx: SummaryContext, name: str) -> str:
    figure = Figure(figsize=FRAMES_FIGURE_INCHES, layout="constrained")
    draw_frame_series(lambda: figure.add_subplot(111), ctx)
    _save(figure, ctx.session_dir / name)
    return name


def build_validation_data(obs: Observation) -> dict:
    """Writes the validation page's JSON and plots next to the final spectrum
    (see module docstring) and returns the JSON's content. Raises only when
    the final spectrum itself cannot be read."""
    ctx = SummaryContext(obs)
    final = obs.reduction_data["final_spectrum"]
    unavailable: dict[str, str] = {}

    def part(key: str, build):
        try:
            return build()
        except Exception as error:
            unavailable[key] = str(error)
            log.warning(f"[bess_validation] {key} not available for {obs.id}: {error}")
            return None

    spectrum = {"file": ctx.final_path.name,
                "overlay": part("spectrum_overlay", lambda: spectrum_overlay(obs, ctx.final_path, HELIUM_LINES_A))}
    guide = part("guide", lambda: _guide(ctx))
    frame2d = part("frame2d", lambda: _frame2d(ctx))
    sky = part("sky", lambda: _sky(ctx))
    plots = {
        "guiding": part("guiding_plot", lambda: _guiding_plot(ctx, validation_file_name(final, "guiding"))),
        "frames": part("frames_plot", lambda: _frames_plot(ctx, validation_file_name(final, "frames"))),
    }
    warnings = part("warnings", lambda: [{"phase": phase, "time": time, "level": level, "message": message}
                                         for phase, time, level, message in log_events(obs)])
    summary_pdf = obs.reduction_data.get("summary_file")
    if summary_pdf and not (ctx.session_dir / summary_pdf).exists():
        summary_pdf = None

    files = [spectrum["file"], *(p for p in plots.values() if p)]
    if guide:
        files.append(guide["file"])
    if frame2d:
        files.append(frame2d["file"])
    if sky:
        files.append(sky["fripon"]["file"])
    if summary_pdf:
        files.append(summary_pdf)
    data = {
        "header": part("header", lambda: _header(ctx)),
        "quality": part("quality", lambda: _quality(ctx)),
        "spectrum": spectrum,
        "guide": guide,
        "frame2d": frame2d,
        "sky": sky,
        "plots": plots,
        "warnings": warnings,
        "summary_pdf": summary_pdf,
        "files": files,
        "unavailable": unavailable,
    }
    (ctx.session_dir / validation_file_name(final, "data")).write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return data


def load_observation(path: Path) -> Observation:
    """An observation as its YAML dossier recorded it, the target back as a
    Target (the only scheduler_data object build_validation_data() reads)."""
    obs = Observation.model_validate(yaml.safe_load(path.read_text()))
    target = obs.scheduler_data.get("target")
    if isinstance(target, dict):
        obs.scheduler_data["target"] = Target(**target)
    return obs


def rebuild_validation_data(apply: bool = False, db_path: Path | None = None) -> list[tuple[str, str, str]]:
    """The --build-validation-data command line option: (re)writes the page
    data of every spectrum in the BeSS validation list, from its observation
    dossier -- for spectra reduced before this step existed, or after a change
    of the page's format. Writes only the "_validation" files, never the
    database. Without apply, only checks each dossier can be read. Refused
    while a session runs. Returns (observation ID, star, outcome) per spectrum."""
    db = sqlite3.connect(f"file:{db_path or DB_PATH}?mode=ro", uri=True)
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=RUNNING_MAX_AGE_HOURS)).isoformat()
        if db.execute("SELECT 1 FROM sessions WHERE status = 'running' AND started_at > ?", (since,)).fetchone():
            raise RuntimeError("a session is running -- run this again once it has closed")
        rows = db.execute(
            """
            SELECT v.observation_id, v.star, s.session_dir, o.yaml_file
            FROM bess_validation v
            LEFT JOIN sessions s ON s.id = v.session_id
            LEFT JOIN observations o ON o.id = v.observation_id
            ORDER BY o.created_at
            """
        ).fetchall()
    finally:
        db.close()
    results = []
    for obs_id, star, session_dir, yaml_file in rows:
        try:
            if not session_dir or not yaml_file:
                raise FileNotFoundError("no session directory or observation file recorded")
            obs = load_observation(Path(session_dir) / yaml_file)
            if not apply:
                results.append((obs_id, star, "ready"))
                continue
            data = build_validation_data(obs)
            missing = ", ".join(data["unavailable"])
            results.append((obs_id, star, f"written{f' (not available: {missing})' if missing else ''}"))
        except Exception as error:
            results.append((obs_id, star, f"FAILED: {error}"))
    return results


class BessValidationPlugin(SubPlugin):
    """Queues the final spectrum for the observer's validation before BeSS, with the page's data."""

    async def execute(self, obs: Observation) -> Observation:
        final_spectrum = find_shared_input(obs, "final_spectrum")
        star = obs.scheduler_data.get("queried_name")
        if not star:
            raise RuntimeError("No queried_name in scheduler_data -- cannot name the spectrum to validate")
        data = build_validation_data(obs)
        added = await db_queue_bess_validation(obs.id, obs.session_id, star, final_spectrum)
        state = "queued for validation" if added else "already in the validation list, left unchanged"
        missing = f" ({len(data['unavailable'])} part(s) not available)" if data["unavailable"] else ""
        log.info(f"[bess_validation] {star} ({obs.id}) {state}; page data written{missing}")
        return obs
