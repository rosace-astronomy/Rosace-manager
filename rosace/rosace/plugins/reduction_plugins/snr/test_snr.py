"""
Tests for the SNR sub-plugin (SnrPlugin), on synthetic spectra.
Run: pytest plugins/reduction_plugins/snr/test_snr.py -v
"""

import logging
from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.snr import snr as module
from rosace.plugins.reduction_plugins.snr.snr import SnrPlugin, der_snr

SUB = "Vega_0e37017f"
WIDTH = 5200
LAW = "unit: A\nA0: 3000.0\nA1: 1.0\n"         # pixel x at 3000 + x A (3000 to 8199 A): 1000 points in 6000-7000 A
ZONES = [[3750, 7500], [6000, 7000]]


def _spectrum(seed=0, level=10000.0, sigma=100.0):
    """A sloping continuum with Gaussian noise of known sigma: SNR about level / sigma in the middle."""
    rng = np.random.default_rng(seed)
    x = np.arange(WIDTH)
    return level * (1.0 + 0.2 * (x - WIDTH / 2) / WIDTH) + rng.normal(0.0, sigma, WIDTH)


def _setup(tmp_path, monkeypatch, zones=ZONES, spectrum=None):
    (tmp_path / SUB).mkdir()
    fits.writeto(tmp_path / SUB / "Vega_stack.fits", (_spectrum() if spectrum is None else spectrum).astype(np.float32))
    (tmp_path / "CALIB_x_dispersion_law.yaml").write_text(LAW)
    obs = Observation(id="0e37017f", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path))
    obs.reduction_data["stack_target_spectrum"] = f"{SUB}/Vega_stack.fits"
    obs.reduction_data["dispersion_law_file"] = "CALIB_x_dispersion_law.yaml"
    monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {"snr": {"zones": zones}})
    return obs


class TestDerSnr:

    def test_known_noise(self):
        rng = np.random.default_rng(1)
        flux = 5000.0 + rng.normal(0.0, 50.0, 20000)
        assert der_snr(flux) == pytest.approx(100.0, rel=0.03)

    def test_zero_points_left_out(self):
        rng = np.random.default_rng(2)
        flux = 5000.0 + rng.normal(0.0, 50.0, 20000)
        flux[:5000] = 0.0
        assert der_snr(flux) == pytest.approx(100.0, rel=0.03)

    def test_too_few_points(self):
        assert der_snr(np.full(9, 100.0) + np.arange(9)) is None

    def test_no_noise(self):
        assert der_snr(np.full(100, 100.0)) is None


class TestSnrPlugin:

    async def test_one_value_per_zone(self, tmp_path, monkeypatch, caplog):
        obs = _setup(tmp_path, monkeypatch)

        with caplog.at_level(logging.INFO, logger="spectro.subplugin.snr"):
            result = await SnrPlugin().execute(obs)

        values = result.reduction_data["spectrum_snr"]
        assert [(v["wavelength_min"], v["wavelength_max"]) for v in values] == [(3750.0, 7500.0), (6000.0, 7000.0)]
        for v in values:
            assert v["snr"] == pytest.approx(100.0, rel=0.1) and v["snr"] == round(v["snr"])
        assert result.reduction_data["snr"] == {"method": "DER_SNR"}
        assert "[SNR] SNR " in caplog.text and "(3750-7500 A)" in caplog.text

    async def test_zone_outside_the_spectrum_left_out(self, tmp_path, monkeypatch, caplog):
        obs = _setup(tmp_path, monkeypatch, zones=[[3750, 7500], [9000, 9500]])

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.snr"):
            result = await SnrPlugin().execute(obs)

        assert len(result.reduction_data["spectrum_snr"]) == 1
        assert "Zone 9000-9500 A left out" in caplog.text

    async def test_no_zones_is_only_a_warning(self, tmp_path, monkeypatch, caplog):
        obs = _setup(tmp_path, monkeypatch, zones=[])

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.snr"):
            result = await SnrPlugin().execute(obs)

        assert "spectrum_snr" not in result.reduction_data and "snr" not in result.reduction_data
        assert "subplugin_params.snr.zones is not set" in caplog.text

    async def test_missing_input_is_only_a_warning(self, tmp_path, monkeypatch, caplog):
        obs = _setup(tmp_path, monkeypatch)
        del obs.reduction_data["dispersion_law_file"]

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.snr"):
            result = await SnrPlugin().execute(obs)

        assert "spectrum_snr" not in result.reduction_data
        assert "[SNR] Not measured" in caplog.text and "dispersion_law_file" in caplog.text
