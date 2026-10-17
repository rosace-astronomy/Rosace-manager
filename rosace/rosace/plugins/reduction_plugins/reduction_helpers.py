"""
Shared functions of the native reduction chain: finding a step's input under
its shared key, writing float images, the observation's intermediate-file
directory, the session's master calibration frames (bias, dark, flat), the
preprocessing of raw frames with them (hot pixels replaced from the master
dark), column/row shifts, reading the
dispersion law and resampling a spectrum on a constant wavelength step.

Plain functions, not a sub-plugin: same status as axes_helpers.py -- not
resolved by pipeline_loader.py's dynamic importlib convention, no program
YAML ever declares this module as a step.

Every computation works on whole numpy arrays, never pixel by pixel in a
Python loop.

A master is combined pixel by pixel: for each pixel, its N values (one per
frame of the series) are sorted, the highest and the lowest are rejected,
and the remaining N-2 are averaged. At least MIN_FRAMES frames are needed.

A master is written to the session directory as "<series root>_master.fits"
(the root being the series' common name, minus each frame's "-<N>" index).
The file name decides reuse: when that file already exists it is reused
as is, otherwise it is computed -- so a master is computed once per session.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable

import numpy as np
import yaml
from astropy.io import fits
from numpy.polynomial import Polynomial

from ...config import GUI_PREVIEW_DIR
from ...file_names import safe_name
from ...image_broadcast import ws_image_broadcaster
from ...models import Observation
from .axes_helpers import FeatureAxes, find_axes

MIN_FRAMES = 3

# Below this value of the normalized master flat (~300 ADU of flat signal on
# the 72ED), a pixel is a dead sensor edge: after the division by the flat it
# can reach 1e10, so a measurement must ignore it.
MIN_FLAT_FOR_MEASUREMENT = 0.05

# The band lit through the slit: rows whose level reaches this fraction of the
# reference row's (the brightest flat row, or the trace row), minus this
# fraction of the band's height at each edge where slit-edge effects matter.
LIT_BAND_LEVEL_FRACTION = 0.5
LIT_BAND_MARGIN_FRACTION = 0.05

# A pixel is hot when its master dark minus master bias exceeds the median of
# that difference by this many times its noise (robust: median absolute
# deviation).
HOT_PIXEL_SIGMA = 5.0

_SERIES_INDEX_RE = re.compile(r"-\d+$")

# Casiers searched, in this order, for a shared input key.
_CASIERS = ("scheduler_data", "sequencer_data", "reduction_data", "sharing_data", "archive_data")


def find_shared_input(obs: Observation, key: str) -> Any:
    """The value stored under a shared, purpose-named key (e.g.
    "bias_images"), whichever casier holds it (see CLAUDE.md, "Shared,
    purpose-named keys"). Raises RuntimeError naming the key when no casier
    holds it, or when it is empty."""
    for casier in _CASIERS:
        value = getattr(obs, casier).get(key)
        if value is not None:
            if not value:
                raise RuntimeError(f"Shared input '{key}' is empty")
            return value
    raise RuntimeError(f"Shared input '{key}' not found in any casier -- the step producing it did not run")


def series_root(files: list[str]) -> str:
    """A raw series' common name: the first frame's name minus its "-<N>"
    index and extension -- e.g. "BIAS_..._120000-1.fits" -> "BIAS_..._120000"."""
    return _SERIES_INDEX_RE.sub("", Path(files[0]).stem)


def master_filename(files: list[str]) -> str:
    """Master file name for a series: series_root() plus "_master.fits" --
    e.g. "BIAS_..._120000-1.fits" -> "BIAS_..._120000_master.fits"."""
    return f"{series_root(files)}_master.fits"


def minmax_mean(stack: np.ndarray) -> np.ndarray:
    """Per-pixel mean of a (N, height, width) stack after rejecting, for each
    pixel, its highest and its lowest value. Computed in float64. Raises
    ValueError below MIN_FRAMES frames."""
    if stack.shape[0] < MIN_FRAMES:
        raise ValueError(f"{stack.shape[0]} frame(s), at least {MIN_FRAMES} needed for a min/max-rejected mean")
    return np.sort(stack.astype(np.float64), axis=0)[1:-1].mean(axis=0)


def load_series(directory: Path, files: list[str]) -> tuple[np.ndarray, fits.Header]:
    """The frames of a series as one float64 (N, height, width) stack, and a
    copy of the first frame's header."""
    stack = np.stack([fits.getdata(directory / name).astype(np.float64) for name in files])
    header = fits.getheader(directory / files[0]).copy()
    return stack, header


def write_float_image(path: Path, data: np.ndarray, header: fits.Header, comments: list[str]) -> None:
    """Writes a computed image as float32, keeping the header of the raw frame
    it comes from plus one COMMENT card per line of comments. BZERO/BSCALE/
    BLANK, inherited from the camera's integer frames, are removed: they
    describe an integer encoding the float data no longer uses."""
    header = header.copy()
    for key in ("BZERO", "BSCALE", "BLANK"):
        header.remove(key, ignore_missing=True, remove_all=True)
    for line in comments:
        header["COMMENT"] = line
    fits.writeto(path, data.astype(np.float32), header, overwrite=True)


def resolve_master(
    obs: Observation,
    input_key: str,
    label: str,
    finish: Callable[[np.ndarray, list[str]], np.ndarray] | None = None,
) -> tuple[str, bool]:
    """Returns the master of the series under input_key (e.g. "bias_images")
    as (file name, reused). Reused when its file already exists in the
    session directory; otherwise combined (min/max-rejected mean), passed to
    finish(data, comments) when given -- which may change the data and append
    comment lines -- and written. label names the frame type in the header
    (e.g. "Master bias")."""
    if not obs.obs_dir:
        raise RuntimeError("Observation directory (obs_dir) not set")
    directory = Path(obs.obs_dir)
    files = find_shared_input(obs, input_key)
    name = master_filename(files)
    if (directory / name).exists():
        return name, True

    stack, header = load_series(directory, files)
    data = minmax_mean(stack)
    exposure = header.get("EXPTIME", 0.0)
    comments = [
        f"{label}: {len(files)}x{exposure:g}s frames",
        "Per-pixel mean after rejecting the highest and lowest value",
    ]
    comments += [f"Source: {f}" for f in files]
    if finish is not None:
        data = finish(data, comments)
    write_float_image(directory / name, data, header, comments)
    return name, False


def intermediate_dir(obs: Observation) -> Path:
    """The observation's directory of intermediate files, created if needed:
    "<queried name>_<observation id>" inside the session directory (e.g.
    "Vega_0e37017f"), the name made file-safe by file_names.safe_name() (same
    rule as every other file named after the star). Every intermediate file of the
    observation goes there; deleting it loses nothing, everything can be
    rebuilt from the raw frames."""
    if not obs.obs_dir:
        raise RuntimeError("Observation directory (obs_dir) not set")
    name = safe_name(obs.scheduler_data.get("queried_name") or "")
    if not name:
        raise RuntimeError("No usable queried_name to name the intermediate-file directory")
    directory = Path(obs.obs_dir) / f"{name}_{obs.id}"
    directory.mkdir(exist_ok=True)
    return directory


def flat_band(flat: np.ndarray) -> tuple[int, int]:
    """First and last row of the band lit through the slit, from the master
    flat: rows whose median reaches LIT_BAND_LEVEL_FRACTION of the brightest
    row's median."""
    level = np.median(flat, axis=1)
    lit = np.nonzero(level >= LIT_BAND_LEVEL_FRACTION * level.max())[0]
    return int(lit[0]), int(lit[-1])


def find_trace(image: np.ndarray, flat: np.ndarray) -> FeatureAxes | None:
    """Principal axes of the spectrum trace (find_axes()), measured on the
    band lit through the slit only (flat_band()), dead pixels (flat below
    MIN_FLAT_FOR_MEASUREMENT) set to 0; cx/cy in full-frame coordinates.
    Outside the band the preprocessed frames are 0: left in, they would make
    find_axes() see a zero background and take the whole lit band for the
    trace. None when no trace is found.

    image is a preprocessed (flat-divided) frame or sum; the measurement is
    made on image x flat, i.e. before the flat division, in memory only. The
    flat is faint at the blue end (about 0.2 against 3.6 in the red on the
    72ED), so dividing by it amplifies the noise there 5 to 20 times: on a
    faint object those amplified noise patches pass find_axes()'s threshold
    and outweigh the trace (M 57: -7.4 deg instead of -0.07). The geometry of
    the trace needs no flat correction."""
    band_low, band_high = flat_band(flat)
    band = slice(band_low, band_high + 1)
    axes = find_axes(np.where(flat[band] < MIN_FLAT_FOR_MEASUREMENT, 0.0, image[band] * flat[band]))
    if axes is None:
        return None
    return dataclasses.replace(axes, cy=axes.cy + band_low)


def hot_pixel_map(dark: np.ndarray, bias: np.ndarray) -> tuple[np.ndarray, float]:
    """Hot pixels of the sensor: where master dark - master bias exceeds its
    median by HOT_PIXEL_SIGMA times its noise (1.4826 x the median absolute
    deviation, which the hot pixels themselves barely move). Returns the mask
    and the threshold, in ADU of dark - bias. A noiseless difference gives no
    hot pixel."""
    diff = dark - bias
    median = float(np.median(diff))
    noise = 1.4826 * float(np.median(np.abs(diff - median)))
    threshold = median + HOT_PIXEL_SIGMA * noise
    return diff > threshold, threshold


def replace_hot_pixels(data: np.ndarray, hot: np.ndarray, good: np.ndarray) -> np.ndarray:
    """Replaces each hot pixel inside the good area by a linear interpolation,
    along its row (the dispersion axis), between the nearest healthy pixels
    (good and not hot) on its left and right: the mean of both neighbours for
    an isolated pixel, the nearest healthy value at an edge. Along the row,
    because across it the trace profile is narrow and a vertical neighbour
    would differ from the pixel itself. A row without any healthy pixel is
    left as is. Returns a new array."""
    result = data.copy()
    to_fix = hot & good
    healthy = good & ~hot
    for row in np.flatnonzero(to_fix.any(axis=1)):
        source = np.flatnonzero(healthy[row])
        if source.size == 0:
            continue
        target = np.flatnonzero(to_fix[row])
        result[row, target] = np.interp(target, source, data[row, source])
    return result


def preprocess_frames(obs: Observation, input_key: str) -> tuple[list[str], Path, int]:
    """Corrects each raw frame under input_key (e.g. "star_spectra_images")
    with the session's masters: (frame - master bias) / master flat, frame by
    frame (no stacking). The dark is not subtracted, but the hot pixels it
    shows (hot_pixel_map()) are replaced by their row neighbours
    (replace_hot_pixels()) -- the same pixels in every frame. Only the useful area is
    kept: outside the band lit through the slit (flat_band()), and where the
    master flat is below MIN_FLAT_FOR_MEASUREMENT (dead sensor edges), the
    result is 0 -- so no inf, NaN or huge value reaches the next steps. Each result
    is written to intermediate_dir(obs) as "<raw name>_preproc.fits", float32,
    with the raw frame's header plus COMMENT lines naming the masters.
    Returns the written paths, relative to the session directory, the
    intermediate directory and the number of hot pixels replaced in each frame."""
    session_dir = Path(obs.obs_dir or "")
    files = find_shared_input(obs, input_key)
    bias_name = find_shared_input(obs, "master_bias_image")
    dark_name = find_shared_input(obs, "master_dark_image")
    flat_name = find_shared_input(obs, "master_flat_image")
    out_dir = intermediate_dir(obs)

    bias = fits.getdata(session_dir / bias_name).astype(np.float64)
    dark = fits.getdata(session_dir / dark_name).astype(np.float64)
    flat = fits.getdata(session_dir / flat_name).astype(np.float64)
    if bias.shape != flat.shape:
        raise RuntimeError(f"Master bias '{bias_name}' is {bias.shape}, master flat '{flat_name}' is {flat.shape}")
    if bias.shape != dark.shape:
        raise RuntimeError(f"Master bias '{bias_name}' is {bias.shape}, master dark '{dark_name}' is {dark.shape}")
    band_low, band_high = flat_band(flat)
    rows = np.arange(flat.shape[0])[:, None]
    flat_usable = (flat >= MIN_FLAT_FOR_MEASUREMENT) & (rows >= band_low) & (rows <= band_high)
    hot, threshold = hot_pixel_map(dark, bias)
    hot_count = int((hot & flat_usable).sum())
    comments = [
        f"Master bias subtracted: {bias_name}",
        f"Divided by master flat: {flat_name}",
        f"Dark not subtracted; hot pixels replaced: {hot_count} (dark - bias > {threshold:.0f} ADU)",
        f"Hot pixel map from: {dark_name}",
    ]

    written = []
    for name in files:
        raw = fits.getdata(session_dir / name).astype(np.float64)
        if raw.shape != bias.shape:
            raise RuntimeError(f"'{name}' is {raw.shape}, the masters are {bias.shape}")
        data = np.divide(raw - bias, flat, out=np.zeros_like(raw), where=flat_usable)
        data = replace_hot_pixels(data, hot, flat_usable)
        out_path = out_dir / f"{Path(name).stem}_preproc.fits"
        write_float_image(out_path, data, fits.getheader(session_dir / name), comments)
        written.append(str(out_path.relative_to(session_dir)))
    return written, out_dir, hot_count


def shift_columns(data: np.ndarray, shifts: np.ndarray) -> np.ndarray:
    """Shifts each column x of data by shifts[x] pixels along Y (upwards when
    positive), by overlap redistribution: moved by k + f pixels (k whole, f in
    [0, 1)), each original pixel overlaps two new pixels, the first by 1 - f,
    the second by f, and gives each that fraction of its value -- so nothing
    is created or lost and each column's total is kept exactly, except what
    leaves through the top or bottom edge. Pixels entering from an edge are 0.
    Whole-array numpy operations, no loop over pixels."""
    height, width = data.shape
    whole = np.floor(shifts).astype(int)
    fraction = shifts - whole
    rows = np.arange(height)[:, None]
    cols = np.arange(width)[None, :]
    result = np.zeros(data.shape, dtype=np.float64)
    for source, weight in ((rows - whole, 1.0 - fraction), (rows - whole - 1, fraction)):
        inside = (source >= 0) & (source < height)
        values = data[np.clip(source, 0, height - 1), cols]
        result += np.where(inside, values * weight, 0.0)
    return result



def shift_rows(data: np.ndarray, shifts: np.ndarray) -> np.ndarray:
    """Shifts each row y of data by shifts[y] pixels along X (to the right
    when positive), by the same overlap redistribution as shift_columns():
    each row's total is kept exactly, except what leaves through the left or
    right edge."""
    return shift_columns(data.T, shifts).T

def transform_frames(
    obs: Observation,
    files: list[str],
    suffix: str,
    transform: Callable[[np.ndarray], np.ndarray],
    comments: list[str],
) -> list[str]:
    """Applies transform to each image (paths relative to the session
    directory) and writes the result to intermediate_dir(obs) as
    "<name>_<suffix>.fits", float32, with the image's own header plus the
    given COMMENT lines. Returns the written paths, relative to the session
    directory."""
    session_dir = Path(obs.obs_dir or "")
    out_dir = intermediate_dir(obs)
    written = []
    for name in files:
        data = fits.getdata(session_dir / name).astype(np.float64)
        out_path = out_dir / f"{Path(name).stem}_{suffix}.fits"
        write_float_image(out_path, transform(data), fits.getheader(session_dir / name), comments)
        written.append(str(out_path.relative_to(session_dir)))
    return written



def read_law(path: Path) -> dict:
    """A dispersion law file (written by dispersion_law): {"unit",
    "coefficients" (A0..An), "comments" (its header comment lines, without
    the leading "# ")}."""
    text = Path(path).read_text()
    data = yaml.safe_load(text)
    coefficients = []
    while f"A{len(coefficients)}" in data:
        coefficients.append(float(data[f"A{len(coefficients)}"]))
    comments = [line[1:].strip() for line in text.splitlines() if line.startswith("#")]
    return {"unit": data.get("unit", "A"), "coefficients": coefficients, "comments": comments}


def resample_linear(spectrum: np.ndarray, law: Polynomial, start: float, step: float, count: int) -> np.ndarray:
    """Resamples a spectrum given per pixel onto count points of constant
    step (point k centred on start + k * step), by overlap redistribution:
    pixel x covers [law(x - 0.5), law(x + 0.5)], its flux spread evenly over
    that interval, and each new interval receives the part of every pixel's
    flux that falls inside it -- so the total is kept, except what falls
    outside the new grid. Built on the cumulative flux at the pixel edges,
    interpolated linearly: no loop over pixels. Raises ValueError when law
    does not increase across the pixels."""
    edges = law(np.arange(spectrum.size + 1) - 0.5)
    if np.any(np.diff(edges) <= 0):
        raise ValueError("The dispersion law does not increase across the spectrum")
    cumulative = np.concatenate(([0.0], np.cumsum(np.asarray(spectrum, dtype=np.float64))))
    new_edges = start + (np.arange(count + 1) - 0.5) * step
    return np.diff(np.interp(new_edges, edges, cumulative))


def wavelength_axis(header: fits.Header) -> np.ndarray:
    """Wavelength of every point of a 1D spectrum with a linear axis:
    CRVAL1 + (i + 1 - CRPIX1) * CDELT1, CRPIX1 being 1 when absent."""
    count = header["NAXIS1"]
    return header["CRVAL1"] + (np.arange(count) + 1 - header.get("CRPIX1", 1)) * header["CDELT1"]


def transform_spectrum(
    obs: Observation,
    input_key: str,
    suffix: str,
    transform: Callable[[np.ndarray, fits.Header], tuple[np.ndarray, fits.Header]],
) -> str:
    """Reads the 1D spectrum under input_key (a path relative to the session
    directory), applies transform(data, header) -> (data, header) and writes
    the result to intermediate_dir(obs) as "<name>_<suffix>.fits". Returns
    its path, relative to the session directory."""
    session_dir = Path(obs.obs_dir or "")
    name = find_shared_input(obs, input_key)
    data, header = fits.getdata(session_dir / name, header=True)
    data, header = transform(data.astype(np.float64), header.copy())
    out_path = intermediate_dir(obs) / f"{Path(name).stem}_{suffix}.fits"
    write_float_image(out_path, data, header, [])
    return str(out_path.relative_to(session_dir))


_LAW_COVERAGE_RE = re.compile(r"valid between ([-+0-9.eE]+) and ([-+0-9.eE]+)")

# Balmer lines, air wavelengths in angstroms, for the spectrum plots
BALMER_LINES_A = {"Hα": 6562.79, "Hβ": 4861.35, "Hγ": 4340.47, "Hδ": 4101.74, "Hε": 3970.07, "H8": 3889.05}


def law_coverage(law_file: Path) -> tuple[float, float] | None:
    """The range covered by the reference lines of a dispersion law file
    ("valid between X and Y" in its header comments), or None when the file
    or the comment is missing."""
    try:
        comments = read_law(law_file)["comments"]
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return None
    for line in comments:
        match = _LAW_COVERAGE_RE.search(line)
        if match:
            return float(match.group(1)), float(match.group(2))
    return None


def final_name(obs: Observation) -> str:
    """Common name of the observation's delivered files (final spectrum,
    plots, summary): the observation file's name without extension, plus the
    observation ID -- e.g. "20260925T213751_16_Peg_ba79e32c"."""
    return f"{Path(obs.yaml_file or obs.suggested_filename).stem}_{obs.id}"


def draw_spectrum(axes, wavelengths: np.ndarray, data: np.ndarray, coverage: tuple[float, float] | None,
                  balmer: bool, unit: str = "Angstrom", extra_lines: dict[str, float] | None = None) -> None:
    """Draws a 1D spectrum on matplotlib axes: the curve, the parts outside
    the dispersion law's coverage shaded as extrapolation, the Balmer lines
    when asked and any extra_lines (name -> wavelength, dotted green; angstrom
    spectra only). Plain matplotlib axes, never pyplot, whose global state is
    not meant for the post-observation thread."""
    axes.plot(wavelengths, data, lw=0.8, color="#1d3c6e")
    axes.set_xlim(wavelengths[0], wavelengths[-1])
    top = float(np.nanmax(data)) if data.size else 1.0
    axes.set_ylim(bottom=min(0.0, float(np.nanmin(data))), top=top * 1.1)
    if coverage:
        for low, high in ((wavelengths[0], coverage[0]), (coverage[1], wavelengths[-1])):
            if high > low:
                axes.axvspan(low, high, color="#f3e0c0", alpha=0.6, lw=0)
        if coverage[0] > wavelengths[0] or coverage[1] < wavelengths[-1]:
            axes.text(0.995, 0.02, "shaded: extrapolated", transform=axes.transAxes, ha="right",
                      va="bottom", fontsize=7, color="#8a6d3b")
    if balmer and unit == "Angstrom":
        for name, line in BALMER_LINES_A.items():
            if wavelengths[0] <= line <= wavelengths[-1]:
                axes.axvline(line, color="#c0392b", ls="--", lw=0.7)
                axes.text(line, top * 1.03, name, color="#c0392b", ha="center", fontsize=8)
    if extra_lines and unit == "Angstrom":
        for name, line in extra_lines.items():
            if wavelengths[0] <= line <= wavelengths[-1]:
                axes.axvline(line, color="#27864a", ls=":", lw=0.8)
                axes.text(line, top * 0.97, name, color="#27864a", ha="right", va="top", fontsize=6.5, rotation=90)
    axes.set_xlabel(f"Wavelength ({unit}, air)")
    axes.set_ylabel("Relative flux")
    axes.grid(alpha=0.25)



SPECTRUM_PANEL = "spectre1d"


async def publish_spectrum_preview(obs: Observation, spectrum_path: Path) -> None:
    """Shows a 1D spectrum on the GUI's "Spectre 1D" panel, the same way
    acquisition_helpers.publish_guide_preview() shows a guide image: copies it
    to GUI_PREVIEW_DIR/"spectre1d.fits" and notifies connected clients. Its
    overlay -- title (object and observation ID, shown in the panel's title
    bar), dispersion-law coverage (the rest is drawn as
    extrapolated) and Balmer lines within the spectrum, angstrom spectra only
    -- is sent with the message and also written next to the file,
    "spectre1d.json", so a reloaded page finds it again (GET /images/{panel}/overlay)."""
    header = fits.getheader(spectrum_path)
    wavelengths = wavelength_axis(header)
    low, high = float(np.min(wavelengths)), float(np.max(wavelengths))

    session_dir = Path(obs.obs_dir or "")
    law_name = obs.reduction_data.get("dispersion_law_file")
    coverage = law_coverage(session_dir / law_name) if law_name else None
    lines = []
    if header.get("CUNIT1", "Angstrom") == "Angstrom":
        lines = [{"name": name, "wavelength": line} for name, line in BALMER_LINES_A.items() if low <= line <= high]

    name = obs.scheduler_data.get("queried_name") or header.get("OBJNAME", "")
    overlay = {
        "title": " — ".join(p for p in (name, obs.id) if p),
        "coverage": list(coverage) if coverage else None,
        "lines": lines,
    }

    GUI_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(spectrum_path, GUI_PREVIEW_DIR / f"{SPECTRUM_PANEL}.fits")
    (GUI_PREVIEW_DIR / f"{SPECTRUM_PANEL}.json").write_text(json.dumps(overlay, ensure_ascii=False))
    await ws_image_broadcaster.broadcast(SPECTRUM_PANEL, source="write_spectrum", overlay=overlay)
