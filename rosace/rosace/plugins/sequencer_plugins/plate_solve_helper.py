"""
Plate-solve helper — wraps astrometry.net's solve-field CLI (a local
install, no wrapper script needed) to fill in a guide image's WCS header,
plus the pure-local WCS math to turn that into a pixel's sky position and
an optional pointing-error measurement. Not a sub-plugin: a plain module,
same status as acquisition_helpers.py (not resolved by pipeline_loader.py's
dynamic importlib convention).

Coordinate frame: J2000/ICRS throughout, consistent with the rest of the
app (see CLAUDE.md's "Coordinate frame convention" section) -- astrometry.net
solves against a J2000/ICRS catalogue, and solve_result() explicitly
converts its result to ICRS regardless of what the header nominally claims.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from astropy.wcs.utils import proj_plane_pixel_scales

from ...database import db_insert_pointing_error

log = logging.getLogger("spectro.subplugin.plate_solve_helper")

SOLVE_FIELD_BIN = "solve-field"

# Plate-scale search bound around sampling_arcsec, +/-5% -- deliberately
# hardcoded, not exposed as a parameter (user's call).
_SCALE_TOLERANCE = 0.05


class PlateSolveError(Exception):
    """Raised when solve-field can't be run, or finds no WCS solution."""


def _count_detected_stars(axy_path: str) -> int | None:
    """Best-effort star count from solve-field's own .axy byproduct (a FITS
    BINTABLE of extracted sources) -- used only to make a failure message
    more useful. Returns None if the file doesn't exist or can't be read,
    never raises."""
    if not os.path.exists(axy_path):
        return None
    try:
        with fits.open(axy_path) as hdul:
            return len(hdul[1].data)
    except Exception:
        return None


