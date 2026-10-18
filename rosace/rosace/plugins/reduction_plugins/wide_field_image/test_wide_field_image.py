"""
Tests for the WIDE_FIELD_IMAGE sub-plugin: Bayer superpixel on synthetic
mosaics, colour/grey images, and the whole step with solve-field replaced by
a fake writing a known WCS (target and bright stars placed, PNG written),
or failing (image without marks); never raises.
Run: pytest plugins/reduction_plugins/wide_field_image/test_wide_field_image.py -v
"""

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from rosace.models import Observation, Target
from rosace.plugins.reduction_plugins.wide_field_image import wide_field_image
from rosace.plugins.reduction_plugins.wide_field_image.wide_field_image import (
    WideFieldImagePlugin, bayer_superpixel, colour_image)
from rosace.plugins.sequencer_plugins.plate_solve_helper import PlateSolveError

VEGA_RA, VEGA_DEC = 279.2347, 38.7837
SCALE_DEG = 20.0 / 3600          # 20"/px on the binned image
RAW_W, RAW_H = 200, 160          # binned: 100 x 80


def _mosaic(pattern: str, values=(10.0, 20.0, 30.0)) -> np.ndarray:
    """A uniform R, G, B scene seen through the Bayer pattern."""
    level = dict(zip("RGB", values))
    raw = np.zeros((8, 10))
    for index, colour in enumerate(pattern):
        dy, dx = divmod(index, 2)
        raw[dy::2, dx::2] = level[colour]
    return raw


@pytest.mark.parametrize("pattern", ["RGGB", "BGGR", "GRBG", "GBRG"])
def test_bayer_superpixel_recovers_the_colours(pattern):
    rgb = bayer_superpixel(_mosaic(pattern), pattern)
    assert rgb.shape == (4, 5, 3)
    assert np.allclose(rgb[..., 0], 10) and np.allclose(rgb[..., 1], 20) and np.allclose(rgb[..., 2], 30)


def test_bayer_offsets_shift_the_pattern():
    raw = np.pad(_mosaic("RGGB"), ((1, 0), (0, 0)))    # one row added in front: the mosaic starts at row 1
    rgb = bayer_superpixel(raw, "RGGB", y_offset=1)
    assert np.allclose(rgb[..., 0], 10) and np.allclose(rgb[..., 2], 30)


def test_unknown_pattern_is_an_error():
    with pytest.raises(ValueError):
        bayer_superpixel(np.zeros((4, 4)), "RGBX")


def test_grey_and_rgb_images():
    grey = colour_image(np.ones((6, 8)), fits.Header())
    assert grey.shape == (3, 4, 3) and np.allclose(grey, 1)
    cube = np.stack([np.full((3, 4), v) for v in (1.0, 2.0, 3.0)])
    assert np.allclose(colour_image(cube, fits.Header())[0, 0], [1, 2, 3])


def _obs(tmp_path: Path) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path))
    obs.scheduler_data["target"] = Target(name="* alf Lyr", ra=VEGA_RA, dec=VEGA_DEC)
    obs.scheduler_data["queried_name"] = "Vega"
    raw = np.random.default_rng(1).normal(100, 5, (RAW_H, RAW_W)).astype(np.float32)
    header = fits.Header({"BAYERPAT": "RGGB", "EXPTIME": 5.0, "DATE-OBS": "2026-10-18T21:00:00"})
    fits.PrimaryHDU(raw, header).writeto(tmp_path / "WIDEFIELD_Vega_5s_20261018_210000.fits")
    obs.sequencer_data["wide_field_images"] = ["WIDEFIELD_Vega_5s_20261018_210000.fits"]
    return obs


def _fake_solve(centre_ra: float, centre_dec: float):
    async def solve(path, **kwargs):
        wcs = WCS(naxis=2)
        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        wcs.wcs.crval = [centre_ra, centre_dec]
        wcs.wcs.crpix = [50.5, 40.5]                       # 1-indexed centre of 100 x 80
        wcs.wcs.cdelt = [-SCALE_DEG, SCALE_DEG]            # East to the left, North up
        with fits.open(path, mode="update") as hdul:
            hdul[0].header.update(wcs.to_header())
    return solve


