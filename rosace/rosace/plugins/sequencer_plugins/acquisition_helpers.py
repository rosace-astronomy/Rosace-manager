"""
Shared acquisition helpers for real-hardware (sequencer_backend=indi)
SEQUENCER sub-plugins — plain functions, not a sub-plugin of their own: not
resolved by pipeline_loader.py's dynamic importlib convention, no program
YAML ever declares this module as a step. Each caller still owns its own
execute() and its own key in obs.sequencer_data; this module only factors
out logic that would otherwise be duplicated across sub-plugins.
"""

from __future__ import annotations

import asyncio
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits

from ...config import GUI_PREVIEW_DIR, SUBPLUGIN_PARAMS
from ...file_names import safe_name
from ...hardware.spectroscope import LightSource
from ...image_broadcast import ws_image_broadcaster
from ...models import Observation
from .guiding_chain_recovery import run_with_guiding_recovery

ALLOWED_IMAGE_TYPES = {"STAR", "BIAS", "DARK", "FLAT", "CALIB", "GUIDING", "GUIDINGDARK", "WIDEFIELD"}

_TEMP_ALLOWED_TYPES = {"DARK", "BIAS"}

# STAR's target_name *replaces* the literal "STAR" prefix (e.g. "V831_Cas_...");
# GUIDING's and WIDEFIELD's *follow* their literal prefix instead (e.g.
# "GUIDING_Vega_...") -- kept as their own types, not folded into STAR, since
# the image_type still needs to appear in the name (it's not itself the
# science target's own file).
_TARGET_NAME_APPEND_TYPES = {"GUIDING", "WIDEFIELD"}


def _raw_variant_filename(filename: str) -> str:
    """Derives the untouched, dark-uncorrected sibling filename for a guide
    image: "GUIDING_..." -> "GUIDING_RAW_...", so the two stay obviously
    paired (same target/exposure/timestamp fields) and easy to grep for
    together. Falls back to a plain "RAW_" prefix for a caller-overridden
    filename that doesn't start with "GUIDING_" (only ever the case in
    tests -- see capture_guide_image()'s own filename override)."""
    if filename.startswith("GUIDING_"):
        return "GUIDING_RAW_" + filename[len("GUIDING_"):]
    return "RAW_" + filename


