"""
REDUCTION sub-plugin — Spectrum plot
Plots the final spectrum to PNG, PDF and/or SVG, each format only when the configuration asks for it.

Input: final_spectrum (the FITS written by write_spectrum, in the session
directory). Outputs: "<final name>.png" / ".pdf" / ".svg" next to it, one per
format set to true in subplugin_params.plot_spectrum (png, pdf, svg; none
set: nothing is produced). A sober A4 landscape graph: the spectrum alone,
the part outside the dispersion law's coverage shaded as extrapolated, the
Balmer lines when balmer_lines is true. Title: object, date, program.
Subtitle: site, equipment, observation and session IDs, observation file,
mid-observation time, exposures. The files written are listed in the casier
reduction_data["plot_spectrum"]["files"].
"""

from __future__ import annotations

DISPLAY_NAME = "Plot"

import logging
from pathlib import Path

from astropy.io import fits
from astropy.time import Time
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from ....config import OBSERVATORY, SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import draw_spectrum, find_shared_input, law_coverage, wavelength_axis

log = logging.getLogger("spectro.subplugin.plot_spectrum")

FORMATS = ("png", "pdf", "svg")
A4_LANDSCAPE_INCHES = (11.69, 8.27)
PNG_DPI = 150


def spectrum_figure(obs: Observation, path: Path, balmer: bool) -> Figure:
    """The final spectrum's figure (see module docstring)."""
    data, header = fits.getdata(path, header=True)
    wavelengths = wavelength_axis(header)
    session_dir = Path(obs.obs_dir or "")
    law_name = obs.reduction_data.get("dispersion_law_file")
    coverage = law_coverage(session_dir / law_name) if law_name else None
    site = OBSERVATORY
    start = Time(header["DATE-OBS"], format="isot", scale="utc")
    middle = Time(header["JD-MID"], format="jd", scale="utc") if "JD-MID" in header else start

    figure = Figure(figsize=A4_LANDSCAPE_INCHES)
    FigureCanvasAgg(figure)
    title = f"{header.get('OBJNAME', '')} - {start.datetime:%Y-%m-%d} - {obs.scheduler_data.get('program', '')}"
    figure.suptitle(title, fontsize=15, fontweight="bold", y=0.955)
    details = [
        site.get("site", ""), site.get("equipment", ""),
        f"observation {obs.id}", f"session {obs.session_id}",
        Path(obs.yaml_file).name if obs.yaml_file else "",
        f"mid-observation {middle.datetime:%H:%M:%S} UT", header.get("EXPTIME2", ""),
    ]
    figure.text(0.5, 0.91, "  |  ".join(d for d in details if d), ha="center", fontsize=9, color="#444")
    axes = figure.add_axes((0.08, 0.09, 0.89, 0.77))
    draw_spectrum(axes, wavelengths, data, coverage, balmer, header.get("CUNIT1", "Angstrom"))
    return figure


class PlotSpectrumPlugin(SubPlugin):
    """Plots the final spectrum to PNG, PDF and/or SVG, each format only when the configuration asks for it."""

    async def execute(self, obs: Observation) -> Observation:
        params = SUBPLUGIN_PARAMS.get("plot_spectrum", {})
        formats = [f for f in FORMATS if params.get(f, False)]
        if not formats:
            log.info("[PLOT_SPECTRUM] No format requested (plot_spectrum.png/pdf/svg all false)")
            return obs
        session_dir = Path(obs.obs_dir or "")
        source = session_dir / find_shared_input(obs, "final_spectrum")
        figure = spectrum_figure(obs, source, bool(params.get("balmer_lines", False)))
        written = []
        for extension in formats:
            out = source.with_suffix(f".{extension}")
            figure.savefig(out, dpi=PNG_DPI if extension == "png" else None)
            written.append(out.name)
        obs.reduction_data["plot_spectrum"] = {"files": written}
        log.info(f"[PLOT_SPECTRUM] Wrote {', '.join(written)}")
        return obs
