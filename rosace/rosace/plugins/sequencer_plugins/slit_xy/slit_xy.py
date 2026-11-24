"""
SEQUENCER sub-plugin -- measures the spectrograph slit's position in the guiding camera's field, "indi" backend only.

Runs between slew_72ed (mount in its final position) and pointing_72ed
(which puts the star on the slit with PHD2): the slit's position in the
guiding image can move with the telescope's own flexure, and the configured
default (subplugin_params.guiding.slit_x/slit_y) was measured once, by eye.

Captures one guide image with the spectroscope on a chosen light source
(CALIB or FLAT: a bright, star-free, reproducible field; SKY also works,
against the sky background), pausing light_settle_seconds between the
spectroscope command and the exposure so the lamp is really on. In every
case the slit shows as a dark line on a brighter background, so
axes_helpers.find_axes(invert=True) locates it -- on the whole frame, or
on a search zone centred on the default slit position. The slit is a
slightly tilted line, so only its Y at a given column is meaningful: X
stays the configured slit_x (where along the slit the star should sit), and
Y is the measured line's height at that column.

subplugin_params.slit_xy.offset_y (pixels, 0 by default) is then added to
the measured Y: the slit seen against the calibration lamp is not exactly
where the sky's light -- the star's -- goes through it (measurements in
HISTORY.md). A static correction, set in the configuration, better than none.

Never blocking: any failure (unusable light source, capture error, nothing
found, a feature that doesn't look like the slit) logs a WARNING and leaves
the default position in force -- except SessionFatalError from the
guiding-chain recovery, which must still stop the session. The spectroscope
is always switched back to SKY afterwards, followed by the same
light_settle_seconds pause: a lamp can keep shining for a moment after its
power is cut, and the next guide exposure must not catch that light.

Casier: sequencer_data["slit_xy"] = {"status": "calculated", "slit_x",
"slit_y", "angle_deg", "segment", "image"} or {"status": "default", "image"}
(image None when the capture itself failed). segment is the measured slit as
[xa, ya, xb, yb]: its two ends (where the dark line is at least half as
deep as at its deepest), on the fitted line (the slit as seen, without offset_y) -- drawn
by the observation summary. acquisition_helpers.py::slit_position() is what
pointing_72ed and stop_phd2_guiding read.

Pixel coordinates follow the app-wide convention (0-indexed, Y up), the same
as slit_x/slit_y, find_axes() and world_to_pixel() -- no conversion needed
beyond adding the search zone's own corner offset.
"""

from __future__ import annotations

DISPLAY_NAME = "Slit XY"
TIME_CATEGORY = "pointing"

import asyncio
import logging
import math
from dataclasses import dataclass

import numpy as np
from astropy.io import fits

from ....config import SUBPLUGIN_PARAMS
from ....errors import SessionFatalError
from ....hardware.spectroscope import LightSource
from ....models import Observation
from ...reduction_plugins.axes_helpers import find_axes
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import capture_guide_image, publish_guide_preview

log = logging.getLogger("spectro.subplugin.slit_xy")

# DARK is deliberately absent: the slit would not be visible at all.
ALLOWED_LIGHT_SOURCES = ("CALIB", "FLAT", "SKY")

# A detection is only trusted when it looks like the slit: a long, thin feature...
MIN_ELONGATION = 10.0
# ...close to horizontal.
MAX_ANGLE_DEG = 10.0


# Ends of the slit: columns where the dark line, sampled END_HALF_ROWS rows
# either side of the fitted line and smoothed over END_SMOOTH_COLUMNS, is at
# least END_DEPTH_FRACTION of its deepest
END_HALF_ROWS = 2
END_SMOOTH_COLUMNS = 9
END_DEPTH_FRACTION = 0.5


class SlitMeasurementError(Exception):
    """Raised when the image gives no trustworthy slit position; the message is the reason logged."""


@dataclass
class SearchZone:
    """Inclusive-exclusive pixel index bounds of the search zone: columns
    x0..x1-1, rows y0..y1-1 of the FITS data array."""

    x0: int
    y0: int
    x1: int
    y1: int

    def edges(self) -> list[float]:
        """Outer edges of the zone in Rosace coordinates (pixel centres on
        integers), for the GUI overlay."""
        return [self.x0 - 0.5, self.y0 - 0.5, self.x1 - 0.5, self.y1 - 0.5]


def parse_light_source(value) -> LightSource:
    name = str(value).strip().upper()
    if name not in ALLOWED_LIGHT_SOURCES:
        raise SlitMeasurementError(
            f"light_source {value!r} not usable (expected one of {', '.join(ALLOWED_LIGHT_SOURCES)})"
        )
    return LightSource[name]


