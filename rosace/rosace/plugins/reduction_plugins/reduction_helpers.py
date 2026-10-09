"""
Shared functions of the native reduction chain: finding a step's input under
its shared key, writing float images, the observation's intermediate-file
directory, the session's master calibration frames (bias, dark, flat) and
the preprocessing of raw frames with them.

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
import re
from pathlib import Path
from typing import Any, Callable

import numpy as np
from astropy.io import fits

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

_SERIES_INDEX_RE = re.compile(r"-\d+$")
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")

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
    "Vega_0e37017f"), the name stripped of anything but letters, digits, "_"
    and "-" (same rule as FITS file names). Every intermediate file of the
    observation goes there; deleting it loses nothing, everything can be
    rebuilt from the raw frames."""
    if not obs.obs_dir:
        raise RuntimeError("Observation directory (obs_dir) not set")
    name = _UNSAFE_NAME_CHARS.sub("", obs.scheduler_data.get("queried_name") or "")
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
    trace. None when no trace is found."""
    band_low, band_high = flat_band(flat)
    band = slice(band_low, band_high + 1)
    axes = find_axes(np.where(flat[band] < MIN_FLAT_FOR_MEASUREMENT, 0.0, image[band]))
    if axes is None:
        return None
    return dataclasses.replace(axes, cy=axes.cy + band_low)


def preprocess_frames(obs: Observation, input_key: str) -> tuple[list[str], Path]:
    """Corrects each raw frame under input_key (e.g. "star_spectra_images")
    with the session's masters: (frame - master bias) / master flat, frame by
    frame (no stacking). The dark is not subtracted. Only the useful area is
    kept: outside the band lit through the slit (flat_band()), and where the
    master flat is below MIN_FLAT_FOR_MEASUREMENT (dead sensor edges), the
    result is 0 -- so no inf, NaN or huge value reaches the next steps. Each result
    is written to intermediate_dir(obs) as "<raw name>_preproc.fits", float32,
    with the raw frame's header plus COMMENT lines naming both masters.
    Returns the written paths, relative to the session directory, and the
    intermediate directory."""
    session_dir = Path(obs.obs_dir or "")
    files = find_shared_input(obs, input_key)
    bias_name = find_shared_input(obs, "master_bias_image")
    flat_name = find_shared_input(obs, "master_flat_image")
    out_dir = intermediate_dir(obs)

    bias = fits.getdata(session_dir / bias_name).astype(np.float64)
    flat = fits.getdata(session_dir / flat_name).astype(np.float64)
    if bias.shape != flat.shape:
        raise RuntimeError(f"Master bias '{bias_name}' is {bias.shape}, master flat '{flat_name}' is {flat.shape}")
    band_low, band_high = flat_band(flat)
    rows = np.arange(flat.shape[0])[:, None]
    flat_usable = (flat >= MIN_FLAT_FOR_MEASUREMENT) & (rows >= band_low) & (rows <= band_high)
    comments = [
        f"Master bias subtracted: {bias_name}",
        f"Divided by master flat: {flat_name}",
        "Dark not subtracted",
    ]

    written = []
    for name in files:
        raw = fits.getdata(session_dir / name).astype(np.float64)
        if raw.shape != bias.shape:
            raise RuntimeError(f"'{name}' is {raw.shape}, the masters are {bias.shape}")
        data = np.divide(raw - bias, flat, out=np.zeros_like(raw), where=flat_usable)
        out_path = out_dir / f"{Path(name).stem}_preproc.fits"
        write_float_image(out_path, data, fits.getheader(session_dir / name), comments)
        written.append(str(out_path.relative_to(session_dir)))
    return written, out_dir


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

