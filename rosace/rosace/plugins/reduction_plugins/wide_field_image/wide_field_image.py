"""
REDUCTION sub-plugin — wide-field image
The pointing camera's image of the target's field, plate-solved, with the target and the bright stars marked.

Reads the image wide_field_acquisition took right after the science
exposures (shared key sequencer_data["wide_field_images"]), so one sees
whether clouds crossed the target's field and where the target sits in it.
Shown beside the FRIPON image, on the summary PDF's « Sky » page and in the
« Ciel » panel of the BeSS validation page.

Colour: a colour camera sends its raw Bayer mosaic (BAYERPAT, with
XBAYROFF/YBAYROFF when present); each 2 x 2 block becomes one RGB pixel
(bayer_superpixel(), the two greens averaged) -- half the size, no
interpolation. An image already in RGB (NAXIS = 3) is used as it is; a 2D
image without BAYERPAT is grey, binned 2 x 2 the same way.

Positions: the grey image (sum of the three colours) is written to the
observation's intermediate directory and plate-solved by solve-field
(plate_solve_helper.solve_wcs), around the target, within SEARCH_RADIUS_DEG.
The pointing camera's scale is not known in advance: without
subplugin_params.wide_field.sampling_arcsec (arcsec per pixel of that
image) every scale is tried; the log line gives the scale found, to be set
there. Solved: the target (red open cross), the bright stars of
data/bright_stars.csv in the field (green circles, usual names) and the
directions of North and East are drawn. Not solved (clouds, a field too
far from the target): the image alone, the reason in its caption.

Writes "<final name>_wide_field.png" in the session directory; shared key
reduction_data["wide_field"]. Nothing to do without a wide-field image
(INFO); never blocking -- an unreadable image is a WARNING.
"""

from __future__ import annotations

DISPLAY_NAME = "Grand champ"

import logging
import math
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from matplotlib.figure import Figure
from matplotlib.patches import Circle

from ....config import SUBPLUGIN_PARAMS
from ....file_names import final_name
from ....models import Observation
from ...sequencer_plugins.plate_solve_helper import pixel_scale_arcsec, solve_result, solve_wcs
from ...sub_plugin_base import SubPlugin
from ..fripon_image.fisheye import bright_stars
from ..reduction_helpers import intermediate_dir

log = logging.getLogger("spectro.subplugin.wide_field_image")

SEARCH_RADIUS_DEG = 10.0            # the pointing camera roughly follows the telescope
SOLVE_TIMEOUT_S = 60                # without a known scale, solve-field tries them all
DISPLAY_SCALE = 1.5                 # the superpixel image is small: drawn this many times larger
STRETCH_PERCENTILES = (1.0, 99.5)   # per colour: also balances the colours on the sky
CROSS_GAP_PX, CROSS_ARM_PX = 5, 15  # in image pixels; the open centre keeps the target visible
STAR_CIRCLE_PX = 6
ARROW_FRACTION = 0.10               # N/E arrows, as a fraction of the image's smaller side

_CHANNELS = {"R": 0, "G": 1, "B": 2}


