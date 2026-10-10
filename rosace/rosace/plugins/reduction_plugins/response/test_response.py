"""
Tests for the RESPONSE sub-plugin (ResponsePlugin), on synthetic spectra.
Run: pytest plugins/reduction_plugins/response/test_response.py -v
"""

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.response import response as module
from rosace.plugins.reduction_plugins.response.response import ResponsePlugin

SUB = "Vega_0e37017f"


def _spectrum_header(start=4000.0, step=2.0, count=500):
    header = fits.Header()
    header["NAXIS1"] = count
    header["CRVAL1"], header["CDELT1"], header["CRPIX1"] = start, step, 1
    header["EXPTIME"] = 100.0
    return header


def _setup(tmp_path, monkeypatch, response_values, response_start=3900.0, response_step=1.0, params=None):
    (tmp_path / SUB).mkdir()
    header = _spectrum_header()
    fits.writeto(tmp_path / SUB / "Vega_lambda.fits", np.full(500, 6.0, dtype=np.float32), header)
    response_header = fits.Header()
    response_header["CRVAL1"], response_header["CDELT1"] = response_start, response_step   # no CRPIX1, as Demetra writes
    fits.writeto(tmp_path / "RI.fit", np.asarray(response_values, dtype=np.float32), response_header)
    obs = Observation(id="0e37017f", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path))
    obs.scheduler_data["queried_name"] = "Vega"
    obs.reduction_data["lambda_target_spectrum"] = f"{SUB}/Vega_lambda.fits"
    monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {"response": params or {"file": str(tmp_path / "RI.fit")}})
    return obs


class TestResponsePlugin:

    async def test_divides_by_the_response_on_its_own_grid(self, tmp_path, monkeypatch):
        # Response from 3900 to 5399 A, 1 A step, linear from 2 to 3; the spectrum (4000-4998 A) is 6 everywhere
        values = np.linspace(2.0, 3.0, 1500)
        obs = _setup(tmp_path, monkeypatch, values)

        result = await ResponsePlugin().execute(obs)

        data, header = fits.getdata(tmp_path / result.reduction_data["response_target_spectrum"], header=True)
        wavelengths = 4000.0 + 2.0 * np.arange(500)
        expected = 6.0 / np.interp(wavelengths, 3900.0 + np.arange(1500), values)
        assert data == pytest.approx(expected, rel=1e-5)
        assert result.reduction_data["response_target_spectrum"] == f"{SUB}/Vega_lambda_response.fits"
        assert header["CRVAL1"] == 4000.0 and header["CDELT1"] == 2.0 and header["EXPTIME"] == 100.0
        assert "COMMENT" not in header

    async def test_zero_outside_the_response_and_where_it_is_tiny(self, tmp_path, monkeypatch):
        # Response from 3900 to 4699 A only; nearly zero between 4100 and 4199 A
        values = np.full(800, 2.0)
        values[200:300] = 0.001
        obs = _setup(tmp_path, monkeypatch, values)

        result = await ResponsePlugin().execute(obs)

        data = fits.getdata(tmp_path / result.reduction_data["response_target_spectrum"])
        wavelengths = 4000.0 + 2.0 * np.arange(500)
        assert np.all(data[wavelengths > 4699] == 0)
        assert np.all(data[(wavelengths > 4101) & (wavelengths < 4198)] == 0)
        assert np.all(data[wavelengths < 4098] == pytest.approx(3.0))

    async def test_file_found_in_the_data_directory(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, np.full(1500, 2.0), params={"file": "RI.fit"})
        (tmp_path / "response").mkdir()
        (tmp_path / "RI.fit").rename(tmp_path / "response" / "RI.fit")
        monkeypatch.setattr(module, "BASE_DATA_DIR", tmp_path)

        result = await ResponsePlugin().execute(obs)

        assert np.all(fits.getdata(tmp_path / result.reduction_data["response_target_spectrum"]) == pytest.approx(3.0))

    async def test_missing_setting_is_an_error(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, np.full(1500, 2.0))
        monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {})

        with pytest.raises(RuntimeError, match="response.file"):
            await ResponsePlugin().execute(obs)

    async def test_missing_file_is_an_error(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, np.full(1500, 2.0), params={"file": str(tmp_path / "absent.fit")})

        with pytest.raises(RuntimeError, match="not found"):
            await ResponsePlugin().execute(obs)
