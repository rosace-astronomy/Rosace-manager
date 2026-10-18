"""
Where the stars are on an all-sky (fisheye) image: the lens model, the
bright stars of the sky, and the fit of the model on the stars of an image.

Plain functions, not a sub-plugin: used by fripon_image and by
scripts/fit_fripon_lens.py.

Lens model (LensModel): a star at altitude alt and azimuth az falls at
    z = 90 - alt                    (zenith distance, degrees)
    r = k * z + k3 * z**3           (pixels from the centre)
    x = cx + mirror * r * sin(az - rotation)
    y = cy - r * cos(az - rotation)
-- an equidistant fisheye with a cubic distortion term, turned by `rotation`
and mirrored or not (mirror = +1 or -1, depending on how the camera image
is read). Pixel convention of the JPEG image itself: origin at the centre of
the top-left pixel, x to the right, y downwards (not Rosace's FITS
convention: these images never leave this module and the drawing of them).

Fit (fit()): the stars of the image are detected, the bright stars above the
horizon placed with astro_math.py at the image's time, and the model that
lays one on the other found -- refined from a stored model, or searched over
every orientation without one. Tried on a FRIPON image (FRPC03, Royan,
2026-10-18): 94 stars at 0.98 px rms (about 0.17 degree).

The bright stars come from data/bright_stars.csv (scripts/build_bright_stars.py).
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from scipy import ndimage, optimize
from scipy.spatial import cKDTree

from ....astro_math import altitude_deg, azimuth_deg

BRIGHT_STARS_PATH = Path(__file__).resolve().parents[3] / "data" / "bright_stars.csv"

COARSE_FACTOR = 4                # slowly varying levels computed on the image reduced this many times
MIN_ALTITUDE_DEG = 12.0          # stars lower than this: haze, horizon, obstacles
DISK_LEVEL = 40                  # 8-bit level separating the sky disk from the dark surround
DISK_EDGE_FRACTION = 0.95        # stars searched inside this fraction of the disk radius
DETECTION_SIGMA = 4.0            # a star: a local maximum this many noise sigmas above its surroundings
MAX_DETECTED = 400               # brightest detections kept
SEARCH_VMAX = 2.6                # stars used for the full orientation search
SEARCH_TOLERANCE_PX = 5.0
SEARCH_SCALES = 8                # scales tried, from (disk radius / 95) to (disk radius / 80) px per degree
SEARCH_CANDIDATES = 10           # best orientations of the coarse search, each refined: the coarse
                                 # score alone (a dozen stars) can prefer a wrong orientation
FIT_VMAX = 3.5                   # stars used for the least-squares fit
FULL_TOLERANCES_PX = (14, 10, 7, 5, 4, 4)     # shrinking match tolerance after a full search
REFINE_TOLERANCES_PX = (8, 5, 4, 4)           # from a stored model
MIN_MATCHED_STARS = 15           # fewer: clouds, the fit is not trusted
# FRIPON captions: logo top left, station and time along the bottom
LOGO_BOX = (0, 0, 260, 70)       # x0, y0, x1, y1
CAPTION_HEIGHT = 66


@dataclass(frozen=True)
class BrightStar:
    simbad_name: str
    usual_name: str
    ra_deg: float
    dec_deg: float
    v: float


@lru_cache(maxsize=1)
def bright_stars() -> tuple[BrightStar, ...]:
    """The bright stars of the whole sky, brightest first."""
    with BRIGHT_STARS_PATH.open() as f:
        rows = csv.DictReader(line for line in f if not line.startswith("#"))
        return tuple(BrightStar(r["simbad_name"], r["usual_name"], float(r["ra_deg"]), float(r["dec_deg"]), float(r["v"]))
                     for r in rows)


@dataclass(frozen=True)
class LensModel:
    cx: float
    cy: float
    k: float
    k3: float
    rotation_deg: float
    mirror: int

    def project(self, alt_deg, az_deg) -> tuple[np.ndarray, np.ndarray]:
        z = 90.0 - np.asarray(alt_deg, float)
        r = self.k * z + self.k3 * z ** 3
        a = np.radians(np.asarray(az_deg, float) - self.rotation_deg)
        return self.cx + self.mirror * r * np.sin(a), self.cy - r * np.cos(a)

    def to_config(self) -> dict:
        """As written in the configuration (subplugin_params.fripon.lens)."""
        values = asdict(self)
        return {key: (int(value) if key == "mirror" else round(float(value), 8)) for key, value in values.items()}

    @classmethod
    def from_config(cls, values: dict) -> "LensModel":
        return cls(float(values["cx"]), float(values["cy"]), float(values["k"]), float(values.get("k3", 0.0)),
                   float(values["rotation_deg"]), int(values["mirror"]))


@dataclass
class SkyPositions:
    """Bright stars above the horizon at a time and place, with their altitude and azimuth."""
    stars: list[BrightStar]
    alt: np.ndarray
    az: np.ndarray
    v: np.ndarray


def sky_positions(when: datetime, latitude_deg: float, longitude_deg: float) -> SkyPositions:
    stars, alts, azs = [], [], []
    for star in bright_stars():
        alt = altitude_deg(star.ra_deg, star.dec_deg, latitude_deg, longitude_deg, when)
        if alt > MIN_ALTITUDE_DEG:
            stars.append(star)
            alts.append(alt)
            azs.append(azimuth_deg(star.ra_deg, star.dec_deg, latitude_deg, longitude_deg, when))
    return SkyPositions(stars, np.array(alts), np.array(azs), np.array([s.v for s in stars]))


def to_gray(image: np.ndarray) -> np.ndarray:
    """An 8-bit JPEG (grey or colour) as one float plane."""
    image = np.asarray(image, float)
    return image[..., :3].mean(axis=2) if image.ndim == 3 else image


def _slow_median(image: np.ndarray, size: int) -> np.ndarray:
    """A median filter of `size` pixels for slowly varying levels (sky disk,
    sky background), computed on the image reduced COARSE_FACTOR times and
    brought back to full size -- the full-size filter takes seconds."""
    small = image[::COARSE_FACTOR, ::COARSE_FACTOR]
    filtered = ndimage.median_filter(small, max(3, size // COARSE_FACTOR))
    full = np.repeat(np.repeat(filtered, COARSE_FACTOR, axis=0), COARSE_FACTOR, axis=1)
    return full[:image.shape[0], :image.shape[1]]


def sky_disk(gray: np.ndarray) -> tuple[np.ndarray, float, float, float]:
    """The sky disk (mask), its centre and its radius; the captions left out."""
    disk = _slow_median(gray, 15) > DISK_LEVEL
    x0, y0, x1, y1 = LOGO_BOX
    disk[y0:y1, x0:x1] = False
    disk[gray.shape[0] - CAPTION_HEIGHT:, :] = False
    ys, xs = np.nonzero(disk)
    if xs.size == 0:
        raise ValueError("no sky disk found on the image")
    return disk, float(xs.mean()), float(ys.mean()), math.sqrt(disk.sum() / math.pi)


def detect_stars(gray: np.ndarray, disk: np.ndarray, cx: float, cy: float, radius: float) -> np.ndarray:
    """Star candidates (x, y), brightest first: local maxima well above their
    surroundings, inside the sky disk."""
    smooth = ndimage.gaussian_filter(gray, 1.2)
    residual = smooth - _slow_median(smooth, 25)
    values = residual[disk]
    noise = 1.4826 * np.median(np.abs(values - np.median(values)))
    peaks = (residual == ndimage.maximum_filter(residual, 7)) & (residual > DETECTION_SIGMA * max(noise, 1e-6)) & disk
    py, px = np.nonzero(peaks)
    inside = np.hypot(px - cx, py - cy) < DISK_EDGE_FRACTION * radius
    px, py = px[inside], py[inside]
    order = np.argsort(-residual[py, px])[:MAX_DETECTED]
    return np.c_[px[order], py[order]].astype(float)


@dataclass
class FitResult:
    model: LensModel
    positions: SkyPositions
    matched: np.ndarray          # per star of positions: True when found on the image
    rms_px: float

    @property
    def matched_count(self) -> int:
        return int(self.matched.sum())


def _refine(start: LensModel, sky: SkyPositions, tree: cKDTree, detections: np.ndarray,
            tolerances: tuple) -> tuple[LensModel, float]:
    model, rms = start, float("inf")
    for tolerance in tolerances:
        x, y = model.project(sky.alt, sky.az)
        distance, index = tree.query(np.c_[x, y])
        used = (distance < tolerance) & (sky.v < FIT_VMAX)
        if used.sum() < 5:
            break
        alt, az, target = sky.alt[used], sky.az[used], detections[index[used]]

        def residuals(q):
            trial = LensModel(q[0], q[1], q[2], q[4], q[3], model.mirror)
            xx, yy = trial.project(alt, az)
            return np.r_[xx - target[:, 0], yy - target[:, 1]]

        solution = optimize.least_squares(residuals, [model.cx, model.cy, model.k, model.rotation_deg, model.k3])
        q = solution.x
        model = LensModel(q[0], q[1], q[2], q[4], q[3] % 360.0, model.mirror)
        rms = math.sqrt(2 * np.mean(solution.fun ** 2))
    return model, rms


def _full_search(sky: SkyPositions, tree: cKDTree, cx: float, cy: float, radius: float) -> list[LensModel]:
    """The SEARCH_CANDIDATES orientations and scales laying the most bright
    stars on detections, best first."""
    bright = sky.v < SEARCH_VMAX
    scored = []
    for mirror in (1, -1):
        for k in np.linspace(radius / 95, radius / 80, SEARCH_SCALES):
            for rotation in np.arange(0.0, 360.0, 1.0):
                model = LensModel(cx, cy, float(k), 0.0, float(rotation), mirror)
                x, y = model.project(sky.alt[bright], sky.az[bright])
                scored.append((int((tree.query(np.c_[x, y])[0] < SEARCH_TOLERANCE_PX).sum()), model))
    scored.sort(key=lambda item: -item[0])
    return [model for score, model in scored[:SEARCH_CANDIDATES] if score > 0]


def fit(image: np.ndarray, when: datetime, latitude_deg: float, longitude_deg: float,
        start: Optional[LensModel] = None) -> Optional[FitResult]:
    """The lens model fitted on the stars of an all-sky image taken at `when`
    from (latitude, longitude): refined from `start`, or searched without it.
    None when fewer than MIN_MATCHED_STARS stars are found (clouds, day)."""
    gray = to_gray(image)
    disk, cx, cy, radius = sky_disk(gray)
    detections = detect_stars(gray, disk, cx, cy, radius)
    sky = sky_positions(when, latitude_deg, longitude_deg)
    if len(detections) < MIN_MATCHED_STARS or len(sky.stars) < MIN_MATCHED_STARS:
        return None
    tree = cKDTree(detections)
    if start is None:
        starts, tolerances = _full_search(sky, tree, cx, cy, radius), FULL_TOLERANCES_PX
    else:
        starts, tolerances = [start], REFINE_TOLERANCES_PX
    best: Optional[FitResult] = None
    for candidate in starts:
        model, rms = _refine(candidate, sky, tree, detections, tolerances)
        x, y = model.project(sky.alt, sky.az)
        matched = tree.query(np.c_[x, y])[0] < tolerances[-1]
        if best is None or matched.sum() > best.matched_count:
            best = FitResult(model, sky, matched, rms)
    if best is None or best.matched_count < MIN_MATCHED_STARS:
        return None
    return best
