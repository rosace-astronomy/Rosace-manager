"""
Tests for the EXTRACT sub-plugin (ExtractPlugin), on synthetic frames.
Run: pytest plugins/reduction_plugins/extract/test_extract.py -v
"""

import math
from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.extract.extract import ZONES_PICTURE, ExtractPlugin

HEIGHT, WIDTH = 200, 500          # 25 column groups of 20 columns
TRACE_ROW = 100
BAND = (30, 170)                  # rows lit through the slit
WIDE_COLUMNS = 100                # the trace is wider (sigma 4) in the first 100 columns, sigma 2 elsewhere
AMPLITUDE = 1000.0
SUB = "Vega_0e37017f"


def _flat(band=BAND):
    flat = np.full((HEIGHT, WIDTH), 0.1)
    flat[band[0]:band[1] + 1] = 1.0
    flat[:, 0] = 0.01                 # dead column
    return flat


def _sky(x):
    return 50.0 + 0.1 * x             # gradient along the dispersion axis


def _target_frame(seed=0, with_trace=True, band=BAND):
    rng = np.random.default_rng(seed)
    x = np.arange(WIDTH)
    y = np.arange(HEIGHT)[:, None]
    sigma = np.where(x < WIDE_COLUMNS, 4.0, 2.0)
    frame = _sky(x) + rng.normal(0, 2, (HEIGHT, WIDTH))
    if with_trace:
        frame = frame + AMPLITUDE * np.exp(-0.5 * ((y - TRACE_ROW) / sigma) ** 2)
    frame[:band[0]] = 0.0             # preprocessing zeroes everything outside the lit band
    frame[band[1] + 1:] = 0.0
    return frame


def _lamp_frame(band=BAND):
    frame = np.zeros((HEIGHT, WIDTH))
    frame[band[0]:band[1] + 1] = 100.0
    frame[band[0]:band[1] + 1, 250] += 5000.0   # one emission line
    return frame


def _setup(tmp_path, target_frames, lamp_frames, flat=None):
    (tmp_path / SUB).mkdir()
    fits.writeto(tmp_path / "FLAT_master.fits", (_flat() if flat is None else flat).astype(np.float32))
    obs = Observation(id="0e37017f", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path))
    obs.scheduler_data["queried_name"] = "Vega"
    obs.reduction_data["master_flat_image"] = "FLAT_master.fits"
    for key, prefix, frames in (("smile_target_images", "STAR", target_frames), ("smile_calib_images", "CALIB", lamp_frames)):
        names = []
        for i, data in enumerate(frames, start=1):
            name = f"{SUB}/{prefix}-{i}_smile.fits"
            hdu = fits.PrimaryHDU(data.astype(np.float32))
            hdu.header["EXPTIME"] = 10.0
            hdu.writeto(tmp_path / name)
            names.append(name)
        obs.reduction_data[key] = names
    return obs


class TestExtractPlugin:

    async def test_window_from_the_widest_column_group(self, tmp_path):
        obs = _setup(tmp_path, [_target_frame(s) for s in (1, 2)], [_lamp_frame()])

        result = await ExtractPlugin().execute(obs)

        casier = result.reduction_data["extract"]
        assert casier["trace_y"] == TRACE_ROW
        assert casier["trace_sigma_px"] == pytest.approx(4.0, abs=0.2)
        sigma = casier["trace_sigma_px"]   # rounded to 0.01 px in the casier
        half = TRACE_ROW - casier["window"][0]
        assert casier["window"] == [TRACE_ROW - half, TRACE_ROW + half]
        assert 3 * sigma - 0.02 <= half < 3 * sigma + 1.02            # ceil(3 sigma)
        margin = 0.05 * (BAND[1] - BAND[0])
        (low_start, low_end), (high_start, high_end) = casier["sky_zones"]
        assert (low_start, high_end) == (math.ceil(BAND[0] + margin), math.floor(BAND[1] - margin))
        gap = TRACE_ROW - low_end
        assert high_start == TRACE_ROW + gap
        assert 6 * sigma - 0.04 <= gap < 6 * sigma + 1.04              # ceil(6 sigma)

    async def test_target_spectrum_has_the_sky_removed_column_by_column(self, tmp_path):
        frame = _target_frame(1)
        frame[60, 300] = 30000.0      # hot pixel in the lower sky zone
        obs = _setup(tmp_path, [frame], [_lamp_frame()])

        result = await ExtractPlugin().execute(obs)

        rel = result.reduction_data["extract_target_spectra"][0]
        assert rel == f"{SUB}/STAR-1_smile_1d.fits"
        with fits.open(tmp_path / rel) as hdul:
            spectrum = hdul[0].data.astype(float)
            assert hdul[0].header["NAXIS"] == 1
            assert hdul[0].header["NAXIS1"] == WIDTH
            assert hdul[0].header["EXPTIME"] == 10.0
        expected = AMPLITUDE * 2.0 * math.sqrt(2 * math.pi)     # whole Gaussian, sigma 2
        for x in (200, 300, 450):     # sky 70 to 95 ADU per pixel, removed
            assert spectrum[x] == pytest.approx(expected, rel=0.02)
        assert spectrum[0] == 0.0     # dead column

    async def test_lamp_uses_the_same_window_without_sky_subtraction(self, tmp_path):
        obs = _setup(tmp_path, [_target_frame(1)], [_lamp_frame()])

        result = await ExtractPlugin().execute(obs)

        window = result.reduction_data["extract"]["window"]
        rows = window[1] - window[0] + 1
        spectrum = fits.getdata(tmp_path / result.reduction_data["extract_calib_spectra"][0]).astype(float)
        assert spectrum[400] == pytest.approx(100.0 * rows)
        assert spectrum[250] == pytest.approx(5100.0 * rows)
        assert spectrum[0] == 0.0

    async def test_zones_picture_is_written(self, tmp_path):
        obs = _setup(tmp_path, [_target_frame(1)], [_lamp_frame()])
        await ExtractPlugin().execute(obs)
        assert (tmp_path / SUB / ZONES_PICTURE).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_no_trace_is_an_error(self, tmp_path):
        obs = _setup(tmp_path, [_target_frame(1, with_trace=False)], [_lamp_frame()])
        with pytest.raises(RuntimeError, match="no spectrum trace"):
            await ExtractPlugin().execute(obs)

    async def test_no_room_for_a_sky_zone_is_an_error(self, tmp_path):
        band = (80, 120)
        obs = _setup(tmp_path, [_target_frame(1, band=band)], [_lamp_frame(band)], flat=_flat(band))
        with pytest.raises(RuntimeError, match="no room for a sky zone"):
            await ExtractPlugin().execute(obs)

    async def test_missing_lamp_spectra_input_is_an_error(self, tmp_path):
        obs = _setup(tmp_path, [_target_frame(1)], [_lamp_frame()])
        del obs.reduction_data["smile_calib_images"]
        with pytest.raises(RuntimeError, match="smile_calib_images"):
            await ExtractPlugin().execute(obs)