async def test_solved_image_places_the_target(tmp_path, monkeypatch):
    monkeypatch.setattr(wide_field_image, "solve_wcs", _fake_solve(VEGA_RA, VEGA_DEC))
    obs = await WideFieldImagePlugin().execute(_obs(tmp_path))

    result = obs.reduction_data["wide_field"]
    assert result["solved"] is True
    assert result["scale_arcsec"] == pytest.approx(20.0, abs=0.01)
    assert result["field_deg"] == pytest.approx([0.56, 0.44], abs=0.01)
    assert result["target_offset_arcmin"] == pytest.approx(0.0, abs=0.1)
    assert result["target_pixel"] == pytest.approx([49.5, 39.5], abs=0.1)
    assert result["exposure_s"] == 5.0 and result["capture_time"].startswith("2026-10-18T21:00:00")
    assert (tmp_path / result["annotated_file"]).stat().st_size > 0
    assert result["annotated_file"].endswith("_obs12345_wide_field.png")


def test_bright_star_in_the_field_is_found():
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [VEGA_RA, VEGA_DEC]
    wcs.wcs.crpix = [50.5, 40.5]
    wcs.wcs.cdelt = [-0.1, 0.1]
    from astropy.coordinates import SkyCoord
    stars = wide_field_image._field_stars(wcs, SkyCoord(VEGA_RA, VEGA_DEC, unit="deg"), 7, 100, 80)
    assert [name for name, _, _ in stars] == ["Vega"]
    north = wide_field_image._direction(wcs, SkyCoord(VEGA_RA, VEGA_DEC, unit="deg"), 0, 1, 1.0)
    east = wide_field_image._direction(wcs, SkyCoord(VEGA_RA, VEGA_DEC, unit="deg"), 1, 0, 1.0)
    assert north == pytest.approx((0, 1), abs=0.02) and east == pytest.approx((-1, 0), abs=0.05)


async def test_target_outside_the_field_has_no_cross(tmp_path, monkeypatch):
    monkeypatch.setattr(wide_field_image, "solve_wcs", _fake_solve(VEGA_RA + 3, VEGA_DEC))
    obs = await WideFieldImagePlugin().execute(_obs(tmp_path))
    result = obs.reduction_data["wide_field"]
    assert result["solved"] is True and result["target_pixel"] is None
    assert result["target_offset_arcmin"] > 100 and type(result["target_offset_arcmin"]) is float


async def test_unsolved_image_is_shown_without_marks(tmp_path, monkeypatch, caplog):
    async def fail(path, **kwargs):
        raise PlateSolveError("no WCS solution")
    monkeypatch.setattr(wide_field_image, "solve_wcs", fail)
    obs = await WideFieldImagePlugin().execute(_obs(tmp_path))
    result = obs.reduction_data["wide_field"]
    assert result["solved"] is False and result["scale_arcsec"] is None
    assert (tmp_path / result["annotated_file"]).exists()
    assert any(r.levelname == "WARNING" and "not plate-solved" in r.message for r in caplog.records)


async def test_sampling_is_passed_to_the_solve(tmp_path, monkeypatch):
    seen = {}
    async def solve(path, **kwargs):
        seen.update(kwargs)
        raise PlateSolveError("stop")
    monkeypatch.setattr(wide_field_image, "solve_wcs", solve)
    monkeypatch.setattr(wide_field_image, "SUBPLUGIN_PARAMS", {"wide_field": {"sampling_arcsec": 31.0}})
    await WideFieldImagePlugin().execute(_obs(tmp_path))
    assert seen["sampling_arcsec"] == 31.0 and seen["ra_hint"] == VEGA_RA


async def test_nothing_without_an_image(tmp_path):
    obs = Observation(id="obs1", created_at=datetime.now(timezone.utc), session_id="s", obs_dir=str(tmp_path))
    obs = await WideFieldImagePlugin().execute(obs)
    assert "wide_field" not in obs.reduction_data


async def test_unreadable_image_never_raises(tmp_path, caplog):
    obs = _obs(tmp_path)
    (tmp_path / obs.sequencer_data["wide_field_images"][0]).write_bytes(b"not a FITS file")
    obs = await WideFieldImagePlugin().execute(obs)
    assert "wide_field" not in obs.reduction_data
    assert any(r.levelname == "WARNING" for r in caplog.records)