async def capture_guide_image(
    obs: Observation,
    exposure_time: float,
    filename: str | None = None,
    ra_deg: float | None = None,
    dec_deg: float | None = None,
    target_name: str | None = None,
    image_to_gui: bool = False,
    subtract_dark: bool = True,
    light_source: LightSource = LightSource.SKY,
    light_settle_seconds: float = 0.0,
) -> Path:
    """Switches obs.hardware["spectroscope"] to light_source (SKY unless told
    otherwise), waits light_settle_seconds, exposes
    obs.hardware["guiding_camera"] for exposure_time seconds, and writes the
    untouched raw exposure to disk. Returns the written path -- of the
    dark-corrected copy described below when subtract_dark=True (the
    default), of the raw file itself when subtract_dark=False.

    The raw file is named build_fits_root_name("GUIDING", ...) with "RAW_"
    inserted right after "GUIDING_" (see _raw_variant_filename() above) --
    e.g. "GUIDING_RAW_Vega_10s_....fits" -- and is written once, never
    touched again, regardless of subtract_dark.

    subtract_dark=True (the default) additionally writes a second file, the
    plain build_fits_root_name(...) name (e.g. "GUIDING_Vega_10s_....fits",
    same name this function always wrote before this raw/corrected split
    existed): the raw exposure with
    obs.sequencer_data["guiding_dark_acquisition"]["median_dark_file"] (the
    per-session guiding-camera master dark, see
    plugins/sequencer_plugins/guiding_dark_acquisition/ -- built from real
    guiding exposures at different pointings, median-combined to cancel
    real stars while keeping the sensor's own fixed hot/defective pixels,
    found 2026-09-05 to routinely break solve-field's star matching)
    subtracted, clipped to >= 0 (a raw pixel can be fainter than the dark
    median at that same pixel) and cast back to the sensor's own dtype.
    Raises RuntimeError if guiding_dark_acquisition hasn't run yet for this
    session -- no silent fallback that skips the correction, since an
    uncorrected image is exactly the failure mode this mechanism exists to
    prevent. Every plate-solving caller (slew_72ed.py, pointing_72ed.py,
    stop_phd2_guiding.py) needs the corrected copy for plate-solving,
    hence the True default -- an existing caller that doesn't pass this
    parameter keeps getting exactly that.

    subtract_dark=False skips the dark lookup and the second file entirely
    (writing a byte-identical "corrected" copy next to the raw one would be
    pure waste) -- for a caller that only ever wants the raw exposure
    itself, with no guiding-dark dependency at all.

    ra_deg/dec_deg (J2000 degrees, must be given together or both omitted)
    and target_name, when given, are stamped into the image's own header
    (RA/DEC/EQUINOX/OBJECT keywords) before any file is written -- the raw
    and corrected copies always carry identical header metadata, only the
    pixel data differs. This is what makes the corrected image
    self-sufficient for plate_solve_helper.py::solve_wcs()'s record_error,
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
    unchanged.

    Same expose()-then-writeto() pattern expose.py already uses for the
    science camera (see hardware/camera.py for the generic Camera contract).
    filename (the *corrected* file's name when subtract_dark=True, the raw
    file's own name otherwise) defaults to build_fits_root_name("GUIDING",
    target_name=target_name, exposure_time=exposure_time) + ".fits" (e.g.
    "GUIDING_Vega_0_30s_....fits" when target_name is given,
    "GUIDING_0_30s_....fits" without it) — pass an explicit filename to
    override (e.g. in tests).

    image_to_gui=True additionally writes the returned image (the corrected
    one when subtract_dark=True, the raw one otherwise -- always the same
    file this function returns the path of) to
    GUI_PREVIEW_DIR/"guidage.fits" -- a fixed name, overwritten by each call
    -- and notifies connected GUI clients over the WebSocket
    (image_broadcast.py), so the web GUI's "Guidage" panel can fetch and
    display it. False by default: an existing caller that doesn't pass it
    keeps behaving exactly as before, no extra write, no notification.

    light_source defaults to LightSource.SKY, the only correct value for a
    real guide image -- a caller never needs to pass it for that. Only
    slit_xy.py passes another one (CALIB/FLAT: the slit seen against a
    lamp-lit background), together with light_settle_seconds, a pause
    between the spectroscope command and the start of the exposure, so the
    lamp is really on before a short exposure starts. 0 by default: every
    other caller keeps behaving exactly as before.

    A caller that wants something drawn over the image on the GUI captures
    with image_to_gui=False, then calls publish_guide_preview() below with
    its overlay."""
    if (ra_deg is None) != (dec_deg is None):
        raise ValueError("capture_guide_image: ra_deg and dec_deg must be given together, or both omitted")
    if filename is None:
        filename = build_fits_root_name("GUIDING", target_name=target_name, exposure_time=exposure_time) + ".fits"

    if subtract_dark:
        dark_info = obs.sequencer_data.get("guiding_dark_acquisition")
        if not dark_info or not dark_info.get("median_dark_file"):
            raise RuntimeError(
                "CAPTURE_GUIDE_IMAGE: sequencer_data['guiding_dark_acquisition'] missing -- "
                "guiding_dark_acquisition must run before capturing guide images"
            )
        dark_median = fits.getdata(Path(obs.obs_dir) / dark_info["median_dark_file"])

    await obs.hardware["spectroscope"].set_light_source(light_source)
    if light_settle_seconds > 0:
        await asyncio.sleep(light_settle_seconds)
    image = await run_with_guiding_recovery(
        obs, lambda: obs.hardware["guiding_camera"].expose(exposure_time), "guide image exposure"
    )
    if ra_deg is not None:
        image[0].header["RA"] = (ra_deg, "J2000, stamped by Rosace")
        image[0].header["DEC"] = (dec_deg, "J2000, stamped by Rosace")
        image[0].header["EQUINOX"] = (2000, "Equinox of RA and DEC above, stamped by Rosace")
    if target_name is not None:
        image[0].header["OBJECT"] = (target_name, "Queried name, stamped by Rosace")

    raw_path = Path(obs.obs_dir) / _raw_variant_filename(filename)
    image.writeto(raw_path, overwrite=True)

    if subtract_dark:
        raw_data = image[0].data
        corrected = np.clip(raw_data.astype(np.float64) - dark_median.astype(np.float64), 0, None)
        image[0].data = corrected.astype(raw_data.dtype)
        path = Path(obs.obs_dir) / filename
        image.writeto(path, overwrite=True)
    else:
        path = raw_path

    if image_to_gui:
        GUI_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        _label_preview(image[0].header, obs)	# the files above are already written
        image.writeto(GUI_PREVIEW_DIR / "guidage.fits", overwrite=True)
        kind = "guide" if light_source is LightSource.SKY else "slit"
        await ws_image_broadcaster.broadcast("guidage", kind=kind)
    return path