def parse_area(value, key: str) -> float | None:
    """None/absent or "none" (any case) means the whole width/height."""
    if value is None or (isinstance(value, str) and value.strip().lower() == "none"):
        return None
    try:
        area = float(value)
    except (TypeError, ValueError):
        raise SlitMeasurementError(f"{key} {value!r} is neither a number nor none")
    if area <= 0:
        raise SlitMeasurementError(f"{key} {value!r} must be positive")
    return area


def parse_offset(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise SlitMeasurementError(f"offset_y {value!r} is not a number")


def search_zone(
    shape: tuple[int, int], slit_x: float, slit_y: float, x_area: float | None, y_area: float | None
) -> SearchZone:
    """Zone of total size x_area by y_area centred on (slit_x, slit_y),
    clipped to the frame; a None size spans the whole frame on that axis."""
    height, width = shape

    def _bounds(center: float, area: float | None, size: int) -> tuple[int, int]:
        if area is None:
            return 0, size
        low = max(0, int(round(center - area / 2)))
        high = min(size, int(round(center + area / 2)))
        return low, high

    x0, x1 = _bounds(slit_x, x_area, width)
    y0, y1 = _bounds(slit_y, y_area, height)
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise SlitMeasurementError(f"search zone x {x0}-{x1}, y {y0}-{y1} is empty or outside the frame")
    return SearchZone(x0, y0, x1, y1)


@dataclass
class SlitMeasurement:
    """The slit line found in the image, in full-frame Rosace coordinates."""

    y_at_slit_x: float
    center_x: float
    center_y: float
    angle_deg: float
    x_first: float = 0.0     # first and last columns of the slit (_slit_ends())
    x_last: float = 0.0

    def y_at(self, x: float) -> float:
        return self.center_y + (x - self.center_x) * math.tan(math.radians(self.angle_deg))

    def segment(self) -> list[float]:
        """The detected slit as [xa, ya, xb, yb]: its two ends on the fitted line."""
        return [round(self.x_first, 2), round(self.y_at(self.x_first), 2),
                round(self.x_last, 2), round(self.y_at(self.x_last), 2)]


def measure_slit(data: np.ndarray, zone: SearchZone, slit_x: float) -> SlitMeasurement:
    """Locates the dark slit line in the zone and returns its height at column slit_x.

    Each column's median is subtracted first: on a lamp-lit field, a few
    percent of illumination gradient along the slit is already many times
    the noise, and find_axes() would otherwise take the darker end of the
    zone for the feature. The slit covers only a few rows of each column, so
    the column median is the local background, not the slit."""
    crop = data[zone.y0:zone.y1, zone.x0:zone.x1].astype(np.float64)
    flattened = crop - np.median(crop, axis=0, keepdims=True)
    axes = find_axes(flattened, invert=True)
    if axes is None:
        raise SlitMeasurementError("no dark feature found")
    if axes.sigma_minor <= 0 or axes.sigma_major / axes.sigma_minor < MIN_ELONGATION:
        raise SlitMeasurementError(
            f"feature not elongated enough to be the slit (sigma {axes.sigma_major:.1f} x {axes.sigma_minor:.1f} px)"
        )
    if abs(axes.angle_deg) > MAX_ANGLE_DEG:
        raise SlitMeasurementError(f"feature too far from horizontal ({axes.angle_deg:+.1f} deg)")

    center_x, center_y = axes.cx + zone.x0, axes.cy + zone.y0
    measurement = SlitMeasurement(
        y_at_slit_x=0.0, center_x=center_x, center_y=center_y, angle_deg=axes.angle_deg
    )
    measurement.x_first, measurement.x_last = _slit_ends(flattened, zone, measurement)
    measurement.y_at_slit_x = measurement.y_at(slit_x)
    if not zone.y0 - 0.5 <= measurement.y_at_slit_x <= zone.y1 - 0.5:
        raise SlitMeasurementError(
            f"slit height {measurement.y_at_slit_x:.2f} at x={slit_x} falls outside the search zone"
        )
    return measurement


def _slit_ends(flattened: np.ndarray, zone: SearchZone, measurement: SlitMeasurement) -> tuple[float, float]:
    """First and last columns of the slit along the fitted line: where the
    line's depth (how much darker than its column's median, over a few rows
    around the line, smoothed along it) reaches END_DEPTH_FRACTION of its
    deepest, in the run of columns containing the deepest point. A faint
    dark trace beyond the slit itself is left out."""
    columns = np.arange(flattened.shape[1])
    rows = np.round([measurement.y_at(x + zone.x0) - zone.y0 for x in columns]).astype(int)
    offsets = np.arange(-END_HALF_ROWS, END_HALF_ROWS + 1)
    sample = np.clip(rows[None, :] + offsets[:, None], 0, flattened.shape[0] - 1)
    depth = -flattened[sample, columns[None, :]].min(axis=0)
    depth = np.convolve(depth, np.ones(END_SMOOTH_COLUMNS) / END_SMOOTH_COLUMNS, mode="same")
    deepest = int(np.argmax(depth))
    deep = depth >= END_DEPTH_FRACTION * depth[deepest]
    first = deepest
    while first > 0 and deep[first - 1]:
        first -= 1
    last = deepest
    while last < len(deep) - 1 and deep[last + 1]:
        last += 1
    return float(first + zone.x0), float(last + zone.x0)


class SlitXyPlugin(SubPlugin):
    """Measures the slit's Y at the configured slit_x column in a lamp-lit
    (or sky) guide image; keeps the default position on any failure."""

    async def execute(self, obs: Observation) -> Observation:
        queried_name = obs.scheduler_data.get("queried_name")
        guiding_params = SUBPLUGIN_PARAMS.get("guiding", {})
        default_x, default_y = guiding_params["slit_x"], guiding_params["slit_y"]
        params = SUBPLUGIN_PARAMS.get("slit_xy", {})

        image_path = None
        zone = None
        try:
            light_source = parse_light_source(params.get("light_source", "CALIB"))
            x_area = parse_area(params.get("slit_x_area"), "slit_x_area")
            y_area = parse_area(params.get("slit_y_area"), "slit_y_area")
            offset_y = parse_offset(params.get("offset_y", 0.0))
            light_settle_seconds = params.get("light_settle_seconds", 1.0)
            try:
                image_path = await capture_guide_image(
                    obs, params.get("exposure_seconds", 1.0),
                    target_name=queried_name,
                    light_source=light_source,
                    light_settle_seconds=light_settle_seconds,
                    subtract_dark=False,
                )
            finally:
                await _restore_sky(obs, light_settle_seconds)
            data = fits.getdata(image_path)
            zone = search_zone(data.shape, default_x, default_y, x_area, y_area)
            measurement = measure_slit(data, zone, default_x)
        except SessionFatalError:
            raise
        except Exception as e:
            image_name = image_path.name if image_path else None
            obs.sequencer_data["slit_xy"] = {"status": "default", "image": image_name}
            log.warning(
                f"[SLIT_XY] Slit calculation failed on {image_name or 'no image'} ({e}), "
                f"using default ({default_x}, {default_y})"
            )
            await _publish(obs, image_path, zone, None, None)
            return obs

        slit_y = round(measurement.y_at_slit_x + offset_y, 2)
        obs.sequencer_data["slit_xy"] = {
            "status": "calculated",
            "slit_x": round(float(default_x), 2),
            "slit_y": slit_y,
            "angle_deg": round(measurement.angle_deg, 3),
            "segment": measurement.segment(),
            "image": image_path.name,
        }
        obs.measurements["slit_y_px"] = slit_y
        log.info(
            f"[SLIT_XY] Slit calculated at ({float(default_x):.2f}, {slit_y:.2f}) px on {image_path.name} "
            f"(measured {measurement.y_at_slit_x:.2f}, offset {offset_y:+.2f}; "
            f"default ({default_x}, {default_y}), angle {measurement.angle_deg:+.2f} deg)"
        )
        await _publish(obs, image_path, zone, measurement, (float(default_x), slit_y))
        return obs


async def _restore_sky(obs: Observation, light_settle_seconds: float) -> None:
    """Best effort: the next guide capture switches to SKY on its own anyway.
    Then waits light_settle_seconds, so the lamp is really off before the
    next exposure."""
    try:
        await obs.hardware["spectroscope"].set_light_source(LightSource.SKY)
    except Exception as e:
        log.warning(f"[SLIT_XY] Could not switch the spectroscope back to SKY: {e}")
    if light_settle_seconds > 0:
        await asyncio.sleep(light_settle_seconds)


async def _publish(
    obs: Observation,
    image_path,
    zone: SearchZone | None,
    measurement: SlitMeasurement | None,
    retained: tuple[float, float] | None,
) -> None:
    """Shows the slit image on the GUI's Guidage panel with its overlay: search
    zone, measured slit line across it and retained point (offset_y included,
    so it sits off the line when an offset is configured; zone only on
    failure). Best effort -- a display problem never affects the measurement."""
    if image_path is None:
        return
    overlay = None
    if zone is not None:
        edges = zone.edges()
        line = point = None
        if measurement is not None:
            xa, xb = edges[0], edges[2]
            line = [xa, round(measurement.y_at(xa), 2), xb, round(measurement.y_at(xb), 2)]
        if retained is not None:
            point = [retained[0], retained[1]]
        overlay = {"zone": edges, "line": line, "point": point}
    try:
        await publish_guide_preview(image_path, source="slit_xy", overlay=overlay, kind="slit", obs=obs)
    except Exception as e:
        log.warning(f"[SLIT_XY] Could not show the slit image on the GUI: {e}")
