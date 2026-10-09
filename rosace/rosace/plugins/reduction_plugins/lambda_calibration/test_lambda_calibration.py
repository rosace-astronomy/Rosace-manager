"""
Tests for the LAMBDA_CALIBRATION sub-plugin (LambdaCalibrationPlugin), on synthetic stacked spectra.
Run: pytest plugins/reduction_plugins/lambda_calibration/test_lambda_calibration.py -v
"""

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits
from numpy.polynomial import Polynomial

from rosace.models import Observation
from rosace.plugins.reduction_plugins.lambda_calibration import lambda_calibration as module
from rosace.plugins.reduction_plugins.lambda_calibration.lambda_calibration import LambdaCalibrationPlugin

WIDTH = 1304
LAW = Polynomial([3054.8, 3.4976, 1.94e-4, -1.64e-7])
LINE = 6562.79
SUB = "Vega_0e37017f"
LAW_FILE = "CALIB_3x3s_20260925_194111_dispersion_law.yaml"


def _pixel_of(wavelength, law=LAW):
    return float(next(r.real for r in (law - wavelength).roots() if abs(r.imag) < 1e-9 and 0 <= r.real < WIDTH))


def _spectrum(law=LAW, line=LINE, continuum=1000.0):
    x = np.arange(WIDTH)
    return continuum + 5000.0 * np.exp(-0.5 * ((x - _pixel_of(line, law)) / 2.0) ** 2)


def _setup(tmp_path, monkeypatch, params=None, law=LAW, unit="A", line=LINE, continuum=1000.0):
    (tmp_path / SUB).mkdir()
    coefficients = "\n".join(f"A{k}: {float(c)!r}" for k, c in enumerate(law.coef))
    (tmp_path / LAW_FILE).write_text(
        f"# Dispersion law of lamp series CALIB_3x3s_20260925_194111, computed by Rosace.\n"
        f"# 8 reference lines, RMS 0.203 {unit}.\nunit: {unit}\n{coefficients}\n")
    obs = Observation(id="0e37017f", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path))
    obs.scheduler_data["queried_name"] = "Vega"
    obs.reduction_data["dispersion_law_file"] = LAW_FILE
    for key, prefix in (("stack_target_spectrum", "Vega"), ("stack_calib_spectrum", "CALIB")):
        name = f"{SUB}/{prefix}_stack.fits"
        hdu = fits.PrimaryHDU(_spectrum(law, line, continuum).astype(np.float32))
        hdu.header["EXPTIME"] = 100.0
        hdu.writeto(tmp_path / name)
        obs.reduction_data[key] = name
    monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {"lambda_calibration": params} if params else {})
    return obs


def _wavelengths(header):
    return header["CRVAL1"] + (np.arange(header["NAXIS1"]) + 1 - header["CRPIX1"]) * header["CDELT1"]


class TestLambdaCalibrationPlugin:

    async def test_one_point_per_pixel_at_the_mean_dispersion(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch)

        result = await LambdaCalibrationPlugin().execute(obs)

        data, header = fits.getdata(tmp_path / result.reduction_data["lambda_target_spectrum"], header=True)
        assert result.reduction_data["lambda_target_spectrum"] == f"{SUB}/Vega_stack_lambda.fits"
        assert data.size == WIDTH
        assert header["CRVAL1"] == pytest.approx(LAW(0))
        assert header["CDELT1"] == pytest.approx((LAW(WIDTH - 1) - LAW(0)) / (WIDTH - 1))
        assert header["CRPIX1"] == 1 and header["CTYPE1"] == "Wavelength" and header["CUNIT1"] == "Angstrom"
        comments = [str(c) for c in header["COMMENT"]]
        assert f"Dispersion law: {LAW_FILE}" in comments
        assert "8 reference lines, RMS 0.203 A." in comments
        assert header["EXPTIME"] == 100.0
        # Total flux kept (to the two half pixels at the ends)
        assert data.sum() == pytest.approx(_spectrum().sum(), rel=1e-3)
        casier = result.reduction_data["lambda_calibration"]
        assert casier["points"] == WIDTH and casier["unit"] == "A"
        assert result.reduction_data["lambda_calib_spectrum"] == f"{SUB}/CALIB_stack_lambda.fits"

    async def test_line_found_at_its_wavelength(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, continuum=0.0)

        result = await LambdaCalibrationPlugin().execute(obs)

        data, header = fits.getdata(tmp_path / result.reduction_data["lambda_target_spectrum"], header=True)
        wavelengths = _wavelengths(header)
        near = np.abs(wavelengths - LINE) < 40
        centre = np.sum(wavelengths[near] * data[near]) / np.sum(data[near])
        assert centre == pytest.approx(LINE, abs=0.01 * header["CDELT1"])

    async def test_imposed_step(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, params={"step": 0.87})

        result = await LambdaCalibrationPlugin().execute(obs)

        data, header = fits.getdata(tmp_path / result.reduction_data["lambda_target_spectrum"], header=True)
        assert header["CDELT1"] == 0.87
        assert data.size == int((LAW(WIDTH - 1) - LAW(0)) // 0.87) + 1
        assert data.sum() == pytest.approx(_spectrum().sum(), rel=2e-3)

    async def test_nanometres(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, law=Polynomial(LAW.coef * 0.1), unit="nm", line=LINE / 10)

        result = await LambdaCalibrationPlugin().execute(obs)

        _, header = fits.getdata(tmp_path / result.reduction_data["lambda_target_spectrum"], header=True)
        assert header["CUNIT1"] == "nm"
        assert header["CRVAL1"] == pytest.approx(305.48)

    async def test_missing_law_is_an_error(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch)
        del obs.reduction_data["dispersion_law_file"]

        with pytest.raises(RuntimeError, match="dispersion_law_file"):
            await LambdaCalibrationPlugin().execute(obs)

    async def test_missing_spectrum_is_an_error(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch)
        del obs.reduction_data["stack_calib_spectrum"]

        with pytest.raises(RuntimeError, match="stack_calib_spectrum"):
            await LambdaCalibrationPlugin().execute(obs)