def _label_preview(header: fits.Header, obs: Observation | None) -> None:
    """Stamps the observation on a GUI preview image -- OBJECT (queried name)
    and OBS_ID -- so its panel can title itself, also after a page reload
    (the preview file survives). Only ever called on the preview copy, never
    on a file of the observation."""
    if obs is None:
        return
    name = obs.scheduler_data.get("queried_name")
    if name:
        header["OBJECT"] = (name, "Queried name, stamped by Rosace")
    header["OBS_ID"] = (obs.id, "Rosace observation ID, GUI preview only")


async def publish_guide_preview(
    image_path: Path, source: str | None = None, overlay: dict | None = None, kind: str = "guide",
    obs: Observation | None = None,
) -> None:
    """Shows an already-written guide image on the GUI's "Guidage" panel:
    copies it to GUI_PREVIEW_DIR/"guidage.fits" (labelled with obs's object
    and ID when given) and notifies connected clients, forwarding source,
    overlay and kind (see image_broadcast.py): "guide" for a sky image,
    "slit" for slit_xy's lamp-lit one.

    For a caller that wants something drawn over the image -- it captures
    with image_to_gui=False, then publishes the image with its overlay:
    slit_xy.py (the measured slit), focus.py (its search window)."""
    GUI_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    with fits.open(image_path) as hdul:
        _label_preview(hdul[0].header, obs)
        hdul.writeto(GUI_PREVIEW_DIR / "guidage.fits", overwrite=True)
    await ws_image_broadcaster.broadcast("guidage", source=source, overlay=overlay, kind=kind)


def slit_position(obs: Observation) -> tuple[float, float]:
    """Slit position (x, y) in the guiding camera's field to use for this
    observation, in Rosace's pixel convention (0-indexed, Y up).

    The position calculated by slit_xy.py when it succeeded for this
    observation (sequencer_data["slit_xy"]["status"] == "calculated"),
    otherwise the default subplugin_params.guiding.slit_x/slit_y -- also
    when the program doesn't run slit_xy at all. The one place that decides
    which slit position is in force, read by pointing_72ed.py and
    stop_phd2_guiding.py."""
    measured = obs.sequencer_data.get("slit_xy")
    if measured and measured.get("status") == "calculated":
        return float(measured["slit_x"]), float(measured["slit_y"])
    guiding_params = SUBPLUGIN_PARAMS.get("guiding", {})
    return float(guiding_params["slit_x"]), float(guiding_params["slit_y"])


async def capture_science_image(
    camera: Any, exposure_time: float, obs_dir: str, filename: str, image_to_gui: bool = False,
    obs: Observation | None = None,
) -> Path:
    """Exposes the science camera for exposure_time seconds and writes the
    resulting FITS image to obs_dir/filename, returning the written path.
    Same expose()-then-writeto() shape as capture_guide_image() above, named
    to match it -- kept as its own function rather than one shared by both
    (not just capture_guide_image(camera, ...) reused here) since guiding
    needs to stamp FITS header cards on the image between expose() and
    writeto(), which this one doesn't. Extracted 2026-08-12 out of what used
    to be expose.py's own inline capture loop (now star_spectra_acquisition.py)
    -- the shared foundation for the DARK/BIAS capture sub-plugins added
    since, which use exactly this same single-shot primitive; the
    multi-sub-exposure loop itself stays in each calling sub-plugin, not
    here.

    image_to_gui=True additionally writes the same image to
    GUI_PREVIEW_DIR/"spectre.fits" -- a fixed name, overwritten by each call
    -- and notifies connected GUI clients over the WebSocket
    (image_broadcast.py), so the web GUI's "Spectre" panel can fetch and
    display it -- same mechanism and panel-per-fixed-file convention as
    capture_guide_image()'s own image_to_gui, just a different panel name.
    Every caller of this function (star_spectra_acquisition.py,
    dark_acquisition.py, bias_acquisition.py) shares the one "Spectre"
    panel -- it shows whichever kind of frame was captured most recently,
    not a per-image-type history. False by default: an existing caller that
    doesn't pass it keeps behaving exactly as before, no extra write, no
    notification."""
    image = await camera.expose(exposure_time)
    path = Path(obs_dir) / filename
    image.writeto(path, overwrite=True)
    if image_to_gui:
        GUI_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        _label_preview(image[0].header, obs)	# the observation's file is already written
        image.writeto(GUI_PREVIEW_DIR / "spectre.fits", overwrite=True)
        await ws_image_broadcaster.broadcast("spectre", kind=frame_kind(filename))
    return path


# File-name prefixes of the calibration series (build_fits_root_name()); a
# science frame is named after its target
_CALIBRATION_PREFIXES = ("DARK", "BIAS", "FLAT", "CALIB")


