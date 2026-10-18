"""
REDUCTION sub-plugin — Observation summary
Writes a few-page PDF showing at a glance how the observation went: the graphic counterpart of its observation file.

Everything comes from the Observation and the files it references (frames,
spectra, dispersion law, final FITS) and from the configuration, except the
warnings and errors, which only the log holds (rosace.log and its rotated
backups, read through session_log_extractor.read_log_lines()).

A4 portrait pages, "<final name>_summary.pdf" in the session directory,
shared key summary_file:
1. Summary: a header with the title block on the left (star name; magnitude,
   date and program; session and observation IDs; final file) and, on the right, a
   box of the same height with the quality indicators, one per line -- the
   resolving power (BSS_ESRP at BSS_SRPW), the signal-to-noise ratio of
   each measured zone (SNR1, SNR2...) and the guiding RMS (GUIDRMS), read
   from the final FITS, so the page
   shows what the delivered file says; "not measured" when absent;
   the observation's conditions (object, program, times, exposures,
   site, equipment, configuration file, observation file, IDs); the final
   spectrum (Balmer and He I lines) and, right below on the same wavelength
   axis, the calibration lamp with every reference line kept (within the
   tolerance of the law, residual shown) or not -- the extrapolated parts
   shaded on both, so the coverage visibly comes from the lines kept.
2. Guiding and sky: the post-science guide image (stop_phd2_guiding; else the
   corrected pointing image), whole (with a box around the slit measured by
   slit_xy -- slope and ends) and zoomed around the slit,
   with the slit position in force, the star's position from the image's astrometric
   solution, the star-to-slit offset and the image's median level (clouds,
   light pollution); then, frame by frame against UT, the star's flux (sum of
   its 1D spectrum) and the sky level (median of the sky zones of extract in
   its corrected frame), frames rejected by stack marked; below, PHD2
   guiding, from the observation's guide log (stop_phd2_guiding, shared
   key guide_log_file) against UT: one panel per axis (RA, Dec) with the
   star's error (arcsec, curve) and the correction pulses (ms, bars on a
   second axis, drawn in the direction the pulse moves the star: East and
   North up, so a bar opposes the error it corrects), the science exposures
   as green bands, the lock position changes as dashed lines, lost frames
   as black triangles along the top, the part before PHD2's last completed
   settling shaded grey (not measured); the statistics of guiding_stats
   (shared key guiding_stats, measured after settling) above. The error and
   pulse axes stop at the 99th percentile after settling (a lock position
   change alone can reach 20").
3. Extraction and series: the whole sum of the corrected 2D target frames
   (true proportions), with extract's spectrum window and sky zones (stretched on the
   sky zones, so the background shows); each frame's spectrum calibrated
   with the law, set to 1 on the scaling zone and limited to the crop range,
   overlaid; below, each one divided by the mean of the frames (1 = same
   shape as the others).
4. Sky: the FRIPON all-sky image of the observation as fripon_image
   annotated it (shared key sky_camera: target, bright stars, N/E/S/W), in
   the left half -- the right half is kept for a wide-field image.
5. The observation's WARNING and ERROR log lines -- during the acquisition,
   between "[DB] Observation inserted: <id>" and "[DB] sequencer_done stamped
   for <id>" (lines tagged by a reduction step left out: the previous
   observation's reduction may run meanwhile); during the reduction, after
   the last "[REDUCTION] Starting reduction" line (one reduction at a time),
   only the lines tagged by this program's reduction steps. At most
   LOG_MAX_LINES lines.

Never blocking: a page that cannot be drawn (missing data) says so on the
page, with a warning in the log; only failing to write the PDF at all is an
error.
"""

from __future__ import annotations

DISPLAY_NAME = "Summary"

import logging
import re
from datetime import timedelta
from pathlib import Path

import numpy as np
from astropy.coordinates import Angle, SkyCoord
from astropy.io import fits
from astropy.time import Time, TimeDelta
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.dates import DateFormatter
from matplotlib.figure import Figure
from matplotlib.image import imread
from matplotlib.patches import Polygon, Rectangle
from numpy.polynomial import Polynomial

from ....config import CONFIG_PATH, OBSERVATORY, SITE_ELEVATION_M, SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG, SUBPLUGIN_PARAMS
from ....models import Observation
from ....phd2.guide_log import parse_guide_log, settled_frame
from ....session_log_extractor import read_log_lines
from ...sequencer_plugins.acquisition_helpers import slit_position
from ...sequencer_plugins.plate_solve_helper import world_to_pixel
from ...sequencer_plugins.slit_xy.slit_xy import measure_slit, parse_area, search_zone
from ...sub_plugin_base import SubPlugin
from ..dispersion_law.dispersion_law import detect_lines, read_reference_lines
from ..reduction_helpers import (
    BALMER_LINES_A,
    draw_spectrum,
    final_name,
    law_coverage,
    read_law,
    resample_linear,
    wavelength_axis,
)

log = logging.getLogger("spectro.subplugin.summary")

A4_PORTRAIT_INCHES = (8.27, 11.69)
GUIDE_ZOOM_HALF_PX = 120          # half-size of the guide image zoom around the slit
SLIT_BOX_HALF_HEIGHT_PX = 20      # half-height of the box drawn around the measured slit
SHORT_SERIES_SECONDS = 600        # below, the time axis shows seconds
MIN_SERIES_SPAN_FRACTION = 0.10   # frame-by-frame axes span at least +-10 % of the mean
ZOOM_STRETCH_SIGMA = 15           # slit zoom: white at the sky median + 15 noise sigmas
HELIUM_LINES_A = {"He I 4026": 4026.19, "He I 4471": 4471.48, "He I 4921": 4921.93,
                  "He I 5876": 5875.62, "He I 6678": 6678.15}
