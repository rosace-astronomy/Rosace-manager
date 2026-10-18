# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Earlier spectra of a Be star, from BeSS and from Rosace, for the BeSS
validation page (bess_validation_api.py): the most recent ones, to check the
spectrum shows the right star and see how it changed.

BeSS is read through its Virtual Observatory service (SSAP, ssapBE.pl), which
answers a VOTable listing the spectra (date, wavelength range, download URL).
What the service does, found by trying it (2026-10-18):
- TARGETNAME must be BeSS's own name for the star, exactly (case included):
  neither Simbad's name nor an HD number BeSS does not use is recognised.
  The star is therefore looked up, in this order: by the BeSS name the
  Arasbeam robot file gives for its HD number (the file be_scheduler keeps up
  to date), by each of our names in turn, then by its coordinates -- found
  only by position, a neighbouring star could be taken for it, so the page
  says so;
- more than 1000 matching rows: QUERY_STATUS "OVERFLOW" and no row at all
  (MAXREC is ignored), hence the H-alpha BAND on every query and a time
  window when needed;
- an echelle spectrum is one row per order: only the order holding H-alpha
  is kept;
- its VOTable declares a "dateTime" datatype astropy refuses: read as plain XML;
- a spectrum file is either a 1D image (linear wavelength axis) or a table
  with WAVE/FLUX/ERROR columns.
Downloaded spectra are kept in BESS_CACHE_DIR (a BeSS spectrum never changes).
"""

import logging
import math
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple, Optional

import httpx
import numpy as np
from astropy.io import fits
from scipy.ndimage import gaussian_filter1d

from .config import BASE_DATA_DIR, OBSERVATORY, SUBPLUGIN_PARAMS
from .database import db_get_bess_validations_by_stars
from .targets import star_aliases_map, star_get_by_name

log = logging.getLogger("spectro.bess_archive")

SSAP_URL = "http://basebe.obspm.fr/cgi-bin/ssapBE.pl"
BESS_CACHE_DIR = BASE_DATA_DIR / "bess_cache"
DEFAULT_ROBOT_PATH = "~/.rosace/be/ObsC2A_15.txt"

HALPHA_A = 6562.8
HALPHA_BAND = "6.55e-7/6.58e-7"      # metres: the rows overlapping it
SEARCH_RADIUS_DEG = 0.01             # 36 arcsec, by position
MAX_SPECTRA = 5
SAME_SPECTRUM_SECONDS = 60           # two starts closer than this: the same spectrum
# Windows tried, most recent first, when the whole history overflows (days)
OVERFLOW_WINDOWS_DAYS = (730, 365, 90)
QUERY_TIMEOUT_SECONDS = 20.0
DOWNLOAD_TIMEOUT_SECONDS = 20.0

# Copies degraded to the validated spectrum's resolution, sampled on a
# regular grid in ln(wavelength) -- where a constant resolving power is a
# constant width -- with this many points per resolution element
DEGRADED_POINTS_PER_ELEMENT = 8
# H-alpha equivalent width, measured at the validated spectrum's resolution
EW_INTEGRATION_A = (6535.0, 6590.0)
EW_CONTINUUM_A = ((6510.0, 6530.0), (6595.0, 6615.0))
EW_MIN_CONTINUUM_POINTS = 5
DEGRADE_ABOVE_FACTOR = 1.5           # degraded only when R is this much higher
HR_STEP_A = 1.0                      # R unknown: a finer sampling means a higher resolution

# One robot line: name (fixed 15-character field), HD (0 = none), RA, DEC,
# magnitude, last observation (date and time, 0000-00-00 = never), BeSS
# priority, requested period (days). Shared with be_scheduler.
ROBOT_LINE = re.compile(
    r"^(.{15})(\S*)\s+([+-]\d\d \d\d [\d.]+)\s+([+-]\d\d \d\d [\d.]+)\s+(\S+)\s+"
    r"(\S+ \S+|\S+)\s+(\S+)\s+(\S+)\s*$"
)
ROBOT_HEADER = ("Name", "HD", "RA", "DEC", "Magn", "Last_Obs", "Prior.", "Obs_Period")

_HD_NAME = re.compile(r"^HD\s*(\d+)$", re.IGNORECASE)


class BessArchiveError(Exception):
    """BeSS could not be read (network, unexpected answer)."""


class BessObservation(NamedTuple):
    """One BeSS spectrum, as listed by the SSAP service (the H-alpha order of an echelle one)."""
    start: datetime
    directory: str           # observer and instrument directory of the file in BeSS
    target_name: str         # BeSS's name of the star
    url: str
    spectrum_id: str         # BeSS's own number


# ─────────────────────────────────────────────────────────────
# Robot file
# ─────────────────────────────────────────────────────────────

def is_robot_file(text: str) -> bool:
    lines = text.splitlines()
    return len(lines) > 2 and tuple(lines[1].split()) == ROBOT_HEADER


def bess_names_by_hd(text: str) -> dict[int, str]:
    """BeSS name of each star of the robot file, keyed by HD number (stars without HD left out)."""
    names: dict[int, str] = {}
    for line in text.splitlines()[2:]:
        match = ROBOT_LINE.match(line)
        if match and match.group(2).isdigit() and int(match.group(2)) > 0:
            names[int(match.group(2))] = match.group(1).strip()
    return names


def _robot_name(names: list[str]) -> Optional[str]:
    """BeSS name given by the robot file for the star's HD alias, if any."""
    numbers = [int(m.group(1)) for m in (_HD_NAME.match(n.strip()) for n in names) if m]
    if not numbers:
        return None
    path = Path(SUBPLUGIN_PARAMS.get("bess_archive", {}).get("robot_path", DEFAULT_ROBOT_PATH)).expanduser()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.warning(f"[BESS] Robot file unavailable ({path}: {e}) — the star is looked up by its names")
        return None
    if not is_robot_file(text):
        log.warning(f"[BESS] Robot file unreadable ({path}) — the star is looked up by its names")
        return None
    by_hd = bess_names_by_hd(text)
    return next((by_hd[n] for n in numbers if n in by_hd), None)


