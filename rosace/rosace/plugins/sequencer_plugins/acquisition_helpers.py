"""
Shared acquisition helpers for real-hardware (sequencer_backend=indi)
SEQUENCER sub-plugins — plain functions, not a sub-plugin of their own: not
resolved by pipeline_loader.py's dynamic importlib convention, no program
YAML ever declares this module as a step. Each caller still owns its own
execute() and its own key in obs.sequencer_data; this module only factors
out logic that would otherwise be duplicated across sub-plugins.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ALLOWED_IMAGE_TYPES = {"STAR", "BIAS", "DARK", "FLAT", "CALIB", "GUIDING"}

_TEMP_ALLOWED_TYPES = {"DARK", "BIAS"}

# STAR's target_name *replaces* the literal "STAR" prefix (e.g. "V831Cas_...");
# GUIDING's *follows* the literal "GUIDING" prefix instead (e.g. "GUIDING_Vega_...")
# -- kept as its own type, not folded into STAR, since GUIDING's own image_type
# still needs to appear in the name (it's not itself the science target).
_TARGET_NAME_APPEND_TYPES = {"GUIDING"}

_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")


async def capture_guide_image(
    camera: Any,
    exposure_time: float,
    obs_dir: str,
    filename: str | None = None,
    ra_deg: float | None = None,
    dec_deg: float | None = None,
    target_name: str | None = None,
) -> Path:
    """Exposes a guiding-role camera for exposure_time seconds and writes the
    resulting FITS image to obs_dir/filename. Returns the written path. Same
    expose()-then-writeto() pattern expose.py already uses for the science
    camera (see hardware/camera.py for the generic Camera contract). filename
    defaults to build_fits_root_name("GUIDING", target_name=target_name,
    exposure_time=exposure_time) + ".fits" (e.g. "GUIDING_Vega_0_30s_....fits"
    when target_name is given, "GUIDING_0_30s_....fits" without it) — pass
    an explicit filename to override (e.g. in tests).

    ra_deg/dec_deg (J2000 degrees, must be given together or both omitted)
    and target_name, when given, are stamped into the image's own header
    (RA/DEC/EQUINOX/OBJECT keywords) before it's written — this is what
    makes the image self-sufficient for plate_solve_helper.py::solve_wcs()'s record_error,
    which reads exactly these keys back. Every stamped card's comment ends
    "stamped by Rosace" — the camera driver may already have written its
    own RA/DEC/OBJCTRA/OBJCTDEC/EQUINOX (e.g. via INDI's mount-snooping),
    but those aren't trusted to be true J2000 (INDI's EQUATORIAL_EOD_COORD
    is JNow by protocol — see CLAUDE.md's coordinate frame convention), so
    the comment is what lets a human reading the header later tell which
    value Rosace actually vouches for. Comments are kept short deliberately
    — a FITS card is 80 columns total, and a long target_name can already
    eat most of that budget on its own; astropy silently truncates an
    over-length comment rather than erroring, so a very long target_name
    degrades gracefully (comment cut short) instead of failing. All three
    (ra_deg/dec_deg/target_name) default to omitted, so an existing caller
    that doesn't pass them keeps writing a plain, unstamped image,
    unchanged."""
    if (ra_deg is None) != (dec_deg is None):
        raise ValueError("capture_guide_image: ra_deg and dec_deg must be given together, or both omitted")
    if filename is None:
        filename = build_fits_root_name("GUIDING", target_name=target_name, exposure_time=exposure_time) + ".fits"
    image = await camera.expose(exposure_time)
    if ra_deg is not None:
        image[0].header["RA"] = (ra_deg, "J2000, stamped by Rosace")
        image[0].header["DEC"] = (dec_deg, "J2000, stamped by Rosace")
        image[0].header["EQUINOX"] = (2000, "Equinox of RA and DEC above, stamped by Rosace")
    if target_name is not None:
        image[0].header["OBJECT"] = (target_name, "Queried name, stamped by Rosace")
    path = Path(obs_dir) / filename
    image.writeto(path, overwrite=True)
    return path


def compute_exposure_time_and_count(
    magnitude: float, exposure_time_mag0: float, max_exposure_time: float
) -> tuple[float, int]:
    """(per-exposure duration in seconds, count) for a target of the given
    magnitude: exposure_time_mag0 * 10**(magnitude / 2.5), split into several
    equal max_exposure_time-long exposures once that raw value exceeds
    max_exposure_time (e.g. a raw 700s at a 300s max -> (300.0, 3)). Maps
    directly onto ExposureConfig.exposure_time/num_exposures — used by
    expose.py whenever a program doesn't set an explicit exposure_time."""
    exposure_time = exposure_time_mag0 * 10 ** (magnitude / 2.5)
    if exposure_time <= max_exposure_time:
        return exposure_time, 1
    count = math.ceil(exposure_time / max_exposure_time)
    return max_exposure_time, count


def build_fits_root_name(
    image_type: str,
    *,
    target_name: str | None = None,
    exposure_time: float | None = None,
    num_exposures: int = 1,
    sensor_temp_c: float | None = None,
    timestamp: datetime | None = None,
) -> str:
    """Builds the FITS filename convention shared by every kind of image
    this app writes (science, guiding, and future calibration frames) —
    naming spec agreed with the user 2026-08-07, relaxed the same day so a
    dev/debug call can get away with only image_type.

    Returns only the "root name": no ".fits" extension and no series index
    (e.g. "-1", "-2", ...) for a multi-file acquisition series — both are
    the caller's responsibility, since every file in one series shares the
    same root name.

    Fields are joined with "_", in this order: prefix, [sensor temperature],
    [exposure], timestamp. image_type is the only required argument —
    everything else is optional and simply omitted from the name if not
    given, not replaced by a placeholder value.

    image_type is one fixed keyword from an explicit, extensible allow-list
    (ALLOWED_IMAGE_TYPES) checked at call time, case-insensitive, normalized
    to upper: STAR (any science image)/BIAS/DARK/FLAT/CALIB/GUIDING.

    target_name (the observer-entered target name — the queried name as
    typed in the observation list, e.g. scheduler_data["queried_name"],
    deliberately not the resolved canonical Target.name) plugs in
    differently depending on image_type:
    - STAR: target_name, if given, *replaces* the literal "STAR" prefix
      — e.g. image_type="STAR", target_name="V831 Cas" -> "V831Cas...".
      Without a target_name, image_type="STAR" alone falls back to the
      literal "STAR" prefix (handy for a quick dev/debug call).
    - GUIDING: target_name, if given, is inserted as its own field right
      after the "GUIDING" prefix instead of replacing it — e.g.
      image_type="GUIDING", target_name="Vega" -> "GUIDING_Vega_...". The
      prefix stays literal here (unlike STAR) since GUIDING is itself
      part of what the name needs to say — a guide image isn't the
      science target's own file.
    - Any other image_type (BIAS/DARK/FLAT/CALIB): passing target_name is
      an explicit error — that combination can only be a caller mistake.
    Every character outside [A-Za-z0-9_-] is stripped from target_name
    (this is what makes stray spaces and characters like "*" in some star
    names filesystem-safe) — no exceptions, since a space or a "*" in a
    FITS filename is a real problem for downstream automated processing.

    sensor_temp_c is an optional field, DARK/BIAS only (an ordinary
    STAR/GUIDING/etc. image never reports it) — not knowing it (no
    temperature sensor, or simply not checked) is normal, not an error:
    the field is just omitted. Passing it together with any other
    image_type is still an explicit error, not silently dropped. Placed
    right after the type, rounded to the nearest degree Celsius (e.g.
    "-30deg").

    Exposure field, omitted entirely if exposure_time isn't given: a
    single exposure is "<seconds>s" — rounded to the nearest second if
    >= 1s (e.g. "30s"), or 2 decimals with "." replaced by "_" if < 1s
    (e.g. "0_30s"), since a literal "." right before the later ".fits"
    extension would be confusing. Several identical exposures making up
    one series are "<count>x<seconds>s" (e.g. "7x30s") — this is a purely
    human-readable convention, not meant to ever be machine-parsed back.

    Timestamp: UTC, second precision, "YYYYMMDD_HHMMSS" — same timezone
    convention as Observation.created_at / init_session.py's session
    directory / database.py elsewhere in this codebase. Guarantees
    uniqueness across single files; for a multi-file series, uniqueness
    additionally relies on the caller's own per-file series index.
    Defaults to the current time (datetime.now(timezone.utc)) if not
    given — overridable for tests.
    """
    image_type = image_type.upper()
    if image_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError(f"build_fits_root_name: image_type={image_type!r} not in {sorted(ALLOWED_IMAGE_TYPES)}")

    target_name_field = None
    if image_type == "STAR":
        if target_name:
            prefix = _UNSAFE_NAME_CHARS.sub("", target_name)
            if not prefix:
                raise ValueError(f"build_fits_root_name: target_name={target_name!r} has no safe characters left")
        else:
            prefix = "STAR"
    elif image_type in _TARGET_NAME_APPEND_TYPES:
        prefix = image_type
        if target_name:
            target_name_field = _UNSAFE_NAME_CHARS.sub("", target_name)
            if not target_name_field:
                raise ValueError(f"build_fits_root_name: target_name={target_name!r} has no safe characters left")
    else:
        if target_name is not None:
            allowed = sorted({"STAR"} | _TARGET_NAME_APPEND_TYPES)
            raise ValueError(f"build_fits_root_name: target_name is only valid when image_type in {allowed}")
        prefix = image_type

    if sensor_temp_c is not None and image_type not in _TEMP_ALLOWED_TYPES:
        raise ValueError(
            f"build_fits_root_name: sensor_temp_c is only valid for image_type in {sorted(_TEMP_ALLOWED_TYPES)}"
        )

    fields = [prefix]
    if target_name_field:
        fields.append(target_name_field)
    if sensor_temp_c is not None:
        fields.append(f"{round(sensor_temp_c)}deg")

    if exposure_time is not None:
        if exposure_time >= 1:
            time_str = f"{round(exposure_time)}s"
        else:
            time_str = f"{exposure_time:.2f}s".replace(".", "_")
        fields.append(f"{num_exposures}x{time_str}" if num_exposures > 1 else time_str)

    fields.append((timestamp or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S"))

    return "_".join(fields)