LOG_MAX_LINES = 60
GUIDE_ERROR_MIN_LIMIT_ARCSEC = 3.0   # PHD2 page: smallest error axis (+-)
GUIDE_PULSE_MIN_LIMIT_MS = 300       # PHD2 page: smallest pulse axis (+-)
SERIES_RECT = (0.13, 0.49, 0.72, 0.15)   # frame-by-frame flux and sky, page 2
EXPOSURE_BAND = "#b9cdb9"            # science exposures, a tint of the web GUI's accent colour
SETTLING_BAND = "#e3e3e3"            # PHD2 settling, left out of the guiding statistics
GUIDE_COLOURS = {"RA": ("#0b2f5c", "#5b8fd0"), "Dec": ("#5c140b", "#e0705c")}   # (error curve, pulse bars)
QUALITY_FONT_SIZE = 10            # page 1 quality box (R and the SNR zones, one per line)
QUALITY_BOX_LEFT = 0.72           # the box takes the right quarter of the page 1 header
HEADER_TOP = 0.975
_LOG_RECORD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2})) - (\w+) - (.*)$", re.S)
_TAG_RE = re.compile(r"^\[([A-Z0-9_]+)\]")
COLOURS = ("#1d3c6e", "#c0392b", "#27864a", "#d68910", "#7d3c98", "#17a589", "#7f8c8d", "#2e86c1")


def _page(title: str | None, subtitle: str = "") -> Figure:
    figure = Figure(figsize=A4_PORTRAIT_INCHES)
    FigureCanvasAgg(figure)
    if title:
        figure.suptitle(title, fontsize=13, fontweight="bold", y=0.975)
    if subtitle:
        figure.text(0.5, 0.948, subtitle, ha="center", fontsize=8, color="#444")
    return figure


def _unavailable(figure: Figure, rect, what: str, reason: str) -> None:
    axes = figure.add_axes(rect)
    axes.set_axis_off()
    axes.text(0.5, 0.5, f"{what}: not available\n({reason})", ha="center", va="center", fontsize=10, color="#8a6d3b")
    log.warning(f"[SUMMARY] {what} not available: {reason}")


class SummaryContext:
    """What every page reads, gathered once."""

    def __init__(self, obs: Observation):
        self.obs = obs
        self.session_dir = Path(obs.obs_dir or "")
        self.final_path = self.session_dir / obs.reduction_data["final_spectrum"]
        self.final, self.header = fits.getdata(self.final_path, header=True)
        self.wavelengths = wavelength_axis(self.header)
        law_name = obs.reduction_data.get("dispersion_law_file")
        self.law_path = self.session_dir / law_name if law_name else None
        self.coverage = law_coverage(self.law_path) if self.law_path else None
        self.title = (f"{self.header.get('OBJNAME', '')} - {self.header['DATE-OBS'][:10]} - "
                      f"{obs.scheduler_data.get('program', '')}")
        self.subtitle = f"Observation {obs.id}  |  session {obs.session_id}  |  {self.final_path.name}"


def _conditions(ctx: SummaryContext) -> list[tuple[str, str]]:
    obs, h = ctx.obs, ctx.header
    target = obs.scheduler_data.get("target")
    site = OBSERVATORY
    ra = Angle(h["RA"], unit="deg").to_string(unit="hourangle", sep=":", precision=1)
    dec = Angle(h["DEC"], unit="deg").to_string(unit="deg", sep=":", precision=0, alwayssign=True)
    canonical = getattr(target, "name", None) if target is not None else None
    star = h.get("OBJNAME", "") + (f"  ({canonical})" if canonical and canonical != h.get("OBJNAME") else "")
    star_details = [f"RA {ra}  Dec {dec} (J2000)"]
    if "BSS_VMAG" in h:
        star_details.append(f"V {h['BSS_VMAG']:.2f}")
    if "BSS_STYP" in h:
        star_details.append(h["BSS_STYP"])
    exposure = f"{h.get('EXPTIME2', '')}, {h['EXPTIME']:.1f} s from first start to last end"
    law = read_law(ctx.law_path)["comments"] if ctx.law_path and ctx.law_path.exists() else []
    law_line = next((c for c in law if "RMS" in c), "").replace(" (extrapolated outside).", "")
    rows = [
        ("Object", star), ("", "  ".join(star_details)),
        ("Program", f"{obs.scheduler_data.get('program', '')} (priority {obs.scheduler_data.get('priority', '-')})"),
        ("Start / end (UT)", f"{h['DATE-OBS'].replace('T', ' ')}  ->  {h['DATE-END'].replace('T', ' ')}"),
        ("Exposures", exposure),
        ("Site", f"{site.get('site', '')}  ({SITE_LATITUDE_DEG:.4f}, {SITE_LONGITUDE_DEG:.4f}, {SITE_ELEVATION_M:.0f} m)"),
        ("Equipment", f"{site.get('equipment', '')}  ({site.get('telescope', '')} + {site.get('spectrograph', '')} "
                      f"+ {site.get('camera', '')})"),
        ("Observer", site.get("observer", "")),
        ("Dispersion law", f"{ctx.law_path.name if ctx.law_path else '-'}"),
        ("", law_line),
        ("Spectrum", f"{ctx.wavelengths[0]:.1f}-{ctx.wavelengths[-1]:.1f} {h.get('CUNIT1', '')}, "
                     f"step {h['CDELT1']:.4f}, {ctx.final.size} points"),
        ("Observation file", Path(obs.yaml_file).name if obs.yaml_file else "-"),
        ("Configuration", str(CONFIG_PATH)),
        ("Observation ID", obs.id), ("Session ID", obs.session_id),
    ]
    return rows