def frame_kind(filename: str) -> str:
    """Type of a science-camera frame, from its file name: "dark", "bias",
    "flat" or "calib" for a calibration series, "science" otherwise. Sent to
    the GUI with the image, which redoes its display stretch when the type
    changes -- the FITS header cannot tell (IMAGETYP is always 'Light Frame')."""
    prefix = Path(filename).name.split("_", 1)[0]
    return prefix.lower() if prefix in _CALIBRATION_PREFIXES else "science"


def compute_exposure_time_and_count(
    *,
    magnitude: float | None = None,
    exposure_time: float | None = None,
    exposure_time_mag0: float | None = None,
    max_exposure_time: float,
    min_exposure_count: int = 1,
) -> tuple[float, int]:
    """(per-sub-exposure duration in seconds, count) — the exposure-time
    policy shared by every science-acquisition sub-plugin. Exactly one of
    magnitude or exposure_time must be given:
    - magnitude: no explicit exposure time is known for this star (no
      program_stars.exposure_time set in targets.db) — the ideal single-
      exposure duration is computed from the target's magnitude:
      exposure_time_mag0 * 10**(magnitude / 2.5). exposure_time_mag0 is
      required in this case.
    - exposure_time: an explicit exposure duration is already known
      (program_stars.exposure_time) — used as-is, exposure_time_mag0 is
      not needed and ignored if given. Meant to be set in targets.db only
      when the magnitude-based duration wouldn't do (e.g. it would
      saturate this particular star) — same per-unit-exposure meaning as
      the magnitude-computed value, not a total to split evenly.

    Either way, that duration (called raw below) is a per-sub-exposure
    target, not a total integration budget to divide:
    - raw <= max_exposure_time: every sub-exposure is raw seconds long, and
      at least min_exposure_count of them are shot — (raw,
      min_exposure_count) — so a bright star with a short computed/explicit
      duration still yields a proper stack instead of a single frame.
    - raw > max_exposure_time: sub-exposures are capped at
      max_exposure_time, and at least ceil(raw / max_exposure_time) of them
      are needed to reach the required integration (e.g. a raw 780s at a
      300s max -> 3x300s) — but never fewer than min_exposure_count either:
      count = max(min_exposure_count, ceil(raw / max_exposure_time)), each
      max_exposure_time long.

    min_exposure_count defaults to 1 (today's behavior, unchanged) for a
    caller that doesn't pass it."""
    if (magnitude is None) == (exposure_time is None):
        raise ValueError("compute_exposure_time_and_count: exactly one of magnitude or exposure_time must be given")
    if magnitude is not None:
        if exposure_time_mag0 is None:
            raise ValueError("compute_exposure_time_and_count: exposure_time_mag0 is required when magnitude is given")
        raw_exposure_time = exposure_time_mag0 * 10 ** (magnitude / 2.5)
    else:
        raw_exposure_time = exposure_time

    if raw_exposure_time <= max_exposure_time:
        return raw_exposure_time, min_exposure_count
    count = max(min_exposure_count, math.ceil(raw_exposure_time / max_exposure_time))
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
    to upper: STAR (any science image)/BIAS/DARK/FLAT/CALIB/GUIDING/
    GUIDINGDARK/WIDEFIELD (the pointing camera's wide-field image).

    target_name (the observer-entered target name — the queried name as
    typed in the observation list, e.g. scheduler_data["queried_name"],
    deliberately not the resolved canonical Target.name) plugs in
    differently depending on image_type:
    - STAR: target_name, if given, *replaces* the literal "STAR" prefix
      — e.g. image_type="STAR", target_name="V831 Cas" -> "V831_Cas...".
      Without a target_name, image_type="STAR" alone falls back to the
      literal "STAR" prefix (handy for a quick dev/debug call).
    - GUIDING (and WIDEFIELD, the same way): target_name, if given, is inserted as its own field right
      after the "GUIDING" prefix instead of replacing it — e.g.
      image_type="GUIDING", target_name="Vega" -> "GUIDING_Vega_...". The
      prefix stays literal here (unlike STAR) since GUIDING is itself
      part of what the name needs to say — a guide image isn't the
      science target's own file.
    - Any other image_type (BIAS/DARK/FLAT/CALIB): passing target_name is
      an explicit error — that combination can only be a caller mistake.
    target_name goes through file_names.safe_name(), the one rule shared by
    every file named after a star ("*   8 Lac A" -> "8_Lac_A") — a space or
    a "*" in a FITS filename is a real problem for downstream automated
    processing.

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
            prefix = safe_name(target_name)
            if not prefix:
                raise ValueError(f"build_fits_root_name: target_name={target_name!r} has no safe characters left")
        else:
            prefix = "STAR"
    elif image_type in _TARGET_NAME_APPEND_TYPES:
        prefix = image_type
        if target_name:
            target_name_field = safe_name(target_name)
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
