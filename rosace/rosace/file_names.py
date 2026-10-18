# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The one rule turning a star or site name into a file-name fragment, used
wherever such a name enters a file or directory name (observation file,
raw FITS frames, intermediate directory, Demetra project, session
directory) — so every file of an observation spells the name the same way.
"""

from __future__ import annotations

import re
from pathlib import Path

_SPACES = re.compile(r"\s+")
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]")
_REPEATED_UNDERSCORES = re.compile(r"_{2,}")


def safe_name(name: str) -> str:
    """The name as a file-name fragment: each run of spaces becomes "_",
    every character outside [A-Za-z0-9_-] is removed (Simbad's "*" among
    them: a shell wildcard, and forbidden in Windows file names), repeated
    "_" collapse into one and leading/trailing "_" are dropped:
    "*   8 Lac A" -> "8_Lac_A", "V/380 Cyg" -> "V380_Cyg". Returns "" when
    nothing usable is left; each caller decides what that means."""
    name = _UNSAFE_CHARS.sub("", _SPACES.sub("_", name))
    return _REPEATED_UNDERSCORES.sub("_", name).strip("_")


def final_name(obs) -> str:
    """Common name of the observation's delivered files (final spectrum,
    plots, summary, PHD2 guide log): the observation file's name without
    extension, plus the observation ID -- e.g. "20260925T213751_16_Peg_ba79e32c"."""
    return f"{Path(obs.yaml_file or obs.suggested_filename).stem}_{obs.id}"


def validation_file_name(final_spectrum: str, part: str) -> str:
    """Name of one file of the BeSS validation page, next to the final
    spectrum in the session directory: part "data" -> "<final>_validation.json",
    "guiding"/"frames" -> "<final>_validation_<part>.png". Written by the
    bess_validation REDUCTION step, read by the BeSS validation API."""
    stem = Path(final_spectrum).stem
    return f"{stem}_validation.json" if part == "data" else f"{stem}_validation_{part}.png"