async def solve_wcs(
    fits_path: str,
    ra_hint: float | None = None,
    dec_hint: float | None = None,
    radius_deg: float = 5.0,
    sampling_arcsec: float | None = None,
    timeout: float = 30,
    record_error: bool = False,
    observation_id: str | None = None,
    pier_side: str | None = None,
) -> None:
    """Runs astrometry.net's solve-field on fits_path, replacing the file
    in place with astrometry's own solved copy (same header plus WCS
    keywords -- solve-field's own .new output, not a hand-patched header).

    ra_hint/dec_hint (J2000 degrees) narrow the search so solving takes
    seconds instead of potentially several minutes. Both must be given
    together or both omitted -- when omitted, RA/DEC already present in
    fits_path's own header (see acquisition_helpers.py's capture-time
    stamping) are used instead; if the header has neither, solving proceeds
    without a position hint (slower, not an error).

    sampling_arcsec (arcsec/pixel), when given, bounds the plate-scale
    search to sampling_arcsec * (1 +/- 5%) on top of the position hint --
    the 5% tolerance is a fixed constant (_SCALE_TOLERANCE), not
    configurable.

    Raises PlateSolveError -- with a bounded diagnostic (return code,
    best-effort detected-star count, whether it was a timeout -- never the
    raw, unbounded subprocess output) -- if solve-field can't be found, or
    finds no solution. On failure, fits_path and every byproduct file are
    left untouched, deliberately, so a failed solve can still be inspected.

    record_error=True additionally measures and records the pointing
    error: the angular separation between the mount's own declared
    position (RA/DEC already stamped in fits_path's header at capture
    time -- see acquisition_helpers.py) and the plate-solved position at
    the image's exact center (solve_result(fits_path), no x/y). This is
    purely a mount-pointing-accuracy check -- nothing to do with the
    spectrograph slit's position in the frame, which is a separate,
    later concern (putting the star on the slit, not measuring how
    accurately the mount pointed). The measurement is always logged and
    written to the pointing_errors table when record_error=True, which
    then requires observation_id -- checked immediately, before running
    the solve, so a caller who forgets it doesn't pay for a solve that
    goes nowhere. When record_error=False (the default), none of this
    runs at all -- the whole computation exists only to feed the DB
    write, so there's nothing to do or log without it.

    pier_side ("east"/"west", see hardware/mount.py::Mount.get_pier_side())
    is stored as-is with that record; ignored when record_error=False.
    """
    if record_error and not observation_id:
        raise ValueError("solve_wcs: observation_id is required when record_error=True")

    if (ra_hint is None) != (dec_hint is None):
        raise ValueError("solve_wcs: ra_hint and dec_hint must be given together, or both omitted")

    path = Path(fits_path)
    fits_name = path.name
    if path.suffix != ".fits":
        raise ValueError(f"solve_wcs: fits_path must end in .fits, got {fits_path!r}")

    if ra_hint is None or record_error:
        with fits.open(fits_path) as hdul:
            header = hdul[0].header
        if ra_hint is None and "RA" in header and "DEC" in header:
            ra_hint = float(header["RA"])
            dec_hint = float(header["DEC"])
        if record_error and ("RA" not in header or "DEC" not in header):
            raise ValueError(f"solve_wcs: {fits_path} has no RA/DEC in its header, can't measure pointing error")

    solve_field_bin = shutil.which(SOLVE_FIELD_BIN)
    if solve_field_bin is None:
        message = f"solve_wcs: '{SOLVE_FIELD_BIN}' not found on PATH"
        log.error(f"[PLATE_SOLVE] {message}")
        raise PlateSolveError(message)

    options = [
        "--cpulimit", str(int(timeout)),
        "--no-verify",
        "--crpix-center",
        "--match", "none",
        "--corr", "none",
        "--no-plots",
        "--overwrite",
        "--radius", str(radius_deg),
        # 2x2 binning before source extraction: raises faint stars above the
        # noise of a sky-limited guide image, needed on star-poor fields
        # (4 Aql, behind the Aquila Rift) -- see HISTORY.md 2026-09-26.
        "--downsample", "2",
    ]
    if ra_hint is not None:
        options += ["--ra", str(ra_hint), "--dec", str(dec_hint)]
    if sampling_arcsec is not None:
        scale_low = sampling_arcsec * (1 - _SCALE_TOLERANCE)
        scale_high = sampling_arcsec * (1 + _SCALE_TOLERANCE)
        options += [
            "--scale-units", "arcsecperpix",
            "--scale-low", str(scale_low),
            "--scale-high", str(scale_high),
        ]

    cmd = [solve_field_bin, *options, fits_path]
    log.info(f"[PLATE_SOLVE] Solving {fits_name} (up to {timeout:.0f}s)...")
    log.debug(f"[PLATE_SOLVE] Running: {' '.join(cmd)}")

    def _run() -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    base = str(path.with_suffix(""))
    try:
        proc = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        message = f"solve_wcs: solve-field timed out after {timeout}s for {fits_name}"
        log.error(f"[PLATE_SOLVE] {message}")
        raise PlateSolveError(message)

    solved_marker = Path(f"{base}.solved")
    if not solved_marker.exists():
        stars_detected = _count_detected_stars(f"{base}.axy")
        message = (
            f"solve_wcs: no WCS solution for {fits_name} "
            f"(return_code={proc.returncode}, stars_detected={stars_detected})"
        )
        log.error(f"[PLATE_SOLVE] {message}")
        raise PlateSolveError(message)

    os.replace(f"{base}.new", fits_path)
    for suffix in (".solved", ".axy", ".rdls", "-indx.xyls", ".match", ".corr", ".wcs"):
        Path(f"{base}{suffix}").unlink(missing_ok=True)

    log.info(f"[PLATE_SOLVE] WCS solved for {fits_name}")

    if record_error:
        with fits.open(fits_path) as hdul:
            header = hdul[0].header
            mount_ra = float(header["RA"])
            mount_dec = float(header["DEC"])
            target_name = header.get("OBJECT", "")

        solved_coord = solve_result(fits_path)
        separation_arcsec = SkyCoord(mount_ra, mount_dec, unit="deg").separation(solved_coord).arcsec
        log.info(
            f"[PLATE_SOLVE] Pointing error for {target_name or '(no target name)'}: {separation_arcsec:.1f}\""
        )
        await db_insert_pointing_error(
            observation_id, target_name, mount_ra, mount_dec,
            solved_coord.ra.degree, solved_coord.dec.degree,
            pier_side=pier_side,
        )