def quality_items(ctx: SummaryContext) -> list[tuple[str, bool]]:
    """The quality indicators shown at the top of page 1, as (text, measured)."""
    h = ctx.header
    unit = "nm" if str(h.get("CUNIT1", "")).lower() == "nm" else "Å"
    if "BSS_ESRP" in h and "BSS_SRPW" in h:
        items = [(f"R = {h['BSS_ESRP']:.0f} at {h['BSS_SRPW']:g} {unit}", True)]
    else:
        items = [("R: not measured", False)]
    k = 1
    while f"SNR{k}" in h:
        items.append((f"SNR {h[f'SNR{k}']:.0f} ({h[f'SNR{k}LO']:g}–{h[f'SNR{k}HI']:g} {unit})", True))
        k += 1
    if k == 1:
        items.append(("SNR: not measured", False))
    if "GUIDRMS" in h:
        items.append((f"Guiding RMS {h['GUIDRMS']:.2f}\"", True))
    else:
        items.append(("Guiding: not measured", False))
    return items


def _draw_header(figure: Figure, ctx: SummaryContext) -> None:
    """Page 1 header: the title block on the left three quarters (star name,
    date and program, IDs, final file), and on the right quarter a box of the
    same height with the quality indicators, one per line."""
    obs, h = ctx.obs, ctx.header
    magnitude = f"Mag. {h['BSS_VMAG']:.2f}  -  " if "BSS_VMAG" in h else ""
    lines = [
        (h.get("OBJNAME", ""), 18, "bold", "#000"),
        (f"{magnitude}{h['DATE-OBS'][:10]}  -  {obs.scheduler_data.get('program', '')}", 11, "normal", "#000"),
        (f"Session {obs.session_id}  |  Observation {obs.id}", 9, "normal", "#444"),
        (ctx.final_path.name, 8, "normal", "#444"),
    ]
    y = HEADER_TOP
    for text, size, weight, colour in lines:
        figure.text(0.05, y, text, ha="left", va="top", fontsize=size, fontweight=weight, color=colour)
        y -= size / 72 / A4_PORTRAIT_INCHES[1] * 1.45          # line height in figure units
    bottom = y + 0.004
    figure.add_artist(Rectangle((QUALITY_BOX_LEFT, bottom), 0.96 - QUALITY_BOX_LEFT, HEADER_TOP + 0.006 - bottom,
                                transform=figure.transFigure, fill=False, edgecolor="#1d3c6e", linewidth=1))
    items = quality_items(ctx)
    step = (HEADER_TOP + 0.006 - bottom) / len(items)
    for i, (text, measured) in enumerate(items):
        figure.text((QUALITY_BOX_LEFT + 0.96) / 2, HEADER_TOP + 0.006 - step * (i + 0.5), text, ha="center",
                    va="center", fontsize=QUALITY_FONT_SIZE, fontweight="bold",
                    color="#1d3c6e" if measured else "#999")


def page_summary(ctx: SummaryContext) -> Figure:
    figure = _page(None)
    _draw_header(figure, ctx)
    text_axes = figure.add_axes((0.06, 0.60, 0.90, 0.28))
    text_axes.set_axis_off()
    rows = _conditions(ctx)
    for i, (label, value) in enumerate(rows):
        y = 1 - i / len(rows)
        text_axes.text(0.0, y, label, fontsize=8.5, fontweight="bold", va="top")
        text_axes.text(0.22, y, value, fontsize=7.5, va="top", family="DejaVu Sans Mono")
    axes = figure.add_axes((0.11, 0.33, 0.85, 0.23))
    draw_spectrum(axes, ctx.wavelengths, ctx.final, ctx.coverage, True, ctx.header.get("CUNIT1", "Angstrom"),
                  extra_lines=HELIUM_LINES_A)
    axes.set_title("Final spectrum (red: Balmer lines, green: He I lines, shaded: extrapolated)", fontsize=9)
    axes.set_xlabel("")
    try:
        _draw_lamp(figure, ctx, (0.11, 0.06, 0.85, 0.20), (ctx.wavelengths[0], ctx.wavelengths[-1]))
    except Exception as error:
        _unavailable(figure, (0.11, 0.06, 0.85, 0.20), "Calibrated lamp", str(error))
    return figure


def guide_image(ctx: SummaryContext) -> tuple[Path, float | None]:
    data = ctx.obs.sequencer_data
    stop = data.get("stop_phd2_guiding") or {}
    if stop.get("guide_image"):
        return ctx.session_dir / stop["guide_image"], stop.get("residual_arcsec")
    pointing = data.get("pointing_72ed") or {}
    if pointing.get("corrected_guide_image"):
        return ctx.session_dir / pointing["corrected_guide_image"], None
    raise FileNotFoundError("no guide image recorded in the observation")


