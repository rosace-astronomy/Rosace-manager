"""
REDUCTION sub-plugin — FRIPON sky image
The all-sky image of a FRIPON camera at the time of the observation, with the target and a dozen bright stars marked.

FRIPON (Fireball Recovery and InterPlanetary Observation Network) runs
all-sky cameras that take one image every 10 minutes; station FRRA12 is at
St-Pancrasse. This step picks the image of subplugin_params.fripon.station
nearest the middle of the science exposures, and draws on it the target (a
red open cross) and the bright stars (fisheye.py), so one sees at once
whether the sky was clear and where the target was. Shown on its own page of
the summary PDF and on the BeSS validation page.

FRIPON, as found on 2026-10-18:
- the capture list (fireball.fripon.org/ajax/liste_capture.ajax.php, the
  « Capture » page) covers the last 24 hours of every station; its search
  filters fail server-side, so the whole list (some 18 000 rows, 4 MB) is
  read and the station's rows kept here. Older images cannot be reached:
  this step runs right after the exposures;
- an image (protect.php?img=capture/...) needs a guest session: any page
  of the site is opened first for its cookie.

The capture must be within max_offset_minutes (15) of mid-observation:
otherwise none is used, and when the station's latest capture is itself
older, it is reported as stopped. Nothing is done twice for the same
capture (the observations of the same 10 minutes, a reduction run again):
a capture already in the session directory (FRIPON's own name) less than
5 minutes from mid-observation is necessarily the nearest one, so FRIPON's
list is not even read; one found in the list is not downloaded again; and
its lens fit is kept next to it ("<capture>.json") and read back -- only
the annotated image, which marks this observation's target, is drawn again.

Positions: the lens model is fitted on each image's own stars (fisheye.fit,
a few seconds): from the model stored in subplugin_params.fripon.lens when
there is one (scripts/fit_fripon_lens.py), searched over every orientation
otherwise -- so nothing needs configuring. A fit is kept only with at least
15 stars and an rms under MAX_RMS_PX; failing that (clouds), the stored
model is used as is, which places the target even through clouds; with no
stored model either, no marks (the image alone).

Writes, in the session directory, the capture (FRIPON's name) and
"<final name>_fripon_annotated.png"; shared key reduction_data["sky_camera"].
Disabled without subplugin_params.fripon.station. Never blocking: FRIPON
unreachable, no capture close enough, an unreadable image are WARNINGs.
"""

from __future__ import annotations

DISPLAY_NAME = "FRIPON"

import io
import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple, Optional

import httpx
import numpy as np
from astropy.io import fits
from matplotlib.figure import Figure
from matplotlib.image import imread
from matplotlib.patches import Circle

from ....astro_math import altitude_deg, azimuth_deg
from ....config import SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG, SUBPLUGIN_PARAMS
from ....file_names import final_name
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from .fisheye import LensModel, fit, sky_disk, sky_positions, to_gray

log = logging.getLogger("spectro.subplugin.fripon_image")

SITE_URL = "https://fireball.fripon.org"
GUEST_PAGE = "/list_capture.php"            # opened first: the guest session cookie
LIST_PATH = "/ajax/liste_capture.ajax.php"
IMAGE_PATH = "/protect.php"
LIST_LENGTH = 50000                         # rows asked for: the whole last 24 hours
TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_OFFSET_MINUTES = 15.0           # one capture every 10 minutes: 5 at most while the station runs

LABELLED_STARS = 12
LABEL_SPACING_PX = 70
POLARIS = "* alf UMi"
CROSS_GAP_PX, CROSS_ARM_PX = 7, 22          # the open centre keeps the target itself visible
MAX_RMS_PX = 2.0                            # a fit farther off is not trusted (about 1 px on clear images)

_IMAGE_SOURCE = re.compile(r"img=(capture/[^\"']+?)-500x500\.jpg")
_CAPTURE_NAME = re.compile(r"[A-Z0-9]+_(\d{8}T\d{6})_UT\.jpg")
CAPTURE_INTERVAL_S = 600                    # FRIPON: one capture every 10 minutes


class Capture(NamedTuple):
    time: datetime              # UTC
    path: str                   # FRIPON's path of the full image, e.g. capture/FRRA12/202610/FRRA12_..._UT-0.jpg

    @property
    def file_name(self) -> str:
        """The capture's own name, kept in the session directory: FRRA12_20261018T071517_UT.jpg."""
        return Path(self.path).name.replace("_UT-0.jpg", "_UT.jpg")