# ─────────────────────────────────────────────────────────────
# SSAP queries
# ─────────────────────────────────────────────────────────────

def parse_votable(text: str) -> Optional[list[dict]]:
    """Rows of an SSAP answer as {field ID: text}; None when BeSS reports an
    overflow (too many matching rows, none returned)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise BessArchiveError(f"unreadable BeSS answer ({e})")
    tag = lambda name: f"{root.tag.split('}')[0]}}}{name}" if root.tag.startswith("{") else name
    status = next((info.get("value") for info in root.iter(tag("INFO")) if info.get("name") == "QUERY_STATUS"), None)
    if status == "OVERFLOW":
        return None
    if status != "OK":
        raise BessArchiveError(f"BeSS answered QUERY_STATUS={status!r}")
    fields = [f.get("ID") or f.get("name") for f in root.iter(tag("FIELD"))]
    return [dict(zip(fields, [(td.text or "") for td in tr])) for tr in root.iter(tag("TR"))]


async def _ssap_query(client: httpx.AsyncClient, params: dict) -> str:
    response = await client.get(SSAP_URL, params=params, timeout=QUERY_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text


async def _query_rows(client: httpx.AsyncClient, target: dict, now: datetime) -> list[dict]:
    """Every row of a target overlapping H-alpha; when the whole history
    overflows, the most recent window that does not."""
    for days in (None, *OVERFLOW_WINDOWS_DAYS):
        start = "2000-01-01" if days is None else f"{now - timedelta(days=days):%Y-%m-%d}"
        params = {**target, "BAND": HALPHA_BAND, "TIME": f"{start}/{now + timedelta(days=1):%Y-%m-%d}"}
        try:
            rows = parse_votable(await _ssap_query(client, params))
        except httpx.HTTPError as e:
            raise BessArchiveError(f"BeSS unreachable ({type(e).__name__}: {e})")
        if rows is not None:
            return rows
    raise BessArchiveError(f"too many BeSS spectra even over the last {OVERFLOW_WINDOWS_DAYS[-1]} days")


def _row_start(row: dict) -> datetime:
    return datetime.fromisoformat(row["boundsTimeStart"].strip()).replace(tzinfo=timezone.utc)


def group_observations(rows: list[dict]) -> list[BessObservation]:
    """One observation per spectrum, most recent first: only rows holding
    H-alpha, and of an echelle spectrum (one row per order, same file
    directory and start) only one."""
    observations: list[BessObservation] = []
    for row in rows:
        try:
            if not float(row["boundsSpectralStart"]) < HALPHA_A * 1e-10 < float(row["boundsSpectralStop"]):
                continue
            start = _row_start(row)
        except (KeyError, ValueError):
            continue
        url = row.get("acref", "")
        file_path = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("fits", [""])[0])
        directory = file_path.rsplit("/", 1)[0]
        if any(o.directory == directory and abs((o.start - start).total_seconds()) < SAME_SPECTRUM_SECONDS
               for o in observations):
            continue
        observations.append(BessObservation(start, directory, row.get("targetName", "").strip(), url,
                                            row.get("creatorDID", "").split(":")[-1]))
    return sorted(observations, key=lambda o: o.start, reverse=True)


async def find_bess_observations(client: httpx.AsyncClient, names: list[str],
                                 ra_deg: Optional[float], dec_deg: Optional[float],
                                 now: datetime) -> tuple[Optional[str], list[BessObservation]]:
    """The star's BeSS spectra, and how it was found: "robot" (BeSS name from
    the robot file), "name" (one of our names), "position" (coordinates only:
    to be checked), or None when BeSS has nothing for it."""
    robot = _robot_name(names)
    attempts: list[tuple[str, dict]] = []
    if robot:
        attempts.append(("robot", {"TARGETNAME": robot}))
    for name in dict.fromkeys(n.strip() for n in names):
        if name and name != robot:
            attempts.append(("name", {"TARGETNAME": name}))
    if ra_deg is not None and dec_deg is not None:
        attempts.append(("position", {"POS": f"{ra_deg},{dec_deg}", "SIZE": str(SEARCH_RADIUS_DEG)}))
    for found_by, target in attempts:
        observations = group_observations(await _query_rows(client, target, now))
        if observations:
            return found_by, observations
    return None, []


# ─────────────────────────────────────────────────────────────
# Spectra
# ─────────────────────────────────────────────────────────────

async def _download(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.content


async def cached_spectrum(client: httpx.AsyncClient, observation: BessObservation) -> Path:
    """The spectrum's file in BESS_CACHE_DIR, downloaded the first time."""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", observation.spectrum_id) or "unknown"
    path = BESS_CACHE_DIR / f"{name}.fits"
    if not path.is_file():
        try:
            content = await _download(client, observation.url)
        except httpx.HTTPError as e:
            raise BessArchiveError(f"download failed ({type(e).__name__}: {e})")
        if not content.startswith(b"SIMPLE"):
            raise BessArchiveError("the downloaded file is not a FITS file")
        BESS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, path)
    return path