def _draw_guide(figure: Figure, ctx: SummaryContext) -> None:
    """The whole guide image and a zoom around the slit, side by side."""
    path, residual = guide_image(ctx)
    image = fits.getdata(path).astype(np.float64)
    height, width = image.shape
    slit_x, slit_y = slit_position(ctx.obs)
    target = ctx.obs.scheduler_data.get("target")
    star = None
    try:
        star = world_to_pixel(str(path), SkyCoord(getattr(target, "ra"), getattr(target, "dec"), unit="deg"))
    except Exception as error:
        log.warning(f"[SUMMARY] Star position not available on {path.name}: {error}")
    median = float(np.median(image))
    offset = f"star to slit {residual:.1f}\"" if residual is not None else "star to slit: not measured"
    figure.text(0.5, 0.925, f"{path.name}  |  {offset}  |  image median {median:.0f} ADU", ha="center", fontsize=9)

    x0, x1 = int(max(0, slit_x - GUIDE_ZOOM_HALF_PX)), int(min(width, slit_x + GUIDE_ZOOM_HALF_PX))
    y0, y1 = int(max(0, slit_y - GUIDE_ZOOM_HALF_PX)), int(min(height, slit_y + GUIDE_ZOOM_HALF_PX))
    whole = figure.add_axes((0.06, 0.70, 0.44, 0.20))
    low, high = np.percentile(image, [1, 99.5])
    whole.imshow(image, origin="lower", cmap="gray", vmin=low, vmax=high, extent=(-0.5, width - 0.5, -0.5, height - 0.5))
    whole.add_patch(Rectangle((x0 - 0.5, y0 - 0.5), x1 - x0, y1 - y0, fill=False, color="#d68910", lw=1))
    # A box around the slit as slit_xy measured it (slope and ends), so the
    # slit itself stays visible
    segment = slit_segment(ctx)
    if segment is not None:
        xa, ya, xb, yb = segment
        h = SLIT_BOX_HALF_HEIGHT_PX
        whole.add_patch(Polygon([(xa, ya - h), (xb, yb - h), (xb, yb + h), (xa, ya + h)], closed=True,
                                fill=False, color="#27864a", lw=1.1))
    title = "measured slit boxed in green, " if segment is not None else ""
    whole.set_title(f"Whole guide image ({title}zoomed area in orange)", fontsize=8.5)
    zoom = figure.add_axes((0.56, 0.70, 0.40, 0.20))
    # Stretched on the sky around the slit (median + ZOOM_STRETCH_SIGMA noise
    # sigmas): the slit and the background show, the star saturates
    cutout = image[y0:y1, x0:x1]
    level = float(np.median(cutout))
    noise = 1.4826 * float(np.median(np.abs(cutout - level))) or 1.0
    low, high = float(np.percentile(cutout, 0.5)), level + ZOOM_STRETCH_SIGMA * noise
    zoom.imshow(image[y0:y1, x0:x1], origin="lower", cmap="gray", vmin=low, vmax=high,
                extent=(x0 - 0.5, x1 - 0.5, y0 - 0.5, y1 - 0.5))
    zoom.set_title("Around the slit", fontsize=8.5)
    for axes in (whole, zoom):
        axes.plot(slit_x, slit_y, "+", color="#27864a", ms=14, mew=1.5, label="slit position")
        if star is not None:
            axes.plot(star[0], star[1], "o", mfc="none", color="#c0392b", ms=12, mew=1.3, label="star (astrometry)")
        axes.tick_params(labelsize=7)
    zoom.legend(loc="upper right", fontsize=7)


def slit_segment(ctx: SummaryContext) -> list[float] | None:
    """The slit measured by slit_xy, [xa, ya, xb, yb]: from its casier, or,
    for an observation recorded before slit_xy kept it, measured again on the
    slit image it recorded, with slit_xy's own function and settings. None
    when the slit was not measured."""
    measured = ctx.obs.sequencer_data.get("slit_xy") or {}
    if measured.get("status") != "calculated":
        return None
    if measured.get("segment"):
        return [float(v) for v in measured["segment"]]
    try:
        data = fits.getdata(ctx.session_dir / measured["image"])
        guiding = SUBPLUGIN_PARAMS.get("guiding", {})
        params = SUBPLUGIN_PARAMS.get("slit_xy", {})
        zone = search_zone(data.shape, guiding["slit_x"], guiding["slit_y"],
                           parse_area(params.get("slit_x_area"), "slit_x_area"),
                           parse_area(params.get("slit_y_area"), "slit_y_area"))
        return measure_slit(data, zone, guiding["slit_x"]).segment()
    except Exception as error:
        log.warning(f"[SUMMARY] Slit line not available: {error}")
        return None


def frame_series(ctx: SummaryContext):
    """(UT times, flux, sky level, rejected flags) of the science frames."""
    reduction = ctx.obs.reduction_data
    spectra = reduction["extract_target_spectra"]
    frames = reduction["smile_target_images"]
    zones = reduction["extract"]["sky_zones"]
    rejected = {r["file"] for r in (reduction.get("stack") or {}).get("rejected", [])}
    rows = np.concatenate([np.arange(z[0], z[1] + 1) for z in zones])
    times, flux, sky, flags = [], [], [], []
    for spectrum_name, frame_name in zip(spectra, frames):
        spectrum, header = fits.getdata(ctx.session_dir / spectrum_name, header=True)
        start = Time(header["DATE-OBS"], format="isot", scale="utc")
        times.append((start + TimeDelta(float(header.get("EXPTIME", 0)) / 2, format="sec")).datetime)
        flux.append(float(np.sum(spectrum)))
        sky.append(float(np.median(fits.getdata(ctx.session_dir / frame_name)[rows])))
        flags.append(spectrum_name in rejected)
    return times, np.array(flux), np.array(sky), np.array(flags)


def _centred_limits(values: np.ndarray) -> tuple[float, float]:
    """Axis limits centred on the values, spanning at least
    +-MIN_SERIES_SPAN_FRACTION of their mean, so that a steady series sits in
    the middle and small variations are not blown up."""
    low, high = float(np.min(values)), float(np.max(values))
    centre = (low + high) / 2
    half = max((high - low) * 0.75, MIN_SERIES_SPAN_FRACTION * abs(float(np.mean(values))), 1e-9)
    return centre - half, centre + half


def _draw_series(figure: Figure, ctx: SummaryContext) -> None:
    draw_frame_series(lambda: figure.add_axes(SERIES_RECT), ctx)


