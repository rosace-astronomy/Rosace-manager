"""
Tests for the SCALING sub-plugin (ScalingPlugin), on synthetic spectra.
Run: pytest plugins/reduction_plugins/scaling/test_scaling.py -v
"""

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.scaling import scaling as module
from rosace.plugins.reduction_plugins.scaling.scaling import ScalingPlugin

SUB = "Vega_0e37017f"
WAVELENGTHS = 4000.0 + 5.0 * np.arange(700)          # 4000 to 7495 A


def _setup(tmp_path, monkeypatch, data, params):
    (tmp_path / SUB).mkdir()
    header = fits.Header()
    header["CRVAL1"], header["CDELT1"], header["CRPIX1"] = 4000.0, 5.0, 1
    fits.writeto(tmp_path / SUB / "Vega_response.fits", np.asarray(data, dtype=np.float32), header)
    obs = Observation(id="0e37017f", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path))
    obs.scheduler_data["queried_name"] = "Vega"
    obs.reduction_data["response_target_spectrum"] = f"{SUB}/Vega_response.fits"
    monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {"scaling": params} if params is not None else {})
    return obs


class TestScalingPlugin:

    async def test_zone_mean_becomes_one(self, tmp_path, monkeypatch):
        data = 200.0 + 0.1 * np.arange(700)
        obs = _setup(tmp_path, monkeypatch, data, {"wavelength_min": 6600, "wavelength_max": 6700})

        result = await ScalingPlugin().execute(obs)

        scaled, header = fits.getdata(tmp_path / result.reduction_data["scaling_target_spectrum"], header=True)
        zone = (WAVELENGTHS >= 6600) & (WAVELENGTHS <= 6700)
        assert scaled[zone].mean() == pytest.approx(1.0)
        assert scaled == pytest.approx(data / data[zone].mean(), rel=1e-5)
        assert result.reduction_data["scaling_target_spectrum"] == f"{SUB}/Vega_response_scaled.fits"
        assert header["CRVAL1"] == 4000.0 and "COMMENT" not in header

    @pytest.mark.parametrize("zone", [(7600, 7700), (7400, 7600), (3900, 4100)])
    async def test_zone_outside_the_spectrum_is_an_error(self, tmp_path, monkeypatch, zone):
        obs = _setup(tmp_path, monkeypatch, np.ones(700), {"wavelength_min": zone[0], "wavelength_max": zone[1]})

        with pytest.raises(RuntimeError, match="not inside the spectrum"):
            await ScalingPlugin().execute(obs)

    async def test_level_not_positive_is_an_error(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, np.zeros(700), {"wavelength_min": 6600, "wavelength_max": 6700})

        with pytest.raises(RuntimeError, match="cannot scale"):
            await ScalingPlugin().execute(obs)

    async def test_missing_settings_are_an_error(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, np.ones(700), {"wavelength_min": 6600})

        with pytest.raises(RuntimeError, match="scaling.wavelength_min"):
            await ScalingPlugin().execute(obs)
