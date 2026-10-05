"""
Shared helpers for DARK acquisition and its temperature-bucketed library
(see plugins/sequencer_plugins/dark_acquisition/, plugins/camera_cooldown.py)
-- plain functions, not a sub-plugin of their own, same status as
acquisition_helpers.py: not resolved by pipeline_loader.py's dynamic
importlib convention, no program YAML ever declares this module as a step.

find_usable_frame_series() (below) is also reused by
plugins/sequencer_plugins/bias_acquisition/ for its current_session/camera
lookup -- BIAS has no library, so temperature_bucket_dirname()/
copy_darks_from_library() stay DARK-only.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from astropy.io import fits

_SERIES_INDEX_RE = re.compile(r"-\d+$")

# Fixed range of temperature buckets pre-created by ensure_dark_library_structure()
# below -- deliberately not a conf/config.yaml key: a couple of extra empty
# directories cost nothing, so a wide-enough fixed range removes the need for a
# config knob entirely. User-confirmed bounds, 2026-08-15.
DARK_LIBRARY_MIN_TEMP_C = -20
DARK_LIBRARY_MAX_TEMP_C = 0
# Follows the step of the session target temperature
# (plugins/camera_cooldown.py::TARGET_TEMPERATURE_STEP_C), so a session's darks
# land in one bucket -- a convention, keep both equal.
DARK_LIBRARY_TEMP_STEP_C = 5


def temperature_bucket_dirname(temp_c: float) -> str:
    """Dark-library subdirectory name for a given (already 5-rounded)
    temperature -- e.g. -5 -> "TempMinus5degrees", 0 -> "Temp0degrees",
    5 -> "TempPlus5degrees". Never a leading "-" in the name: a literal
    minus sign at the start of a path segment is best avoided (shell
    globbing, some tools mistaking it for an option flag)."""
    temp = round(temp_c)
    if temp < 0:
        return f"TempMinus{abs(temp)}degrees"
    if temp > 0:
        return f"TempPlus{temp}degrees"
    return "Temp0degrees"


_TEMP_TOKEN_RE = re.compile(r"_(-?\d+)deg_")

# Real-world tolerances: a session restart can recompute a slightly different
# target temperature (see compute_camera_target_temperature()'s own safety
# floor in plugins/camera_cooldown.py, added for the same underlying reason), and a real camera
# driver doesn't guarantee an EXPTIME of exactly the requested duration to
# the millisecond. Without these, a perfectly usable series could be
# rejected and reshot for no real reason -- found in practice on a first
# real-sky test. Both user-confirmed values, not yet exposed as config.yaml
# keys (see DARK_LIBRARY_MIN_TEMP_C etc. above for the same reasoning).
TEMPERATURE_TOLERANCE_DEG_C = 2.0
EXPOSURE_TIME_TOLERANCE_SECONDS = 1.0


def find_usable_frame_series(
    directory: Path,
    *,
    image_type: str,
    temp_c: float,
    count_required: int,
    exposure_time_required: float,
    frame_size: tuple[int, int],
) -> list[str] | None:
    """Looks for a usable DARK or BIAS series in `directory` (the session
    directory, or -- DARK only -- one dark-library temperature bucket, same
    rule either way, see CLAUDE.md's dark_acquisition section).

    A series is every file of this image_type ("DARK" or "BIAS") sharing the
    same root name -- the filename minus the trailing "-<N>" index each
    capture appends (see summarize_series_root_name()). Only files within
    TEMPERATURE_TOLERANCE_DEG_C of this temperature count (parsed from the
    filename convention build_fits_root_name() already writes, e.g.
    "..._-5deg_...", see acquisition_helpers.py -- not read from the FITS
    header, which carries no temperature card of its own). Series are tried
    newest first (capture timestamp embedded in the root name); the first one
    with at least count_required files whose first file's FITS header (read
    alone, not the pixel data) reports the same frame size (NAXIS1/NAXIS2)
    as frame_size (the camera's *currently configured* CCD_FRAME, sub-framed
    or not -- see hardware/camera.py::Camera.get_frame_size(), not the
    sensor's maximum capability) and an EXPTIME at least
    exposure_time_required minus EXPOSURE_TIME_TOLERANCE_SECONDS wins.
    Returns its first count_required filenames (in capture order), or None
    if no series qualifies. Opens at most one header per series.

    Each series is judged on its own: one that doesn't qualify -- cut short
    by a stop request, or shot at another frame size -- is skipped rather
    than hiding every other usable series behind it (see HISTORY.md,
    2026-09-27)."""
    if not directory.is_dir():
        return None

    candidates = []
    for p in directory.glob(f"{image_type}_*.fits"):
        match = _TEMP_TOKEN_RE.search(p.name)
        if match is None:
            continue
        if abs(int(match.group(1)) - temp_c) <= TEMPERATURE_TOLERANCE_DEG_C:
            candidates.append(p)

    return pick_most_recent_usable_series(
        candidates, count_required=count_required,
        exposure_time_required=exposure_time_required, frame_size=frame_size,
    )


def pick_most_recent_usable_series(
    candidates: list[Path],
    *,
    count_required: int,
    exposure_time_required: float,
    frame_size: tuple[int, int],
) -> list[str] | None:
    """Groups candidate files into series (same root name, minus the
    trailing "-<N>" index) and returns the first count_required filenames,
    in capture order, of the most recent series (capture timestamp embedded
    in the root name) that has at least count_required files and whose first
    file's FITS header reports this frame size and an EXPTIME at least
    exposure_time_required minus EXPOSURE_TIME_TOLERANCE_SECONDS -- or None.
    Shared by the DARK/BIAS lookup above and flat_acquisition, which only
    differ in how they pick their candidates. Opens at most one header per
    series."""
    series: dict[str, list[Path]] = {}
    for p in candidates:
        series.setdefault(_SERIES_INDEX_RE.sub("", p.stem), []).append(p)

    for root in sorted(series, key=_series_sort_key, reverse=True):
        files = sorted(series[root], key=_series_index)
        if len(files) < count_required:
            continue
        selected = files[:count_required]
        header = fits.getheader(selected[0])
        if (header.get("NAXIS1"), header.get("NAXIS2")) != tuple(frame_size):
            continue
        exptime = header.get("EXPTIME")
        if exptime is None or exptime < exposure_time_required - EXPOSURE_TIME_TOLERANCE_SECONDS:
            continue
        return [p.name for p in selected]

    return None


_TIMESTAMP_TOKEN_RE = re.compile(r"_(\d{8}_\d{6})")
_SERIES_INDEX_NUMBER_RE = re.compile(r"-(\d+)$")


def _series_sort_key(root: str) -> tuple[str, str]:
    """Chronological key for a series root name: its embedded capture
    timestamp (build_fits_root_name()'s YYYYMMDD_HHMMSS token), the root name
    itself as a tie-breaker. A root without a timestamp sorts oldest."""
    match = _TIMESTAMP_TOKEN_RE.search(root)
    return (match.group(1) if match else "", root)


def _series_index(path: Path) -> int:
    """Per-file index within a series ("-3" -> 3), numeric so "-10" comes
    after "-9"; 0 for a single-file series with no index suffix."""
    match = _SERIES_INDEX_NUMBER_RE.search(path.stem)
    return int(match.group(1)) if match else 0


def copy_darks_from_library(library_dir: Path, filenames: list[str], dest_dir: Path) -> list[str]:
    """Copies each named file from library_dir into dest_dir (the session
    directory -- every session image must live there, see CLAUDE.md's
    on-disk layout). One-way only: nothing in this module ever writes into
    the library itself, that stays a manual transfer by the observer."""
    for name in filenames:
        shutil.copy2(library_dir / name, dest_dir / name)
    return list(filenames)


def summarize_series_root_name(files: list[str]) -> str:
    """Human-readable stand-in for a whole DARK/BIAS series in a single log
    line, instead of listing every filename (found too noisy in practice --
    a 5-file series already makes for an unreadable log line). Takes the
    first file's name and replaces its trailing per-file series index (the
    "-<N>" build_fits_root_name() appends when a series has more than one
    file, see its own docstring) with "-xxx" -- e.g.
    "DARK_-15deg_5x300s_20260815_130137-1.fits" ->
    "DARK_-15deg_5x300s_20260815_130137-xxx.fits". A single-file series (no
    index suffix at all) is returned unchanged."""
    if not files:
        return ""
    stem = files[0].removesuffix(".fits")
    return f"{_SERIES_INDEX_RE.sub('-xxx', stem)}.fits"


def ensure_dark_library_structure(library_dir: Path) -> None:
    """Creates library_dir and one empty subdirectory per temperature bucket
    from DARK_LIBRARY_MIN_TEMP_C to DARK_LIBRARY_MAX_TEMP_C (step
    DARK_LIBRARY_TEMP_STEP_C), if they don't already exist -- so the observer
    can see where to transfer darks by hand. Called by dark_acquisition.py at
    every execution, the one sub-plugin that uses the library. Uses
    temperature_bucket_dirname() for every name, the exact same function
    dark_acquisition.py itself calls to look a bucket up, so the two can
    never name a bucket differently. Safe to call every time:
    mkdir(..., exist_ok=True) never touches a directory that already exists,
    so it never disturbs darks a manual transfer already put there."""
    for temp in range(DARK_LIBRARY_MIN_TEMP_C, DARK_LIBRARY_MAX_TEMP_C + 1, DARK_LIBRARY_TEMP_STEP_C):
        (library_dir / temperature_bucket_dirname(temp)).mkdir(parents=True, exist_ok=True)
