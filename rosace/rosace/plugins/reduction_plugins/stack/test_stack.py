"""
Tests for the STACK sub-plugin (StackPlugin), on synthetic 1D spectra.
Run: pytest plugins/reduction_plugins/stack/test_stack.py -v
"""

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.stack.stack import (
    StackPlugin,
    exposure_decomposition,
    stack_filename,
)

LENGTH = 100
SUB = "Vega_0e37017f"
READOUT = 3.0                       # seconds between the end of a frame and the start of the next


def _spectrum(level):
    return np.full(LENGTH, level, dtype=np.float64)


def _write_series(tmp_path, prefix, spectra, exposures, start="2026-09-25T21:00:00.000"):
    names = []
    t = datetime.fromisoformat(start)
    for i, (data, exposure) in enumerate(zip(spectra, exposures), start=1):
        name = f"{SUB}/{prefix}_20260925_210000-{i}_preproc_1d.fits"
        hdu = fits.PrimaryHDU(data.astype(np.float32))
        hdu.header["EXPTIME"] = exposure
        hdu.header["DATE-OBS"] = t.isoformat(timespec="milliseconds")
        hdu.writeto(tmp_path / name)
        names.append(name)
        t += timedelta(seconds=exposure + READOUT)
    return names


def _setup(tmp_path, target, lamp=None, target_exposures=None, lamp_exposures=None):
    (tmp_path / SUB).mkdir()
    obs = Observation(id="0e37017f", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path))
    obs.scheduler_data["queried_name"] = "Vega"
    lamp = lamp if lamp is not None else [_spectrum(50.0)] * 3
    obs.reduction_data["extract_target_spectra"] = _write_series(
        tmp_path, "Vega_3x20s", target, target_exposures or [20.0] * len(target))
    obs.reduction_data["extract_calib_spectra"] = _write_series(
        tmp_path, "CALIB_3x3s", lamp, lamp_exposures or [3.0] * len(lamp))
    return obs


def _read(tmp_path, relative):
    with fits.open(tmp_path / relative) as hdul:
        return hdul[0].data.astype(np.float64), hdul[0].header