def parse_capture_list(payload: dict, station: str) -> list[Capture]:
    """The station's captures in the list, most recent first."""
    if payload.get("error"):
        raise ValueError(f"FRIPON list refused: {payload['error']}")
    captures = []
    for row in payload.get("data", []):
        if len(row) < 5 or row[1] != station:
            continue
        match = _IMAGE_SOURCE.search(row[4])
        if not match:
            continue
        time = datetime.strptime(row[3], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        captures.append(Capture(time, f"{match.group(1)}.jpg"))
    return sorted(captures, key=lambda c: c.time, reverse=True)


def _list_params() -> dict:
    params = {"draw": 1, "start": 0, "length": LIST_LENGTH, "order[0][column]": 3, "order[0][dir]": "desc"}
    for i in range(5):
        params.update({f"columns[{i}][data]": i, f"columns[{i}][searchable]": "true",
                       f"columns[{i}][orderable]": "true", f"columns[{i}][search][value]": ""})
    return params


async def fetch_captures(client: httpx.AsyncClient, station: str) -> list[Capture]:
    await client.get(SITE_URL + GUEST_PAGE)
    response = await client.get(SITE_URL + LIST_PATH, params=_list_params())
    response.raise_for_status()
    return parse_capture_list(response.json(), station)


async def download_capture(client: httpx.AsyncClient, capture: Capture) -> bytes:
    response = await client.get(SITE_URL + IMAGE_PATH, params={"img": capture.path})
    response.raise_for_status()
    if not response.content.startswith(b"\xff\xd8"):
        raise ValueError(f"not a JPEG image ({response.headers.get('content-type')}, {len(response.content)} bytes)")
    return response.content


def nearest_capture(captures: list[Capture], when: datetime) -> Optional[Capture]:
    return min(captures, key=lambda c: abs((c.time - when).total_seconds()), default=None)


def _duration(seconds: float) -> str:
    minutes = round(abs(seconds) / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def mid_observation(obs: Observation) -> datetime:
    """Middle of the science exposures: first DATE-OBS, last DATE-OBS + EXPTIME."""
    names = obs.sequencer_data.get("star_spectra_images") or []
    if not names or not obs.obs_dir:
        raise ValueError("no science frame (sequencer_data['star_spectra_images'])")
    first = fits.getheader(Path(obs.obs_dir) / names[0])
    last = fits.getheader(Path(obs.obs_dir) / names[-1])
    start = datetime.fromisoformat(str(first["DATE-OBS"])).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(str(last["DATE-OBS"])).replace(tzinfo=timezone.utc) + timedelta(seconds=float(last.get("EXPTIME", 0)))
    return start + (end - start) / 2


def _labelled(positions, matched: Optional[np.ndarray], x: np.ndarray, y: np.ndarray) -> list[int]:
    """About LABELLED_STARS stars to name: Polaris first when visible, then the
    brightest (identified ones only when a fit found some), LABEL_SPACING_PX apart."""
    order = sorted(range(len(positions.stars)), key=lambda i: (positions.stars[i].simbad_name != POLARIS, positions.v[i]))
    chosen: list[int] = []
    for i in order:
        if not positions.stars[i].usual_name or (matched is not None and not matched[i]):
            continue
        if any(math.hypot(x[i] - x[j], y[i] - y[j]) < LABEL_SPACING_PX for j in chosen):
            continue
        chosen.append(i)
        if len(chosen) == LABELLED_STARS:
            break
    return chosen


def draw_annotated(path: Path, image: np.ndarray, caption: str, model: Optional[LensModel], positions,
                   matched: Optional[np.ndarray], target: Optional[tuple[str, float, float]]) -> Optional[list[float]]:
    """The image with the identified stars (green circles), a dozen names,
    N/E/S/W on the horizon and the target (red open cross); returns the
    target's pixel, or None."""
    gray = to_gray(image)
    height, width = gray.shape
    disk = sky_disk(gray)[0]
    figure = Figure(figsize=(width / 100, height / 100), dpi=100)
    axes = figure.add_axes((0, 0, 1, 1))
    axes.imshow(gray, cmap="gray", vmin=np.percentile(gray[disk], 2), vmax=np.percentile(gray[disk], 99.7))
    axes.set_xlim(-0.5, width - 0.5)
    axes.set_ylim(height - 0.5, -0.5)
    axes.set_axis_off()
    target_pixel = None
    if model is not None:
        x, y = model.project(positions.alt, positions.az)
        if matched is not None:
            for i in np.nonzero(matched)[0]:
                axes.add_patch(Circle((x[i], y[i]), 7, fill=False, color="lime", lw=0.8))
        for i in _labelled(positions, matched, x, y):
            axes.text(x[i] + 10, y[i] - 6, positions.stars[i].usual_name, color="yellow", fontsize=10, fontweight="bold")
        for label, azimuth in (("N", 0.0), ("E", 90.0), ("S", 180.0), ("W", 270.0)):
            (hx,), (hy,) = model.project([2.0], [azimuth])
            axes.text(hx, hy, label, color="cyan", fontsize=14, ha="center", va="center")
        if target is not None:
            name, alt, az = target
            (tx,), (ty,) = model.project([alt], [az])
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                axes.plot([tx + dx * CROSS_GAP_PX, tx + dx * CROSS_ARM_PX], [ty + dy * CROSS_GAP_PX, ty + dy * CROSS_ARM_PX],
                          color="red", lw=2.2)
            axes.text(tx + CROSS_ARM_PX + 4, ty + CROSS_ARM_PX + 2, name, color="red", fontsize=11, fontweight="bold")
            target_pixel = [round(float(tx), 1), round(float(ty), 1)]
    axes.text(width - 10, 12, caption, color="white", fontsize=10, ha="right", va="top")
    figure.savefig(path, dpi=100)
    return target_pixel


def local_capture(session_dir: Path, station: str, when: datetime) -> Optional[Capture]:
    """A capture already in the session directory taken less than half a
    capture interval from `when`: necessarily the nearest one, so FRIPON's
    list need not be read again."""
    best = None
    for path in session_dir.glob(f"{station}_*_UT.jpg"):
        match = _CAPTURE_NAME.fullmatch(path.name)
        if not match:
            continue
        time = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        if abs((time - when).total_seconds()) <= CAPTURE_INTERVAL_S / 2 and (
                best is None or abs((time - when).total_seconds()) < abs((best.time - when).total_seconds())):
            best = Capture(time, f"capture/{station}/{time:%Y%m}/{station}_{match.group(1)}_UT-0.jpg")
    return best


def _positions(raw: Path, image: np.ndarray, when: datetime, latitude: float, longitude: float,
               stored: Optional[LensModel]):
    """The lens model of a capture and the stars it identified, kept next to
    the capture ("<capture>.json") so the next observation of the same
    10 minutes reads them instead of fitting again -- as long as the stored
    lens model has not changed. Returns (model, method, matched star names,
    rms, matched count, read from the file); method "fitted", "stored" (too
    few stars, the stored model as is) or None (too few stars, no stored model)."""
    stored_config = stored.to_config() if stored else None
    cache = raw.with_suffix(".json")
    try:
        kept = json.loads(cache.read_text())
        if kept.get("stored_lens") == stored_config:
            if kept["method"] is None:
                return None, None, None, None, 0, True
            model = LensModel.from_config(kept["lens"])
            matched = set(kept["matched"]) if kept["method"] == "fitted" else None
            return model, kept["method"], matched, kept["rms_px"], kept["matched_stars"], True
    except (OSError, ValueError, KeyError):
        pass
    result = fit(image, when, latitude, longitude, start=stored)
    if result is not None and result.rms_px <= MAX_RMS_PX:
        model, method, rms, count = result.model, "fitted", round(result.rms_px, 2), result.matched_count
        matched = {s.simbad_name for s, m in zip(result.positions.stars, result.matched) if m}
    else:
        if result is not None:
            log.info(f"[FRIPON] Lens fit not trusted on {raw.name}: rms {result.rms_px:.1f} px > {MAX_RMS_PX} px")
        model, method, rms, count, matched = stored, ("stored" if stored else None), None, 0, None
    cache.write_text(json.dumps({"stored_lens": stored_config, "lens": model.to_config() if model else None,
                                 "method": method, "rms_px": rms, "matched_stars": count,
                                 "matched": sorted(matched or [])}, indent=1))
    return model, method, matched, rms, count, False


class FriponImagePlugin(SubPlugin):
    """Fetches the FRIPON all-sky image nearest the observation and marks the target and a dozen bright stars on it."""

    async def execute(self, obs: Observation) -> Observation:
        params = SUBPLUGIN_PARAMS.get("fripon") or {}
        station = params.get("station")
        if not station:
            log.info("[FRIPON] Not used: subplugin_params.fripon.station is not set")
            return obs
        try:
            await self._run(obs, params, station)
        except Exception as error:
            log.warning(f"[FRIPON] No sky image for this observation: {error}")
        return obs

    async def _run(self, obs: Observation, params: dict, station: str) -> None:
        max_offset_s = 60 * float(params.get("max_offset_minutes", DEFAULT_MAX_OFFSET_MINUTES))
        latitude = float(params.get("latitude_deg", SITE_LATITUDE_DEG))
        longitude = float(params.get("longitude_deg", SITE_LONGITUDE_DEG))
        mid = mid_observation(obs)
        session_dir = Path(obs.obs_dir)

        capture, source = local_capture(session_dir, station, mid), "already in the session directory"
        if capture is None:
            capture, source = await self._fetch(session_dir, station, mid, max_offset_s)
        offset_s = (capture.time - mid).total_seconds()
        raw = session_dir / capture.file_name

        image = imread(io.BytesIO(raw.read_bytes()), format="jpeg")
        stored = LensModel.from_config(params["lens"]) if params.get("lens") else None
        model, method, matched, rms, matched_count, cached = _positions(raw, image, capture.time, latitude, longitude, stored)
        positions = sky_positions(capture.time, latitude, longitude)
        if matched is not None:
            matched = np.array([s.simbad_name in matched for s in positions.stars])

        target = obs.scheduler_data.get("target")
        name = obs.scheduler_data.get("queried_name") or (target.name if target else "")
        target_altaz = None
        if target is not None:
            target_altaz = (name, altitude_deg(target.ra, target.dec, latitude, longitude, capture.time),
                            azimuth_deg(target.ra, target.dec, latitude, longitude, capture.time))
        sign = "+" if offset_s >= 0 else "-"
        positions_text = {"fitted": f"positions fitted on {matched_count} stars",
                          "stored": "positions from the stored lens model (too few stars)",
                          None: "too few stars and no stored lens model: no positions"}[method]
        caption = (f"FRIPON {station} {capture.time:%Y-%m-%d %H:%M:%S} UT, "
                   f"{sign}{_duration(offset_s)} from mid-observation -- {positions_text}")
        annotated = f"{final_name(obs)}_fripon_annotated.png"
        target_pixel = draw_annotated(session_dir / annotated, image, caption, model, positions, matched, target_altaz)

        obs.reduction_data["sky_camera"] = {
            "station": station,
            "capture_time": capture.time.isoformat(),
            "offset_s": round(offset_s),
            "file": raw.name,
            "annotated_file": annotated,
            "model": method,
            "matched_stars": matched_count,
            "rms_px": rms,
            "target_pixel": target_pixel,
        }
        log.info(f"[FRIPON] {station} {capture.time:%H:%M:%S} UT ({sign}{_duration(offset_s)}, {source}), "
                 f"{positions_text}{' (fit of an earlier observation)' if cached else ''} -> {annotated}")

    async def _fetch(self, session_dir: Path, station: str, mid: datetime, max_offset_s: float) -> tuple[Capture, str]:
        """The capture nearest mid-observation in FRIPON's list, downloaded
        unless already in the session directory."""
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            try:
                captures = await fetch_captures(client, station)
            except httpx.HTTPError as error:
                raise RuntimeError(f"FRIPON unreachable ({type(error).__name__}: {error})")
            capture = nearest_capture(captures, mid)
            if capture is None:
                raise RuntimeError(f"no capture of {station} in FRIPON's list of the last 24 hours -- station stopped?")
            offset_s = (capture.time - mid).total_seconds()
            if abs(offset_s) > max_offset_s:
                stopped = (" -- station stopped?"
                           if (datetime.now(timezone.utc) - captures[0].time).total_seconds() > max_offset_s else "")
                raise RuntimeError(f"nearest capture of {station} ({capture.time:%Y-%m-%d %H:%M:%S} UT) is "
                                   f"{_duration(offset_s)} from mid-observation, more than "
                                   f"{_duration(max_offset_s)}{stopped}")
            raw = session_dir / capture.file_name
            if raw.exists():
                return capture, "already downloaded"
            content = await download_capture(client, capture)
        tmp = raw.with_name(raw.name + ".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, raw)
        return capture, "downloaded"