def solve_result(fits_path: str, x: float | None = None, y: float | None = None) -> SkyCoord:
    """Returns the sky position (a SkyCoord, always ICRS/J2000) of pixel
    (x, y) in fits_path, reading the WCS already present in its header
    (from a prior solve_wcs() call). Purely local: never invokes
    solve-field.

    x and y must be given together or both omitted -- omitted defaults to
    the image center, from (NAXIS1-1)/2, (NAXIS2-1)/2.

    Pixel convention, easy to get wrong: this uses astropy's high-level
    WCS.pixel_to_world(), which is zero-indexed -- pixel (0, 0) is the
    center of the first pixel. A position picked by eye in a FITS viewer
    (e.g. DS9) is typically one-indexed instead -- subtract 1 from each
    axis before passing such a value in here, or the result is off by
    one pixel (exactly the kind of error this function exists to catch,
    applied to itself).
    """
    if (x is None) != (y is None):
        raise ValueError("solve_result: x and y must be given together, or both omitted")

    with fits.open(fits_path) as hdul:
        header = hdul[0].header
        wcs = _wcs_from_header(header)

    if x is None:
        x = (header["NAXIS1"] - 1) / 2
        y = (header["NAXIS2"] - 1) / 2

    coord = wcs.pixel_to_world(x, y).icrs
    # .icrs above only changes the *frame* to ICRS -- it does not clear
    # leftover SkyCoord-level attributes (e.g. equinox) inherited from the
    # WCS's original frame (typically FK5), which are irrelevant to ICRS
    # (ICRS itself has no frame attributes at all) but still make
    # SkyCoord.is_equivalent_frame() -- and so spherical_offsets_to()/
    # spherical_offsets_by() -- report two otherwise-identical ICRS
    # SkyCoords as non-equivalent. Rebuilding a plain SkyCoord from just
    # ra/dec drops that leftover baggage. Found the hard way: real
    # solve-field output (FK5 with an explicit EQUINOX) triggered
    # "Tried to use spherical_offsets_to with two non-matching frames!"
    # against a coordinate built fresh from a Target's plain ra/dec.
    return SkyCoord(ra=coord.ra, dec=coord.dec, frame="icrs")


def world_to_pixel(fits_path: str, coord: SkyCoord) -> tuple[float, float]:
    """Returns the pixel (x, y) in fits_path where coord falls, reading the
    WCS already present in its header (from a prior solve_wcs() call) --
    the inverse of solve_result(). Purely local: never invokes solve-field.
    A thin wrapper around astropy.wcs.WCS.world_to_pixel(), which already
    accepts a SkyCoord directly -- no coordinate math of our own here.

    Same zero-indexed pixel convention as solve_result() (pixel (0, 0) is
    the center of the first pixel).
    """
    with fits.open(fits_path) as hdul:
        wcs = _wcs_from_header(hdul[0].header)
    x, y = wcs.world_to_pixel(coord)
    return float(x), float(y)


def pixel_scale_arcsec(fits_path: str) -> float:
    """Returns the average pixel scale, in arcsec/pixel, from the WCS
    already present in fits_path's header (a prior solve_wcs() call) --
    the mean of the per-axis scales astropy.wcs.utils.proj_plane_pixel_scales()
    reports (degrees/pixel), converted to arcsec. Purely local, like
    solve_result() -- never invokes solve-field."""
    with fits.open(fits_path) as hdul:
        wcs = _wcs_from_header(hdul[0].header)
    return float(np.mean(proj_plane_pixel_scales(wcs))) * 3600.0


