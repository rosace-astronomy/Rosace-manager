"""
REDUCTION sub-plugin — Spectrum stacking
Sums the 1D spectra of the target series, and those of the lamp series, into one spectrum each.

For each series -- target (shared key extract_target_spectra), then lamp
(extract_calib_spectra) -- the flux of every spectrum is the sum of its
values. A spectrum below MIN_FLUX_FRACTION of the brightest spectrum's flux
(a cloud, a frame without starlight, a lamp that did not light up) is
rejected with a warning -- the brightest, not the median: once half the
series is cloudy, the median is itself a cloudy frame and would reject
nothing. The others are summed as they are, without shifting (their
position comes from the slit) and without cosmic-ray rejection.

The stacked spectrum keeps the first retained spectrum's header, plus:
- DATE-OBS: start of the first retained exposure; DATE-END: end of the last;
- EXPTIME: DATE-END - DATE-OBS, i.e. the duration of the whole observation,
  readouts and pauses included -- the one exposure time BeSS defines;
- EXPTIME2: the exposures themselves, from each frame's actual EXPTIME
  (e.g. "5x107.7s", one group per distinct duration: "2x20s+3x30s");
- COMMENT lines: the method, one "Source:" line per retained spectrum, one
  "Rejected" line per rejected spectrum (file names only).

Written to the observation's intermediate directory as the first spectrum's
name without its series index, plus "_stack.fits", under the shared keys
stack_target_spectrum / stack_calib_spectrum (one file each). The casier
reduction_data["stack"] only exists when a spectrum was rejected, and lists
them.
"""

from __future__ import annotations

DISPLAY_NAME = "Stack"

import logging
import re
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.time import Time, TimeDelta

from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import find_shared_input, intermediate_dir, write_float_image

log = logging.getLogger("spectro.subplugin.stack")

MIN_FLUX_FRACTION = 0.30

# The last "-<N>" series index of a file stem, followed by "_" or the end:
# "16Peg_..._213944-1_preproc_1d" -> "16Peg_..._213944_preproc_1d"
_LAST_SERIES_INDEX_RE = re.compile(r"-\d+(?=_[^-]*$|$)")


def stack_filename(first: str) -> str:
    """Stacked file name: the first spectrum's name without its series index,
    plus "_stack.fits"."""
    return f"{_LAST_SERIES_INDEX_RE.sub('', Path(first).stem)}_stack.fits"


def exposure_decomposition(exposures: list[float]) -> str:
    """The exposures as "<count>x<duration>s", one group per distinct duration
    (rounded to 0.1 s), in order of first appearance: "2x20s+3x30s"."""
    counts: dict[float, int] = {}
    for exposure in exposures:
        rounded = round(exposure, 1)
        counts[rounded] = counts.get(rounded, 0) + 1
    return "+".join(f"{count}x{duration:g}s" for duration, count in counts.items())


def _exposure_window(headers: list[fits.Header], files: list[str]) -> tuple[Time, Time]:
    """Start of the first exposure and end of the last, from each header's
    DATE-OBS and EXPTIME."""
    starts, ends = [], []
    for header, name in zip(headers, files):
        if "DATE-OBS" not in header or "EXPTIME" not in header:
            raise RuntimeError(f"STACK: '{name}' has no DATE-OBS or no EXPTIME in its header")
        start = Time(header["DATE-OBS"], format="isot", scale="utc")
        starts.append(start)
        ends.append(start + TimeDelta(float(header["EXPTIME"]), format="sec"))
    return min(starts), max(ends)


def stack_series(obs: Observation, input_key: str, label: str) -> tuple[str, list[dict]]:
    """Sums the 1D spectra under input_key (see module docstring) and writes
    the result. Returns its path, relative to the session directory, and the
    rejected spectra ({"file", "flux_fraction"})."""
    session_dir = Path(obs.obs_dir or "")
    files = find_shared_input(obs, input_key)
    spectra = [fits.getdata(session_dir / name).astype(np.float64) for name in files]
    headers = [fits.getheader(session_dir / name) for name in files]
    if len({s.shape for s in spectra}) > 1:
        raise RuntimeError(f"STACK: the {label.lower()} spectra have different lengths: {[s.shape for s in spectra]}")

    fluxes = np.array([s.sum() for s in spectra])
    brightest = float(fluxes.max())
    if brightest <= 0:
        raise RuntimeError(f"STACK: the {label.lower()} spectra carry no light (brightest flux {brightest:g})")
    kept, rejected = [], []
    for index, (name, flux) in enumerate(zip(files, fluxes)):
        fraction = float(flux) / brightest
        if fraction < MIN_FLUX_FRACTION:
            rejected.append({"file": name, "flux_fraction": round(fraction, 3)})
            log.warning(f"[STACK] {label} spectrum rejected, flux {fraction:.0%} of the brightest: {name}")
        else:
            kept.append(index)

    kept_files = [files[i] for i in kept]
    kept_headers = [headers[i] for i in kept]
    total = np.sum([spectra[i] for i in kept], axis=0)
    start, end = _exposure_window(kept_headers, kept_files)
    duration = round(float((end - start).to_value("sec")), 1)
    decomposition = exposure_decomposition([float(h["EXPTIME"]) for h in kept_headers])

    header = kept_headers[0].copy()
    header["DATE-OBS"] = (start.isot, "UTC start of the first exposure")
    header["DATE-END"] = (end.isot, "UTC end of the last exposure")
    header["EXPTIME"] = (duration, "Start of first to end of last exposure (s)")
    header["EXPTIME2"] = (decomposition, "Exposures summed")
    comments = [f"Sum of {len(kept)} 1D spectra"]
    # File names only: the sources sit in the same intermediate directory, and
    # a full path would not fit in one COMMENT card
    comments += [f"Source: {Path(name).name}" for name in kept_files]
    comments += [f"Rejected (flux {r['flux_fraction']:.0%} of the brightest):{Path(r['file']).name}" for r in rejected]

    out_path = intermediate_dir(obs) / stack_filename(files[0])
    write_float_image(out_path, total, header, comments)
    log.info(f"[STACK] {label}: {len(kept)}/{len(files)} spectra summed, {decomposition}, EXPTIME {duration:g} s")
    return str(out_path.relative_to(session_dir)), rejected


class StackPlugin(SubPlugin):
    """Sums the 1D spectra of the target series, and those of the lamp series, into one spectrum each."""

    async def execute(self, obs: Observation) -> Observation:
        target, target_rejected = stack_series(obs, "extract_target_spectra", "Target")
        calib, calib_rejected = stack_series(obs, "extract_calib_spectra", "Lamp")
        obs.reduction_data["stack_target_spectrum"] = target
        obs.reduction_data["stack_calib_spectrum"] = calib
        if target_rejected or calib_rejected:
            obs.reduction_data["stack"] = {"rejected": target_rejected + calib_rejected}
        return obs