def read_spectrum(path: Path) -> tuple[np.ndarray, np.ndarray, dict]:
    """Wavelengths (angstrom), flux and header keywords of a 1D spectrum:
    a 1D image with a linear axis, or a WAVE/FLUX table."""
    with fits.open(path) as hdul:
        header = {k: v for hdu in reversed(hdul) for k, v in hdu.header.items() if k}
        table = next((hdu for hdu in hdul if isinstance(hdu, fits.BinTableHDU)), None)
        if table is not None and table.data is not None and "WAVE" in table.columns.names:
            wavelength = np.asarray(table.data["WAVE"], float).ravel()
            flux = np.asarray(table.data["FLUX"], float).ravel()
        else:
            flux = np.asarray(hdul[0].data, float).ravel()
            h = hdul[0].header
            wavelength = h["CRVAL1"] + (np.arange(flux.size) + 1 - h.get("CRPIX1", 1)) * h["CDELT1"]
            if str(h.get("CUNIT1", "")).strip().lower() == "nm":
                wavelength = wavelength * 10
    order = np.argsort(wavelength)
    return wavelength[order], flux[order], header


def resolving_power(header: dict) -> Optional[float]:
    for key in ("BSS_ESRP", "BSS_ITRP"):
        try:
            value = float(header.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def scale_to_unit(wavelength: np.ndarray, flux: np.ndarray) -> Optional[np.ndarray]:
    """Flux divided by its median over the two continuum zones on either side
    of H-alpha (EW_CONTINUUM_A), the same rule for every spectrum, Rosace's
    included, so they all share one scale; None when the spectrum does not
    cover both zones (it is then shown as it is, and the page says so)."""
    finite = np.isfinite(wavelength) & np.isfinite(flux)
    zones = [finite & (wavelength >= lo) & (wavelength <= hi) for lo, hi in EW_CONTINUUM_A]
    if any(zone.sum() < EW_MIN_CONTINUUM_POINTS for zone in zones):
        return None
    level = np.median(flux[zones[0] | zones[1]])
    return flux / level if level > 0 else None


def degrade(wavelength: np.ndarray, flux: np.ndarray, own_r: Optional[float],
            target_r: float) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """The whole spectrum at resolving power target_r: Gaussian convolution
    of FWHM = wavelength / R (less the spectrum's own width when known), on a
    regular grid in ln(wavelength); None for a spectrum not finer than the
    target."""
    if wavelength.size < 2:
        return None
    if own_r is None:
        if np.median(np.diff(wavelength)) >= HR_STEP_A:
            return None
    elif own_r < DEGRADE_ABOVE_FACTOR * target_r:
        return None
    finite = np.isfinite(wavelength) & np.isfinite(flux) & (wavelength > 0)
    wavelength, flux = wavelength[finite], flux[finite]
    step = 1.0 / (DEGRADED_POINTS_PER_ELEMENT * target_r)
    ln_grid = np.arange(math.log(wavelength[0]), math.log(wavelength[-1]), step) if wavelength.size >= 2 else []
    if len(ln_grid) < 10 * DEGRADED_POINTS_PER_ELEMENT:
        return None
    sigma_target = 1.0 / (target_r * 2.3548)
    sigma_own = 1.0 / (own_r * 2.3548) if own_r else 0.0
    sigma = math.sqrt(max(sigma_target ** 2 - sigma_own ** 2, 0.0)) / step
    grid = np.exp(ln_grid)
    return grid, gaussian_filter1d(np.interp(grid, wavelength, flux), sigma, mode="nearest")


def halpha_equivalent_width(wavelength: np.ndarray, flux: np.ndarray) -> Optional[float]:
    """H-alpha equivalent width (angstrom, negative in emission, as in BeSS):
    the integral of 1 - F/Fc over EW_INTEGRATION_A, Fc a straight line fitted
    on the two EW_CONTINUUM_A zones; None when the spectrum does not cover
    them."""
    finite = np.isfinite(wavelength) & np.isfinite(flux)
    wavelength, flux = wavelength[finite], flux[finite]
    zones = [(wavelength >= lo) & (wavelength <= hi) for lo, hi in EW_CONTINUUM_A]
    inside = (wavelength >= EW_INTEGRATION_A[0]) & (wavelength <= EW_INTEGRATION_A[1])
    if (wavelength.size == 0 or wavelength[0] > EW_INTEGRATION_A[0] or wavelength[-1] < EW_INTEGRATION_A[1]
            or any(zone.sum() < EW_MIN_CONTINUUM_POINTS for zone in zones)):
        return None
    continuum_points = zones[0] | zones[1]
    slope, intercept = np.polyfit(wavelength[continuum_points], flux[continuum_points], 1)
    continuum = slope * wavelength[inside] + intercept
    if np.any(continuum <= 0):
        return None
    return float(np.trapezoid(1 - flux[inside] / continuum, wavelength[inside]))


def _numbers(values: np.ndarray, decimals: int) -> list:
    return [round(float(v), decimals) if math.isfinite(v) else None for v in values]


def _date_obs(header: dict) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(header["DATE-OBS"]).strip()).replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None