def draw_frame_series(make_axes, ctx: SummaryContext) -> None:
    """The star's flux and the sky level frame by frame, on the axes
    make_axes() returns -- called only once the data is read, so a failure
    leaves no empty axes behind. Shared with bess_validation."""
    times, flux, sky, rejected = frame_series(ctx)
    axes = make_axes()
    axes.plot(times, flux, "o-", color="#1d3c6e", label="star flux")
    if rejected.any():
        axes.plot(np.array(times)[rejected], flux[rejected], "x", color="#c0392b", ms=12, mew=2,
                  label="rejected by stack")
    axes.set_ylabel("Star flux (ADU, sum of the 1D spectrum)", color="#1d3c6e", fontsize=8.5)
    axes.set_ylim(*_centred_limits(flux))
    twin = axes.twinx()
    twin.plot(times, sky, "s--", color="#d68910", label="sky level")
    twin.set_ylabel("Sky level (ADU per pixel, sky zones median)", color="#d68910", fontsize=8.5)
    twin.set_ylim(*_centred_limits(sky))
    axes.set_xlabel("UT (middle of each exposure)")
    # Seconds shown for a short series (exposures of a few seconds)
    short = len(times) > 1 and (max(times) - min(times)).total_seconds() < SHORT_SERIES_SECONDS
    axes.xaxis.set_major_formatter(DateFormatter("%H:%M:%S" if short else "%H:%M"))
    lines = axes.get_legend_handles_labels()
    more = twin.get_legend_handles_labels()
    axes.legend(lines[0] + more[0], lines[1] + more[1], loc="lower left", fontsize=8)
    axes.set_title("Frame by frame: the star's flux and the sky level", fontsize=10)
    axes.grid(alpha=0.25)


def page_guiding(ctx: SummaryContext) -> Figure:
    figure = _page(f"{ctx.title} - guiding and sky", ctx.subtitle)
    for draw, rect, what in ((_draw_guide, (0.06, 0.70, 0.90, 0.20), "Guide image"),
                             (_draw_series, SERIES_RECT, "Frame-by-frame flux and sky"),
                             (_draw_phd2_guiding, (0.11, 0.055, 0.77, 0.37), "PHD2 guiding")):
        try:
            draw(figure, ctx)
        except Exception as error:
            _unavailable(figure, rect, what, str(error))
    return figure


def exposure_spans(ctx: SummaryContext) -> list[tuple]:
    """(start, end) UT of each science exposure."""
    spans = []
    for name in ctx.obs.sequencer_data.get("star_spectra_images", []):
        header = fits.getheader(ctx.session_dir / name)
        start = Time(header["DATE-OBS"], format="isot", scale="utc")
        spans.append((start.datetime, (start + TimeDelta(float(header.get("EXPTIME", 0)), format="sec")).datetime))
    return spans


def _symmetric_limit(values: np.ndarray, minimum: float) -> float:
    values = np.abs(values[np.isfinite(values)])
    return max(minimum, 1.1 * float(np.percentile(values, 99))) if values.size else minimum


def _draw_phd2_guiding(figure: Figure, ctx: SummaryContext) -> None:
    rects = {"RA": (0.11, 0.235, 0.77, 0.14), "Dec": (0.11, 0.055, 0.77, 0.14)}
    name, stats, scale = draw_guiding_axes(lambda axis: figure.add_axes(rects[axis]), ctx)
    if stats:
        text = (f"After settling: RMS {stats['total_rms_arcsec']:.2f}\" (RA {stats['ra_rms_arcsec']:.2f}\", "
                f"Dec {stats['dec_rms_arcsec']:.2f}\")  |  largest error RA {stats['ra_max_arcsec']:.2f}\", "
                f"Dec {stats['dec_max_arcsec']:.2f}\"  |  {stats['frames']} frames, {stats['dropped']} lost  |  "
                f"{scale:g}\"/px")
    else:
        text = "Guiding statistics: not measured"
    figure.text(0.5, 0.428, text, ha="center", fontsize=8.5)
    figure.text(0.5, 0.414, f"{Path(name).name}", ha="center", fontsize=7, color="#444")
    figure.text(0.5, 0.401, "Curve: star error.  Bars: correction pulse, in the direction it moves the star "
                            "(East, North up).  Green: science exposures.  Grey: settling, not measured.", ha="center", fontsize=7, color="#444")


