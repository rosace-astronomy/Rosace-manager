"""
Tests for the PLOT_SPECTRUM sub-plugin (PlotSpectrumPlugin), on a synthetic final spectrum.
Run: pytest plugins/reduction_plugins/plot_spectrum/test_plot_spectrum.py -v
"""

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.plot_spectrum import plot_spectrum as module
from rosace.plugins.reduction_plugins.plot_spectrum.plot_spectrum import PlotSpectrumPlugin

FINAL = "20260925T213751_16_Peg_ba79e32c.fits"


def _setup(tmp_path, monkeypatch, params, with_law=True):
    header = fits.Header()
    header["CRVAL1"], header["CDELT1"], header["CRPIX1"], header["CUNIT1"] = 3752.7, 3.4723, 1.0, "Angstrom"
    header["DATE-OBS"], header["JD-MID"], header["OBJNAME"] = "2026-09-25T21:39:44.232", 2461309.405763, "16 Peg"
    header["EXPTIME2"] = "5x107.7s"
    fits.writeto(tmp_path / FINAL, np.linspace(5, 0.5, 1080).astype(np.float32), header)
    obs = Observation(id="ba79e32c", created_at=datetime.now(timezone.utc), session_id="0493029c",
                      obs_dir=str(tmp_path), yaml_file=str(tmp_path / "20260925T213751_16_Peg.yaml"))
    obs.scheduler_data["program"] = "Be_stars"
    obs.reduction_data["final_spectrum"] = FINAL
    if with_law:
        (tmp_path / "CALIB_law.yaml").write_text(
            "# Dispersion law.\n# 8 reference lines, RMS 0.203 A (0.059 px); valid between 4158.59 and 7147.04 A "
            "(extrapolated outside).\nunit: A\nA0: 3054.8\nA1: 3.5\n")
        obs.reduction_data["dispersion_law_file"] = "CALIB_law.yaml"
    monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {"plot_spectrum": params})
    monkeypatch.setattr(module, "OBSERVATORY", {"site": "St-Pancrasse"})
    return obs


class TestPlotSpectrumPlugin:

    async def test_every_format(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, {"png": True, "pdf": True, "svg": True, "balmer_lines": True})

        result = await PlotSpectrumPlugin().execute(obs)

        stem = FINAL.removesuffix(".fits")
        assert result.reduction_data["plot_spectrum"]["files"] == [f"{stem}.png", f"{stem}.pdf", f"{stem}.svg"]
        assert result.documents == {"spectrum": f"{stem}.pdf"}   # the PDF first
        assert (tmp_path / f"{stem}.png").read_bytes()[:4] == b"\x89PNG"
        assert (tmp_path / f"{stem}.pdf").read_bytes()[:4] == b"%PDF"
        assert b"<svg" in (tmp_path / f"{stem}.svg").read_bytes()[:500]

    @pytest.mark.parametrize("fmt", ["png", "pdf", "svg"])
    async def test_only_the_requested_format(self, tmp_path, monkeypatch, fmt):
        obs = _setup(tmp_path, monkeypatch, {fmt: True}, with_law=False)

        result = await PlotSpectrumPlugin().execute(obs)

        assert result.reduction_data["plot_spectrum"]["files"] == [FINAL.replace(".fits", f".{fmt}")]
        assert result.documents == {"spectrum": FINAL.replace(".fits", f".{fmt}")}
        assert {p.suffix for p in tmp_path.iterdir()} == {".fits", f".{fmt}"}

    async def test_nothing_when_no_format_is_requested(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, {"png": False, "pdf": False, "svg": False})

        result = await PlotSpectrumPlugin().execute(obs)

        assert "plot_spectrum" not in result.reduction_data
        assert result.documents == {}
        assert {p.suffix for p in tmp_path.iterdir()} == {".fits", ".yaml"}