def bayer_superpixel(data: np.ndarray, pattern: str, x_offset: int = 0, y_offset: int = 0) -> np.ndarray:
    """RGB image (height/2, width/2, 3) of a raw Bayer mosaic: each 2 x 2 block
    one pixel, the two greens averaged. pattern names the colours of the
    block starting at array index [y_offset, x_offset], row by row (RGGB:
    [0,0] red, [0,1] green, [1,0] green, [1,1] blue)."""
    pattern = pattern.strip().upper()
    if len(pattern) != 4 or sorted(pattern) != ["B", "G", "G", "R"]:
        raise ValueError(f"unknown Bayer pattern {pattern!r}")
    data = np.asarray(data, dtype=np.float64)[y_offset % 2:, x_offset % 2:]
    height, width = data.shape[0] // 2 * 2, data.shape[1] // 2 * 2
    data = data[:height, :width]
    rgb = np.zeros((height // 2, width // 2, 3))
    count = np.zeros(3)
    for index, colour in enumerate(pattern):
        dy, dx = divmod(index, 2)
        rgb[:, :, _CHANNELS[colour]] += data[dy::2, dx::2]
        count[_CHANNELS[colour]] += 1
    return rgb / count


def _bin2(data: np.ndarray) -> np.ndarray:
    data = np.asarray(data, dtype=np.float64)
    height, width = data.shape[0] // 2 * 2, data.shape[1] // 2 * 2
    d = data[:height, :width]
    return (d[0::2, 0::2] + d[0::2, 1::2] + d[1::2, 0::2] + d[1::2, 1::2]) / 4


def colour_image(data: np.ndarray, header: fits.Header) -> np.ndarray:
    """The image as RGB (height, width, 3), array row 0 = FITS row 0 (the bottom)."""
    if data.ndim == 3 and data.shape[0] == 3:
        return np.moveaxis(np.asarray(data, dtype=np.float64), 0, -1)
    if data.ndim != 2:
        raise ValueError(f"unexpected image shape {data.shape}")
    if header.get("BAYERPAT"):
        return bayer_superpixel(data, str(header["BAYERPAT"]),
                                int(header.get("XBAYROFF", 0)), int(header.get("YBAYROFF", 0)))
    return np.repeat(_bin2(data)[:, :, None], 3, axis=2)


def _stretch(rgb: np.ndarray) -> np.ndarray:
    out = np.empty_like(rgb)
    for channel in range(3):
        low, high = np.percentile(rgb[:, :, channel], STRETCH_PERCENTILES)
        out[:, :, channel] = (rgb[:, :, channel] - low) / max(high - low, 1e-9)
    return np.clip(out, 0, 1)


def _wcs(path: Path) -> WCS:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        return WCS(fits.getheader(path))


def _field_stars(wcs: WCS, centre: SkyCoord, radius_deg: float, width: int, height: int):
    """(name, x, y) of the bright stars inside the image."""
    stars = [s for s in bright_stars() if s.usual_name]
    coords = SkyCoord([s.ra_deg for s in stars], [s.dec_deg for s in stars], unit="deg")
    near = centre.separation(coords).deg < radius_deg
    if not near.any():
        return []
    x, y = wcs.world_to_pixel(coords[near])
    found = []
    for star, sx, sy in zip([s for s, n in zip(stars, near) if n], np.atleast_1d(x), np.atleast_1d(y)):
        if 0 <= sx <= width - 1 and 0 <= sy <= height - 1:
            found.append((star.usual_name, float(sx), float(sy)))
    return found


def _direction(wcs: WCS, centre: SkyCoord, east_deg: float, north_deg: float, step_deg: float) -> tuple[float, float]:
    """Unit vector, in pixels, from the centre towards North or East."""
    cx, cy = wcs.world_to_pixel(centre)
    tip = centre.spherical_offsets_by(east_deg * step_deg * u.deg, north_deg * step_deg * u.deg)
    tx, ty = wcs.world_to_pixel(tip)
    dx, dy = float(tx - cx), float(ty - cy)
    norm = math.hypot(dx, dy) or 1.0
    return dx / norm, dy / norm


def draw_annotated(path: Path, rgb: np.ndarray, caption: str, marks: Optional[dict]) -> None:
    """The colour image (FITS row 0 at the bottom) with, when solved
    (marks), the target, the bright stars and the N/E directions."""
    height, width = rgb.shape[:2]
    figure = Figure(figsize=(width * DISPLAY_SCALE / 100, height * DISPLAY_SCALE / 100), dpi=100)
    axes = figure.add_axes((0, 0, 1, 1))
    axes.imshow(_stretch(rgb), origin="lower", interpolation="nearest")
    axes.set_xlim(-0.5, width - 0.5)
    axes.set_ylim(-0.5, height - 0.5)
    axes.set_axis_off()
    if marks:
        target = marks["target"]
        for name, x, y in marks["stars"]:
            if target is not None and math.hypot(x - target[1], y - target[2]) < CROSS_ARM_PX:
                continue            # the target itself: named by its cross
            axes.add_patch(Circle((x, y), STAR_CIRCLE_PX, fill=False, color="lime", lw=0.9))
            axes.text(x + STAR_CIRCLE_PX + 3, y + 3, name, color="yellow", fontsize=10, fontweight="bold")
        if target is not None:
            name, tx, ty = target
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                axes.plot([tx + dx * CROSS_GAP_PX, tx + dx * CROSS_ARM_PX], [ty + dy * CROSS_GAP_PX, ty + dy * CROSS_ARM_PX],
                          color="red", lw=2.2)
            axes.text(tx + CROSS_ARM_PX + 3, ty - CROSS_ARM_PX - 3, name, color="red", fontsize=11, fontweight="bold",
                      va="top")
        length = ARROW_FRACTION * min(width, height)
        ox, oy = 0.06 * width + 1.4 * length, 0.94 * height - 1.4 * length   # room for a label on any side
        for label, (ux, uy) in (("N", marks["north"]), ("E", marks["east"])):
            axes.annotate("", xy=(ox + ux * length, oy + uy * length), xytext=(ox, oy),
                          arrowprops={"arrowstyle": "->", "color": "cyan", "lw": 1.5})
            axes.text(ox + ux * length * 1.25, oy + uy * length * 1.25, label, color="cyan", fontsize=12,
                      ha="center", va="center")
    axes.text(width - 6, 4, caption, color="white", fontsize=9, ha="right", va="bottom",
              bbox={"facecolor": "black", "alpha": 0.6, "edgecolor": "none", "pad": 2})
    figure.savefig(path, dpi=100)


class WideFieldImagePlugin(SubPlugin):
    """Plate-solves the pointing camera's wide-field image and marks the target and the bright stars on it."""

    async def execute(self, obs: Observation) -> Observation:
        names = obs.sequencer_data.get("wide_field_images") or []
        if not names:
            log.info("[WIDE_FIELD] No wide-field image for this observation")
            return obs
        try:
            await self._run(obs, names[-1])
        except Exception as error:
            log.warning(f"[WIDE_FIELD] No annotated wide-field image: {type(error).__name__}: {error}")
        return obs

    async def _run(self, obs: Observation, name: str) -> None:
        params = SUBPLUGIN_PARAMS.get("wide_field") or {}
        session_dir = Path(obs.obs_dir)
        with fits.open(session_dir / name) as hdul:
            header = hdul[0].header.copy()
            rgb = colour_image(hdul[0].data, header)
        height, width = rgb.shape[:2]
        exposure = float(header.get("EXPTIME", 0))
        captured = (datetime.fromisoformat(str(header["DATE-OBS"])).replace(tzinfo=timezone.utc)
                    if "DATE-OBS" in header else None)

        target = obs.scheduler_data.get("target")
        target_name = obs.scheduler_data.get("queried_name") or (target.name if target else "")
        gray_path = intermediate_dir(obs) / f"{Path(name).stem}_gray.fits"
        gray_header = fits.Header()
        for key in ("DATE-OBS", "EXPTIME", "OBJECT"):
            if key in header:
                gray_header[key] = header[key]
        fits.PrimaryHDU(rgb.sum(axis=2).astype(np.float32), gray_header).writeto(gray_path, overwrite=True)

        marks, solved = None, {}
        try:
            if target is None:
                raise ValueError("no target position")
            await solve_wcs(str(gray_path), ra_hint=target.ra, dec_hint=target.dec, radius_deg=SEARCH_RADIUS_DEG,
                            sampling_arcsec=params.get("sampling_arcsec"), timeout=SOLVE_TIMEOUT_S)
            marks, solved = self._marks(gray_path, target, target_name, width, height)
            text = (f"field {solved['field_deg'][0]:.1f} x {solved['field_deg'][1]:.1f} deg, "
                    f"{solved['scale_arcsec']:.1f}\"/px, target {solved['target_offset_arcmin']:.0f}' from the centre")
        except Exception as error:
            log.warning(f"[WIDE_FIELD] {name} not plate-solved, shown without marks: {error}")
            text = f"not plate-solved: {error}"[:120]

        when = f"{captured:%Y-%m-%d %H:%M:%S} UT, " if captured else ""
        caption = f"Wide field {when}{exposure:g} s -- {text}"
        annotated = f"{final_name(obs)}_wide_field.png"
        draw_annotated(session_dir / annotated, rgb, caption, marks)

        obs.reduction_data["wide_field"] = {
            "file": name,
            "annotated_file": annotated,
            "capture_time": captured.isoformat() if captured else None,
            "exposure_s": exposure,
            "solved": marks is not None,
            "scale_arcsec": solved.get("scale_arcsec"),
            "field_deg": solved.get("field_deg"),
            "target_offset_arcmin": solved.get("target_offset_arcmin"),
            "target_pixel": solved.get("target_pixel"),
        }
        log.info(f"[WIDE_FIELD] {name}: {text} -> {annotated}")

    @staticmethod
    def _marks(gray_path: Path, target, target_name: str, width: int, height: int) -> tuple[dict, dict]:
        wcs = _wcs(gray_path)
        scale = pixel_scale_arcsec(str(gray_path))
        centre = solve_result(str(gray_path))
        field = [round(width * scale / 3600, 2), round(height * scale / 3600, 2)]
        target_coord = SkyCoord(target.ra, target.dec, unit="deg")
        tx, ty = (float(v) for v in wcs.world_to_pixel(target_coord))
        inside = 0 <= tx <= width - 1 and 0 <= ty <= height - 1
        step = min(field) / 10
        marks = {
            "target": (target_name, tx, ty) if inside else None,
            "stars": _field_stars(wcs, centre, math.hypot(*field) / 2, width, height),
            "north": _direction(wcs, centre, 0, 1, step),
            "east": _direction(wcs, centre, 1, 0, step),
        }
        solved = {
            "scale_arcsec": round(scale, 2),
            "field_deg": field,
            "target_offset_arcmin": round(float(centre.separation(target_coord).arcmin), 1),
            "target_pixel": [round(tx, 1), round(ty, 1)] if inside else None,
        }
        return marks, solved