class TestStackPlugin:

    async def test_sum_and_header(self, tmp_path):
        obs = _setup(tmp_path, [_spectrum(10.0), _spectrum(12.0), _spectrum(14.0)])

        result = await StackPlugin().execute(obs)

        target = result.reduction_data["stack_target_spectrum"]
        assert target == f"{SUB}/Vega_3x20s_20260925_210000_preproc_1d_stack.fits"
        data, header = _read(tmp_path, target)
        assert data.shape == (LENGTH,)
        assert np.allclose(data, 36.0)
        assert header["EXPTIME2"] == "3x20s"
        assert header["EXPTIME"] == pytest.approx(3 * 20.0 + 2 * READOUT)
        assert header["DATE-OBS"] == "2026-09-25T21:00:00.000"
        assert header["DATE-END"] == "2026-09-25T21:01:06.000"
        comments = [str(c) for c in header["COMMENT"]]
        assert "Sum of 3 1D spectra" in comments
        assert sum(c.startswith("Source: ") for c in comments) == 3
        assert "BZERO" not in header

        lamp, lamp_header = _read(tmp_path, result.reduction_data["stack_calib_spectrum"])
        assert np.allclose(lamp, 150.0)
        assert lamp_header["EXPTIME2"] == "3x3s"
        assert "stack" not in result.reduction_data

    async def test_low_flux_spectrum_rejected(self, tmp_path, caplog):
        obs = _setup(tmp_path, [_spectrum(10.0), _spectrum(1.0), _spectrum(12.0)])

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.stack"):
            result = await StackPlugin().execute(obs)

        data, header = _read(tmp_path, result.reduction_data["stack_target_spectrum"])
        assert np.allclose(data, 22.0)
        assert header["EXPTIME2"] == "2x20s"
        # A middle frame rejected: the observation still spans all three
        assert header["EXPTIME"] == pytest.approx(3 * 20.0 + 2 * READOUT)
        rejected = result.reduction_data["stack"]["rejected"]
        assert len(rejected) == 1
        assert rejected[0]["file"].endswith("-2_preproc_1d.fits")
        assert rejected[0]["flux_fraction"] == pytest.approx(1 / 12, abs=1e-3)
        assert "-2_preproc_1d.fits" in caplog.text
        assert any(str(c).startswith("Rejected (flux 8% of the brightest)") for c in header["COMMENT"])

    async def test_mostly_cloudy_series(self, tmp_path):
        # Three of five frames under cloud: the median is a cloudy frame, so
        # only a comparison with the brightest frame rejects them
        obs = _setup(tmp_path, [_spectrum(12.0), _spectrum(1.5), _spectrum(1.2), _spectrum(1.0), _spectrum(10.0)])

        result = await StackPlugin().execute(obs)

        data, header = _read(tmp_path, result.reduction_data["stack_target_spectrum"])
        assert np.allclose(data, 22.0)
        assert header["EXPTIME2"] == "2x20s"
        rejected = result.reduction_data["stack"]["rejected"]
        assert [r["file"][-18:] for r in rejected] == ["-2_preproc_1d.fits", "-3_preproc_1d.fits", "-4_preproc_1d.fits"]

    async def test_last_frame_rejected_shortens_exptime(self, tmp_path):
        obs = _setup(tmp_path, [_spectrum(10.0), _spectrum(12.0), _spectrum(0.5)])

        result = await StackPlugin().execute(obs)

        _, header = _read(tmp_path, result.reduction_data["stack_target_spectrum"])
        assert header["EXPTIME"] == pytest.approx(2 * 20.0 + READOUT)
        assert header["DATE-END"] == "2026-09-25T21:00:43.000"

    async def test_different_exposures(self, tmp_path):
        obs = _setup(tmp_path, [_spectrum(10.0)] * 3, target_exposures=[20.0, 20.0, 30.0])

        result = await StackPlugin().execute(obs)

        _, header = _read(tmp_path, result.reduction_data["stack_target_spectrum"])
        assert header["EXPTIME2"] == "2x20s+1x30s"

    async def test_missing_key_is_an_error(self, tmp_path):
        obs = _setup(tmp_path, [_spectrum(10.0)] * 3)
        del obs.reduction_data["extract_calib_spectra"]

        with pytest.raises(RuntimeError, match="extract_calib_spectra"):
            await StackPlugin().execute(obs)

    async def test_no_light_is_an_error(self, tmp_path):
        obs = _setup(tmp_path, [_spectrum(0.0)] * 3)

        with pytest.raises(RuntimeError, match="no light"):
            await StackPlugin().execute(obs)

    async def test_different_lengths_is_an_error(self, tmp_path):
        obs = _setup(tmp_path, [_spectrum(10.0), np.full(LENGTH + 1, 10.0), _spectrum(10.0)])

        with pytest.raises(RuntimeError, match="different lengths"):
            await StackPlugin().execute(obs)

    async def test_missing_date_obs_is_an_error(self, tmp_path):
        obs = _setup(tmp_path, [_spectrum(10.0)] * 3)
        path = tmp_path / obs.reduction_data["extract_target_spectra"][0]
        with fits.open(path, mode="update") as hdul:
            del hdul[0].header["DATE-OBS"]

        with pytest.raises(RuntimeError, match="DATE-OBS"):
            await StackPlugin().execute(obs)


class TestHelpers:

    def test_stack_filename(self):
        assert stack_filename("Vega_1/16Peg_5x108s_20260925_213944-1_preproc_tilt_smile_1d.fits") == (
            "16Peg_5x108s_20260925_213944_preproc_tilt_smile_1d_stack.fits")
        assert stack_filename("CALIB_3x3s_20260925_194111-12.fits") == "CALIB_3x3s_20260925_194111_stack.fits"

    def test_exposure_decomposition(self):
        assert exposure_decomposition([107.7457] * 5) == "5x107.7s"
        assert exposure_decomposition([20.0, 30.0, 20.0]) == "2x20s+1x30s"
