"""
Tests for fisheye.py on synthetic all-sky images: the bright stars drawn
where a known lens model puts them, on a noisy sky disk.
Run: pytest plugins/reduction_plugins/fripon_image/test_fisheye.py -v
"""

from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import pytest

from rosace.plugins.reduction_plugins.fripon_image.fisheye import (
    LensModel, bright_stars, fit, sky_positions,
)

WHEN = datetime(2026, 10, 18, 1, 9, 19, tzinfo=timezone.utc)
LAT, LON = 45.62, -1.03
TRUE = LensModel(cx=634.0, cy=472.5, k=5.92, k3=-8.9e-05, rotation_deg=181.2, mirror=-1)
HEIGHT, WIDTH = 966, 1296


def synthetic_sky(model: LensModel = TRUE, when: datetime = WHEN, stars: bool = True, seed: int = 0) -> np.ndarray:
    """An 8-bit RGB all-sky image: a grey disk of the lens's horizon radius,
    and, unless stars is False (clouds), every bright star as a small
    Gaussian where the model puts it, brighter for a brighter star."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    radius = model.k * 90 + model.k3 * 90 ** 3
    image = np.where(np.hypot(xx - model.cx, yy - model.cy) < radius, 90.0, 5.0)
    if stars:
        sky = sky_positions(when, LAT, LON)
        x, y = model.project(sky.alt, sky.az)
        for sx, sy, v in zip(x, y, sky.v):
            amplitude = np.clip(120 * 10 ** (-0.4 * (v - 1.0)), 25, 160)
            x0, y0 = int(sx) - 6, int(sy) - 6          # a small cutout around the star is enough
            if 0 <= x0 and 0 <= y0 and x0 + 13 <= WIDTH and y0 + 13 <= HEIGHT:
                cy, cx = np.mgrid[y0:y0 + 13, x0:x0 + 13]
                image[y0:y0 + 13, x0:x0 + 13] += amplitude * np.exp(-((cx - sx) ** 2 + (cy - sy) ** 2) / (2 * 1.3 ** 2))
    image += rng.normal(0, 6, image.shape)
    gray = np.clip(image, 0, 255).astype(np.uint8)
    return np.repeat(gray[..., None], 3, axis=2)


def test_bright_stars_cover_the_whole_sky():
    stars = bright_stars()
    assert len(stars) > 250
    assert min(s.dec_deg for s in stars) < -60 and max(s.dec_deg for s in stars) > 80   # both hemispheres
    names = {s.usual_name for s in stars}
    assert {"Polaris", "Sirius", "Vega", "Canopus"} <= names
    assert all(s.v < 3.5 for s in stars)


def test_model_projects_the_zenith_on_the_centre_and_the_mirror():
    (x,), (y,) = TRUE.project([90.0], [0.0])
    assert (x, y) == pytest.approx((TRUE.cx, TRUE.cy))
    (xe,), _ = TRUE.project([0.0], [90.0])
    (xm,), _ = replace(TRUE, mirror=1).project([0.0], [90.0])
    assert xe - TRUE.cx == pytest.approx(-(xm - TRUE.cx))


def test_config_round_trip():
    assert LensModel.from_config(TRUE.to_config()) == TRUE
    assert all(type(v) in (float, int) for v in TRUE.to_config().values())


def test_full_search_finds_the_model():
    result = fit(synthetic_sky(), WHEN, LAT, LON)
    assert result is not None
    assert result.model.mirror == TRUE.mirror
    assert result.model.rotation_deg == pytest.approx(TRUE.rotation_deg, abs=0.2)
    x, y = result.model.project(result.positions.alt, result.positions.az)
    tx, ty = TRUE.project(result.positions.alt, result.positions.az)
    assert np.max(np.hypot(x - tx, y - ty)) < 0.5
    assert result.matched_count > 80


def test_refinement_from_a_stored_model():
    stored = replace(TRUE, cx=TRUE.cx + 3, rotation_deg=TRUE.rotation_deg - 0.6)     # the camera moved a little
    result = fit(synthetic_sky(), WHEN, LAT, LON, start=stored)
    assert result is not None
    assert result.model.cx == pytest.approx(TRUE.cx, abs=0.3)
    assert result.model.rotation_deg == pytest.approx(TRUE.rotation_deg, abs=0.1)


def test_clouds_give_no_model():
    assert fit(synthetic_sky(stars=False), WHEN, LAT, LON, start=TRUE) is None