def _wcs_from_header(header: fits.Header) -> WCS:
    """astropy.wcs.WCS() "fixes" a handful of common real-world header
    quirks on construction (e.g. deriving MJD-OBS from DATE-OBS) and warns
    about each one via FITSFixedWarning -- routine, not a sign of a bad
    solve, so silenced here rather than at every call site."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        return WCS(header)


# ─────────────────────────────────────────────────────────────
# Manual CLI, for verifying against a real astrometry.net install on a real
# image, without going through a sub-plugin or touching rosace.db (never
# passes record_error, so the DB is never touched here). Warning: fits_path
# IS modified in place (overwritten with astrometry.net's solved copy) --
# work on a copy of the image if the original must stay untouched.
#
#   # Simple case: the file already has RA/DEC in its own header
#   python -m rosace.plugins.sequencer_plugins.plate_solve_helper /path/to/image.fits
#
#   # No RA/DEC in the header, or to force a different search position
#   python -m rosace.plugins.sequencer_plugins.plate_solve_helper /path/to/image.fits --ra 279.23 --dec 38.78
#
#   # Query a specific pixel position (e.g. simulate the slit position)
#   python -m rosace.plugins.sequencer_plugins.plate_solve_helper /path/to/image.fits --x 512 --y 480
#
#   # With the plate scale, to bound the search (+/-5%)
#   python -m rosace.plugins.sequencer_plugins.plate_solve_helper /path/to/image.fits --sampling-arcsec 1.75
#
# Full flag list: python -m rosace.plugins.sequencer_plugins.plate_solve_helper --help
# ─────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="plate_solve_helper",
        description="Manually plate-solve a real FITS image and print the resulting RA/DEC. "
                    "Modifies fits_path in place (astrometry.net's solved WCS) but never "
                    "deletes it, and never touches rosace.db.",
    )
    parser.add_argument("fits_path", help="Path to the FITS image to solve, in place")
    parser.add_argument("--ra", type=float, default=None,
                        help="RA search hint, J2000 degrees (default: read from the image's own RA header key)")
    parser.add_argument("--dec", type=float, default=None,
                        help="DEC search hint, J2000 degrees (default: read from the image's own DEC header key)")
    parser.add_argument("--radius", type=float, default=5.0, help="Search radius in degrees (default: 5.0)")
    parser.add_argument("--sampling-arcsec", type=float, default=None,
                        help="Plate scale, arcsec/pixel -- bounds the search to +/-5%% (default: unbounded)")
    parser.add_argument("--timeout", type=float, default=360, help="solve-field timeout in seconds (default: 360)")
    parser.add_argument("--x", type=float, default=None, help="Pixel X to report RA/DEC for (default: image center)")
    parser.add_argument("--y", type=float, default=None, help="Pixel Y to report RA/DEC for (default: image center)")
    return parser.parse_args(argv)


async def _run_cli(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    print(f"Solving {args.fits_path} ...")
    try:
        await solve_wcs(
            args.fits_path,
            ra_hint=args.ra,
            dec_hint=args.dec,
            radius_deg=args.radius,
            sampling_arcsec=args.sampling_arcsec,
            timeout=args.timeout,
        )
        coord = solve_result(args.fits_path, args.x, args.y)
        scale = pixel_scale_arcsec(args.fits_path)
    except (PlateSolveError, ValueError) as e:
        print(f"FAILED: {e}")
        return 1

    where = "image center" if args.x is None else f"pixel ({args.x}, {args.y})"
    print(
        f"Solved. RA/DEC at {where}: {coord.ra.degree:.6f}, {coord.dec.degree:.6f} "
        f"(J2000 degrees), pixel scale: {scale:.4f} arcsec/pixel"
    )
    print(f"{args.fits_path} was overwritten in place with the solved WCS -- kept, not deleted.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run_cli()))
