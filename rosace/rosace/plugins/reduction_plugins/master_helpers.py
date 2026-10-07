"""
Building the session's master calibration frames (bias, dark, flat) from a
raw series, and finding a step's input under its shared key.

Plain functions, not a sub-plugin: same status as axes_helpers.py -- not
resolved by pipeline_loader.py's dynamic importlib convention, no program
YAML ever declares this module as a step.

A master is combined pixel by pixel: for each pixel, its N values (one per
frame of the series) are sorted, the highest and the lowest are rejected,
and the remaining N-2 are averaged. At least MIN_FRAMES frames are needed.

A master is written to the session directory as "<series root>_master.fits"
(the root being the series' common name, minus each frame's "-<N>" index).
The file name decides reuse: when that file already exists it is reused
as is, otherwise it is computed -- so a master is computed once per session.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import numpy as np
from astropy.io import fits

from ...models import Observation

MIN_FRAMES = 3

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


def master_filename(files: list[str]) -> str:
    """Master file name for a series: the first frame's name minus its
    "-<N>" index, plus "_master.fits" -- e.g. "BIAS_..._120000-1.fits" ->
    "BIAS_..._120000_master.fits"."""
    stem = Path(files[0]).stem
    return f"{_SERIES_INDEX_RE.sub('', stem)}_master.fits"


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


def write_master(path: Path, data: np.ndarray, header: fits.Header, comments: list[str]) -> None:
    """Writes a master as float32, keeping the first frame's header plus one
    COMMENT card per line of comments. BZERO/BSCALE/BLANK, inherited from the
    camera's integer frames, are removed: they describe an integer encoding
    the float data no longer uses."""
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
    write_master(directory / name, data, header, comments)
    return name, False