def draw_guiding_axes(make_axes, ctx: SummaryContext) -> tuple[str, dict | None, float]:
    """PHD2 guiding from the observation's guide log, one panel per axis on
    make_axes("RA") / make_axes("Dec") -- called only once the guide log is
    read, so a failure leaves no empty axes behind. Returns (guide log name,
    guiding statistics or None, plate scale). Shared with bess_validation."""
    name = ctx.obs.sequencer_data.get("guide_log_file")
    if not name:
        raise FileNotFoundError("no guide log recorded in the observation")
    sections = parse_guide_log(ctx.session_dir / name)
    if not sections or not sections[-1].steps:
        raise ValueError(f"no guided frame in {name}")
    section = sections[-1]
    stats = ctx.obs.reduction_data.get("guiding_stats")
    scale = (stats or {}).get("pixel_scale") or SUBPLUGIN_PARAMS.get("guiding", {}).get("sampling_arcsec") or 1.0
    start = section.started_at.replace(tzinfo=None)
    settled = settled_frame(section)
    steps = [s for s in section.steps if not s.dropped]
    times = [start + timedelta(seconds=s.time_s) for s in steps]
    frame_times = {s.frame: start + timedelta(seconds=s.time_s) for s in section.steps}
    locks = [frame_times.get(frame + 1) for frame, text in section.infos if text.startswith("SET LOCK POSITION")]
    dropped = [start + timedelta(seconds=s.time_s) for s in section.steps if s.dropped]
    # Grey until the last completed settling (the whole section when PHD2 never settled)
    settling_end = (frame_times.get(settled, start) if settled is not None
                    else start + timedelta(seconds=section.steps[-1].time_s))
    after_settling = [settled is not None and s.frame > settled for s in steps]
    exposures = exposure_spans(ctx)

    panels = (("RA", [s.ra_raw_px for s in steps], [s.ra_duration_ms if s.ra_direction == "E" else -s.ra_duration_ms
                                                     for s in steps]),
              ("Dec", [s.dec_raw_px for s in steps], [s.dec_duration_ms if s.dec_direction == "N" else -s.dec_duration_ms
                                                      for s in steps]))
    for axis, errors_px, pulses in panels:
        curve, bars = GUIDE_COLOURS[axis]
        errors = np.array(errors_px, dtype=np.float64) * scale
        axes = make_axes(axis)
        twin = axes.twinx()
        # The bars and bands on the twin axis, drawn under the error curve
        axes.set_zorder(twin.get_zorder() + 1)
        axes.patch.set_visible(False)
        for begin, end in exposures:
            twin.axvspan(begin, end, color=EXPOSURE_BAND, lw=0, zorder=0)
        twin.axvspan(start, settling_end, color=SETTLING_BAND, lw=0, zorder=0)
        twin.vlines(times, 0, pulses, color=bars, lw=1.0, zorder=1)
        pulses = np.array(pulses, dtype=np.float64)
        pulse_limit = _symmetric_limit(pulses[after_settling] if any(after_settling) else pulses,
                                       GUIDE_PULSE_MIN_LIMIT_MS)
        twin.set_ylim(-pulse_limit, pulse_limit)
        twin.set_ylabel("correction (ms)", color=bars, fontsize=8.5)
        axes.plot(times, errors, color=curve, lw=0.7)
        axes.axhline(0, color="#000", lw=0.5)
        for when in locks:
            if when is not None:
                axes.axvline(when, color="#7f8c8d", ls="--", lw=0.8)
        measured = errors[after_settling] if any(after_settling) else errors
        limit = _symmetric_limit(measured, GUIDE_ERROR_MIN_LIMIT_ARCSEC)
        axes.set_ylim(-limit, limit)
        # Lost frames: black triangles along the top edge
        if dropped:
            axes.plot(dropped, [0.92 * limit] * len(dropped), "v", color="#000", ms=4, ls="none")
        axes.set_ylabel(f"{axis} error (arcsec)", color=curve, fontsize=8.5)
        # The axis covers the guiding: an exposure outside it (the demo's replayed
        # frames keep their recording night's dates) is simply not shown
        margin = timedelta(seconds=30)
        axes.set_xlim(min(times[0], start) - margin, times[-1] + margin)
        short = (times[-1] - times[0]).total_seconds() < SHORT_SERIES_SECONDS
        axes.xaxis.set_major_formatter(DateFormatter("%H:%M:%S" if short else "%H:%M"))
        if axis == "Dec":
            axes.set_xlabel("UT", fontsize=8)
        axes.tick_params(labelsize=7)
        twin.tick_params(labelsize=7)
        if stats:
            key = axis.lower()
            axes.set_title(f"{axis}: RMS {stats[f'{key}_rms_arcsec']:.2f}\"  largest {stats[f'{key}_max_arcsec']:.2f}\""
                           f"  (dashed: lock position moved{', triangles: star lost' if dropped else ''})",
                           fontsize=9, loc="left")
        else:
            axes.set_title(axis, fontsize=9, loc="left")
    return name, stats, scale


def _spectrum_in_wavelength(ctx: SummaryContext, key: str):
    data, header = fits.getdata(ctx.session_dir / ctx.obs.reduction_data[key], header=True)
    return wavelength_axis(header), data.astype(np.float64)


def page_series(ctx: SummaryContext) -> Figure:
    figure = _page(f"{ctx.title} - extraction and series", ctx.subtitle)
    for draw, rect, what in ((_draw_extraction, (0.11, 0.60, 0.85, 0.29), "Extraction zones"),
                             (_draw_unit_spectra, (0.11, 0.07, 0.85, 0.45), "Spectra of the series")):
        try:
            draw(figure, ctx)
        except Exception as error:
            _unavailable(figure, rect, what, str(error))
    return figure


def _draw_extraction(figure: Figure, ctx: SummaryContext) -> None:
    """The whole sum of the corrected 2D target frames, with its true
    proportions, the spectrum window and the sky zones of extract. Stretched
    on the sky zones' pixels, so the sky background shows (the trace
    saturates)."""
    reduction = ctx.obs.reduction_data
    frames = reduction["smile_target_images"]
    image = sum(fits.getdata(ctx.session_dir / name).astype(np.float64) for name in frames)
    window = reduction["extract"]["window"]
    zones = reduction["extract"]["sky_zones"]
    height, width = image.shape
    sky = image[np.concatenate([np.arange(z[0], z[1] + 1) for z in zones])]
    low, high = np.percentile(sky, [1, 99.5])
    # Not below 0: the parts preprocessing set to 0 (outside the band lit
    # through the slit, dead columns) then show black
    low = max(low, 0.0)
    axes = figure.add_axes((0.11, 0.60, 0.85, 0.29))
    axes.imshow(image, origin="lower", cmap="gray", vmin=low, vmax=high, aspect="equal",
                extent=(-0.5, width - 0.5, -0.5, height - 0.5))
    for edge in window:
        axes.axhline(edge, color="#17becf", lw=1.0)
    for zone in zones:
        for edge in zone:
            axes.axhline(edge, color="#ff3fbf", lw=1.2, ls="--")
    axes.set_title(f"Extraction: sum of {len(frames)} corrected frames\nspectrum window {window[0]}-{window[1]} (cyan), "
                   f"sky zones (magenta, dashed)", fontsize=8.5)
    axes.set_xlabel("x (px)", fontsize=8)
    axes.set_ylabel("y (px)", fontsize=8)
    axes.tick_params(labelsize=7)


