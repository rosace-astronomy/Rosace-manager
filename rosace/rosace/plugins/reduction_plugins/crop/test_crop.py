"""
Tests for the CROP sub-plugin (CropPlugin), on synthetic spectra.
Run: pytest plugins/reduction_plugins/crop/test_crop.py -v
"""

import logging
from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.crop import crop as module
from rosace.plugins.reduction_plugins.crop.crop import CropPlugin
from rosace.plugins.reduction_plugins.reduction_helpers import wavelength_axis

SUB = "Vega_0e37017f"
DATA = np.arange(1000, dtype=np.float64)                 # point i at 3000 + 5 i A (3000 to 7995 A)


def _setup(tmp_path, monkeypatch, params):
    (tmp_path / SUB).mkdir()
    header = fits.Header()
    header["CRVAL1"], header["CDELT1"], header["CRPIX1"] = 3000.0, 5.0, 1
    fits.writeto(tmp_path / SUB / "Vega_scaled.fits", DATA.astype(np.float32), header)
    obs = Observation(id="0e37017f", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path))
    obs.scheduler_data["queried_name"] = "Vega"
    obs.reduction_data["scaling_target_spectrum"] = f"{SUB}/Vega_scaled.fits"
    monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {"crop": params})
    return obs


class TestCropPlugin:

    async def test_keeps_the_points_between_the_bounds(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, {"wavelength_min": 3752, "wavelength_max": 7500})

        result = await CropPlugin().execute(obs)

        data, header = fits.getdata(tmp_path / result.reduction_data["crop_target_spectrum"], header=True)
        assert result.reduction_data["crop_target_spectrum"] == f"{SUB}/Vega_scaled_crop.fits"
        assert header["CRVAL1"] == 3755.0 and header["CDELT1"] == 5.0
        assert header["NAXIS1"] == 750                    # 3755 to 7500 A
        assert data == pytest.approx(DATA[151:901])
        assert wavelength_axis(header)[-1] == 7500.0

    async def test_bound_beyond_the_spectrum_keeps_what_exists(self, tmp_path, monkeypatch, caplog):
        obs = _setup(tmp_path, monkeypatch, {"wavelength_min": 2500, "wavelength_max": 7000})

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.crop"):
            result = await CropPlugin().execute(obs)

        data, header = fits.getdata(tmp_path / result.reduction_data["crop_target_spectrum"], header=True)
        assert header["CRVAL1"] == 3000.0 and data.size == 801
        assert "go beyond the spectrum" in caplog.text

    async def test_no_point_kept_is_an_error(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, {"wavelength_min": 9000, "wavelength_max": 9500})

        with pytest.raises(RuntimeError, match="no point"):
            await CropPlugin().execute(obs)

    async def test_missing_settings_are_an_error(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, {})

        with pytest.raises(RuntimeError, match="crop.wavelength_min"):
            await CropPlugin().execute(obs)