def _entry(source: str, path: Path, target_r: Optional[float], **extra) -> dict:
    """One spectrum of the page: scaled flux, labels, and the degraded H-alpha region."""
    wavelength, flux, header = read_spectrum(path)
    scaled = scale_to_unit(wavelength, flux)
    flux = flux if scaled is None else scaled
    own_r = resolving_power(header)
    degraded = degrade(wavelength, flux, own_r, target_r) if target_r and source != "rosace" else None
    # measured on what the "Résolution Rosace" view shows, so every value is comparable
    ew = halpha_equivalent_width(*(degraded if degraded else (wavelength, flux)))
    date = _date_obs(header)
    return {
        "source": source,
        "date_obs": date.isoformat() if date else None,
        "observer": str(header.get("OBSERVER", "")).strip() or None,
        "instrument": str(header.get("BSS_INST", "")).strip() or None,
        "site": str(header.get("BSS_SITE", "")).strip() or None,
        "resolving_power": round(own_r) if own_r else None,
        "wavelength": _numbers(wavelength, 3),
        "flux": _numbers(flux, 4),
        "degraded": {"wavelength": _numbers(degraded[0], 2), "flux": _numbers(degraded[1], 4)} if degraded else None,
        "halpha_ew": round(ew, 2) if ew is not None else None,
        "scaled": scaled is not None,
        **extra,
    }


# ─────────────────────────────────────────────────────────────
# The comparison set
# ─────────────────────────────────────────────────────────────

class _Rosace(NamedTuple):
    start: Optional[datetime]
    row: dict
    path: Path