def _draw_lamp(figure: Figure, ctx: SummaryContext, rect, x_range: tuple[float, float]) -> None:
    """The stacked lamp in wavelength over x_range, every reference line kept
    (blue, residual) or not (orange), the extrapolated parts shaded as on the
    final spectrum above it."""
    wavelengths, lamp = _spectrum_in_wavelength(ctx, "lambda_calib_spectrum")
    law = Polynomial(read_law(ctx.law_path)["coefficients"])
    references = read_reference_lines(ctx.law_path.with_name(
        ctx.law_path.name.replace("_dispersion_law.yaml", "_reference_lines.txt")))
    tolerance = float(SUBPLUGIN_PARAMS.get("dispersion_law", {}).get("tolerance", 1.0))
    stacked_lamp = fits.getdata(ctx.session_dir / ctx.obs.reduction_data["stack_calib_spectrum"]).astype(np.float64)
    detected = law(detect_lines(stacked_lamp).x)
    axes = figure.add_axes(rect)
    visible = (wavelengths >= x_range[0]) & (wavelengths <= x_range[1])
    axes.plot(wavelengths[visible], lamp[visible], lw=0.7, color="#555")
    if ctx.coverage:
        for low, high in ((x_range[0], ctx.coverage[0]), (ctx.coverage[1], x_range[1])):
            if high > low:
                axes.axvspan(low, high, color="#f3e0c0", alpha=0.6, lw=0)
    top = float(lamp[visible].max())
    kept = 0
    for reference in references:
        distance = detected - reference
        nearest = distance[np.argmin(np.abs(distance))] if distance.size else np.inf
        if abs(nearest) <= tolerance:
            kept += 1
            axes.axvline(reference, color="#1d6fb8", lw=0.8)
            axes.text(reference, top * 0.9, f"{-nearest:+.2f}", rotation=90, fontsize=6.5, color="#1d6fb8",
                      ha="right", va="top")
        else:
            axes.axvline(reference, color="#d68910", ls="--", lw=1.2)
    axes.set_xlim(*x_range)
    axes.set_title(f"Calibration lamp: blue = reference line kept (residual, A), orange = farther than "
                   f"{tolerance:g} A ({kept}/{len(references)} kept)", fontsize=8.5)
    axes.set_xlabel("Wavelength (A, air)")
    axes.set_ylabel("Lamp (ADU)")
    axes.grid(alpha=0.25)


def _draw_unit_spectra(figure: Figure, ctx: SummaryContext) -> None:
    reduction = ctx.obs.reduction_data
    law = Polynomial(read_law(ctx.law_path)["coefficients"])
    grid_header = fits.getheader(ctx.session_dir / reduction["lambda_target_spectrum"])
    grid = wavelength_axis(grid_header)
    scaling = SUBPLUGIN_PARAMS.get("scaling", {})
    crop = SUBPLUGIN_PARAMS.get("crop", {})
    zone = (grid >= float(scaling["wavelength_min"])) & (grid <= float(scaling["wavelength_max"]))
    shown = (grid >= float(crop.get("wavelength_min", grid[0]))) & (grid <= float(crop.get("wavelength_max", grid[-1])))
    rejected = {r["file"] for r in (reduction.get("stack") or {}).get("rejected", [])}
    curves = []
    for name in reduction["extract_target_spectra"]:
        spectrum = fits.getdata(ctx.session_dir / name).astype(np.float64)
        resampled = resample_linear(spectrum, law, grid_header["CRVAL1"], grid_header["CDELT1"], grid.size)
        curves.append((Path(name).name, resampled / resampled[zone].mean(), name in rejected))
    mean = np.mean([c for _, c, flagged in curves if not flagged], axis=0)
    top = figure.add_axes((0.11, 0.29, 0.85, 0.24))
    bottom = figure.add_axes((0.11, 0.07, 0.85, 0.16), sharex=top)
    for i, (name, curve, flagged) in enumerate(curves):
        colour = COLOURS[i % len(COLOURS)]
        style = "--" if flagged else "-"
        label = f"frame {i + 1}" + (" (rejected)" if flagged else "")
        top.plot(grid[shown], curve[shown], style, lw=0.7, color=colour, label=label)
        with np.errstate(divide="ignore", invalid="ignore"):
            bottom.plot(grid[shown], (curve / mean)[shown], style, lw=0.6, color=colour)
    top.set_title("The series: each frame's spectrum, calibrated with the law, set to 1 on the scaling zone",
                  fontsize=9)
    top.set_ylabel("Relative flux")
    top.legend(fontsize=7, loc="upper right")
    top.grid(alpha=0.25)
    bottom.set_ylim(0.8, 1.2)
    bottom.axhline(1.0, color="#999", lw=0.8)
    bottom.set_title("Each frame divided by the mean of the frames: 1 = same shape as the others", fontsize=8.5)
    bottom.set_ylabel("Frame / mean")
    bottom.set_xlabel("Wavelength (A, air)")
    bottom.grid(alpha=0.25)
    top.set_xlim(grid[shown][0], grid[shown][-1])


SKY_IMAGE_RECT = (0.04, 0.50, 0.45, 0.40)   # left half: the right half is kept for the wide-field image