def _rosace_spectrum(row: dict) -> Optional[_Rosace]:
    path = Path(row.get("session_dir") or "") / (row.get("final_spectrum") or "")
    if not path.is_file():
        return None
    try:
        start = _date_obs(dict(fits.getheader(path)))
    except OSError:
        return None
    return _Rosace(start, row, path)


def _same_time(a: Optional[datetime], b: Optional[datetime]) -> bool:
    return a is not None and b is not None and abs((a - b).total_seconds()) < SAME_SPECTRUM_SECONDS


async def previous_spectra(row: dict, now: Optional[datetime] = None) -> dict:
    """The spectrum being validated and the MAX_SPECTRA most recent other
    spectra of its star, from BeSS and from Rosace (a Rosace spectrum found in
    BeSS once, as "bess_rosace"); rejected Rosace spectra left out. A BeSS
    failure never hides the Rosace spectra: it is returned in bess_error."""
    now = now or datetime.now(timezone.utc)
    star = await star_get_by_name(row["star"])
    names = [row["star"]]
    if star:
        names = [star.name, *(await star_aliases_map([star.id])).get(star.id, [])]
        names += [row["star"]] if row["star"] not in names else []

    current = _rosace_spectrum(row)
    if current is None:
        raise BessArchiveError(f"final spectrum not found: {row.get('final_spectrum')}")
    target_r = resolving_power(dict(fits.getheader(current.path)))

    rosace = [s for s in (_rosace_spectrum(r) for r in await db_get_bess_validations_by_stars(names)
                          if r["observation_id"] != row["observation_id"] and r["status"] != "rejected")
              if s is not None]

    errors: list[str] = []
    found_by: Optional[str] = None
    bess: list[BessObservation] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            found_by, bess = await find_bess_observations(client, names, star.ra if star else None,
                                                          star.dec if star else None, now)
        except BessArchiveError as e:
            errors.append(str(e))
        bess = [o for o in bess if not _same_time(o.start, current.start)]

        # Most recent first; a BeSS spectrum taken at the time of a Rosace one stands for it
        candidates: list[tuple[datetime, str, object]] = []
        matched: set[int] = set()
        for o in bess:
            twin = next((i for i, s in enumerate(rosace) if i not in matched and _same_time(o.start, s.start)), None)
            if twin is not None:
                matched.add(twin)
            candidates.append((o.start, "bess" if twin is None else "bess_rosace", (o, twin)))
        candidates += [(s.start or datetime.min.replace(tzinfo=timezone.utc), "rosace", s)
                       for i, s in enumerate(rosace) if i not in matched]
        candidates.sort(key=lambda c: c[0], reverse=True)

        site = str(OBSERVATORY.get("site") or "").strip()
        spectra: list[dict] = []
        for _, source, item in candidates:
            if len(spectra) >= MAX_SPECTRA:
                break
            if source == "rosace":
                spectra.append(_entry("rosace", item.path, target_r, observation_id=item.row["observation_id"],
                                      status=item.row["status"]))
                continue
            o, twin = item
            try:
                entry = _entry(source, await cached_spectrum(client, o), target_r,
                               bess_target_name=o.target_name, bess_id=o.spectrum_id)
            except (BessArchiveError, OSError, KeyError, ValueError) as e:
                errors.append(f"BeSS spectrum {o.spectrum_id}: {e}")
                continue
            if source == "bess_rosace":
                if site and entry["site"] and entry["site"] != site:
                    entry["source"] = "bess"       # another site at the same time: two spectra
                    spectra.append(entry)
                    entry = _entry("rosace", rosace[twin].path, target_r,
                                   observation_id=rosace[twin].row["observation_id"],
                                   status=rosace[twin].row["status"])
                else:
                    entry["observation_id"] = rosace[twin].row["observation_id"]
            spectra.append(entry)
        spectra = sorted(spectra, key=lambda e: e["date_obs"] or "", reverse=True)[:MAX_SPECTRA]

    log.info(f"[BESS] {row['star']}: {len(bess)} BeSS spectra"
             f"{f' (found by {found_by})' if found_by else ''}, {len(rosace)} other Rosace spectra, "
             f"{len(spectra)} shown{f' — {len(errors)} problem(s)' if errors else ''}")
    for error in errors:
        log.warning(f"[BESS] {row['star']}: {error}")
    return {
        "found_by": found_by,
        "bess_error": "; ".join(errors) or None,
        "current": _entry("current", current.path, None, observation_id=row["observation_id"]),
        "spectra": spectra,
    }