def _draw_sky(figure: Figure, ctx: SummaryContext) -> None:
    """The FRIPON all-sky image of the observation, as fripon_image annotated it."""
    sky = ctx.obs.reduction_data.get("sky_camera")
    if not sky:
        raise ValueError("no FRIPON image for this observation (see the log of fripon_image)")
    path = ctx.session_dir / sky["annotated_file"]
    if not path.exists():
        raise FileNotFoundError(f"{path.name} not found")
    axes = figure.add_axes(SKY_IMAGE_RECT)
    axes.imshow(imread(path))
    axes.set_axis_off()
    minutes = round(sky["offset_s"] / 60)
    positions = {"fitted": f"target and stars placed by the lens model fitted on {sky['matched_stars']} stars",
                 "stored": "target and stars placed by the stored lens model (too few stars on the image)",
                 None: "no lens model: target not placed"}.get(sky.get("model"), "")
    axes.set_title(f"FRIPON {sky['station']}, {sky['capture_time'][:19].replace('T', ' ')} UT "
                   f"({'+' if minutes >= 0 else ''}{minutes} min from mid-observation)", fontsize=8.5)
    figure.text(SKY_IMAGE_RECT[0], SKY_IMAGE_RECT[1] - 0.015, f"{positions}\nImage: FRIPON network (fripon.org)",
                fontsize=7, va="top", color="#444")


def page_sky(ctx: SummaryContext) -> Figure:
    figure = _page(f"{ctx.title} - sky", ctx.subtitle)
    try:
        _draw_sky(figure, ctx)
    except Exception as error:
        _unavailable(figure, SKY_IMAGE_RECT, "FRIPON sky image", str(error))
    return figure


def page_log(ctx: SummaryContext) -> Figure:
    figure = _page(f"{ctx.title} - warnings and errors", ctx.subtitle)
    try:
        _draw_log(figure, ctx)
    except Exception as error:
        _unavailable(figure, (0.05, 0.05, 0.9, 0.82), "Warnings and errors", str(error))
    return figure


def log_events(obs: Observation) -> list[tuple[str, str, str, str]]:
    """(phase, UT time, level, message) of the observation's WARNING and
    ERROR log lines (see module docstring)."""
    lines, _ = read_log_lines(obs.created_at - timedelta(seconds=5))
    records: list[str] = []
    for line in lines:
        if _LOG_RECORD_RE.match(line) or not records:
            records.append(line.rstrip("\n"))
        else:
            records[-1] += " " + line.strip()
    reduction_tags = {step.upper() for step in obs.program_steps.get("reduction", [])} | {"REDUCTION"}

    def tag(message: str) -> str | None:
        match = _TAG_RE.match(message)
        return match.group(1) if match else None

    parsed = [m.groups() for m in (_LOG_RECORD_RE.match(r) for r in records) if m]
    start = next((i for i, p in enumerate(parsed) if f"[DB] Observation inserted: {obs.id}" in p[3]), None)
    end = next((i for i, p in enumerate(parsed) if f"[DB] sequencer_done stamped for {obs.id}" in p[3]), None)
    reduction = max((i for i, p in enumerate(parsed) if p[3].startswith("[REDUCTION] Starting reduction")), default=None)
    events = []
    for i, (_, time, level, message) in enumerate(parsed):
        if level not in ("WARNING", "ERROR", "CRITICAL"):
            continue
        if start is not None and start <= i <= (end if end is not None else len(parsed)) and tag(message) not in reduction_tags:
            events.append(("acquisition", time, level, message))
        elif reduction is not None and i > reduction and tag(message) in reduction_tags:
            events.append(("reduction", time, level, message))
    return events


def _draw_log(figure: Figure, ctx: SummaryContext) -> None:
    events = log_events(ctx.obs)
    axes = figure.add_axes((0.05, 0.05, 0.9, 0.82))
    axes.set_axis_off()
    axes.text(0.0, 1.0, f"Warnings and errors of this observation ({len(events)})", fontsize=10,
              fontweight="bold", va="top")
    if not events:
        axes.text(0.0, 0.96, "None", fontsize=9, va="top")
        return
    step = 0.9 / LOG_MAX_LINES
    shown = events[:LOG_MAX_LINES]
    for i, (phase, time, level, message) in enumerate(shown):
        text = f"{time}  {phase:<11}  {level:<7}  {message}"
        axes.text(0.0, 0.96 - i * step, text[:120], fontsize=6.5, va="top", family="DejaVu Sans Mono",
                  color="#c0392b" if level != "WARNING" else "#333")
    if len(events) > LOG_MAX_LINES:
        axes.text(0.0, 0.96 - len(shown) * step, f"... and {len(events) - LOG_MAX_LINES} more (see the session log)",
                  fontsize=7, va="top", style="italic")


class SummaryPlugin(SubPlugin):
    """Writes a few-page PDF showing at a glance how the observation went: the graphic counterpart of its observation file."""

    async def execute(self, obs: Observation) -> Observation:
        ctx = SummaryContext(obs)
        name = f"{final_name(obs)}_summary.pdf"
        pages = 0
        with PdfPages(ctx.session_dir / name) as pdf:
            for build in (page_summary, page_guiding, page_series, page_sky, page_log):
                try:
                    figure = build(ctx)
                except Exception as error:
                    figure = _page(ctx.title, ctx.subtitle)
                    _unavailable(figure, (0.08, 0.1, 0.84, 0.75), build.__name__.removeprefix("page_"), str(error))
                pdf.savefig(figure)
                pages += 1
        obs.reduction_data["summary_file"] = name
        log.info(f"[SUMMARY] Wrote {name} ({pages} pages)")
        return obs
