"""
Tests for the SUMMARY sub-plugin (SummaryPlugin), on a synthetic observation.
Run: pytest plugins/reduction_plugins/summary/test_summary.py -v
"""

import re
from datetime import datetime, timezone

import numpy as np
from astropy.io import fits

import rosace.plugins.sequencer_plugins.acquisition_helpers as acquisition_helpers
import rosace.session_log_extractor as session_log_extractor
from rosace.models import Observation, Target
from rosace.plugins.reduction_plugins.summary import summary as module
from rosace.plugins.reduction_plugins.summary.summary import SummaryPlugin, log_events

SUB = "Vega_0e37017f"
WIDTH, HEIGHT = 300, 120
LAW = (4000.0, 10.0)                       # lambda = 4000 + 10 x
REFS = [4300.0, 5000.0, 5600.0, 6500.0]
PARAMS = {
    "observatory": {"observer": "F. Cochard", "site": "St-Pancrasse", "equipment": "Alpy600-72ED-AM3"},
    "dispersion_law": {"tolerance": 1.0},
    "scaling": {"wavelength_min": 6600, "wavelength_max": 6700},
    "crop": {"wavelength_min": 4100, "wavelength_max": 6900},
    "guiding": {"slit_x": 50, "slit_y": 40},
}


def _spectrum_1d(path, data, start=None, step=None, date="2026-09-25T21:39:44.000", exptime=100.0):
    header = fits.Header()
    header["DATE-OBS"], header["EXPTIME"] = date, exptime
    if start is not None:
        header["CRVAL1"], header["CDELT1"], header["CRPIX1"], header["CUNIT1"] = start, step, 1, "Angstrom"
    fits.writeto(path, np.asarray(data, dtype=np.float32), header)


def _lamp():
    x = np.arange(WIDTH, dtype=float)
    lamp = np.full(WIDTH, 100.0)
    for wavelength in REFS:
        lamp += 5000.0 * np.exp(-0.5 * ((x - (wavelength - LAW[0]) / LAW[1]) / 1.5) ** 2)
    return lamp


def _setup(tmp_path, monkeypatch, with_guide=True, log_text=""):
    session = tmp_path / "session"
    (session / SUB).mkdir(parents=True)
    obs = Observation(id="0e37017f", created_at=datetime(2026, 9, 25, 21, 37, 51, tzinfo=timezone.utc),
                      session_id="sess1234", obs_dir=str(session), yaml_file=str(session / "20260925T213751_Vega.yaml"),
                      program_steps={"reduction": ["extract", "stack", "crop"]})
    obs.scheduler_data.update(queried_name="Vega", program="Be_stars", priority=3,
                              target=Target(name="* alf Lyr", ra=279.2347, dec=38.7837, magnitude_v=0.03))
    red = obs.reduction_data
    # Final spectrum
    final = "20260925T213751_Vega_0e37017f.fits"
    header = fits.Header()
    header["OBJNAME"], header["RA"], header["DEC"] = "Vega", 279.2347, 38.7837
    header["DATE-OBS"], header["DATE-END"], header["EXPTIME"], header["EXPTIME2"] = (
        "2026-09-25T21:39:44.000", "2026-09-25T21:48:51.000", 547.0, "5x100s")
    header["CRVAL1"], header["CDELT1"], header["CRPIX1"], header["CUNIT1"] = 4100.0, 10.0, 1.0, "Angstrom"
    fits.writeto(session / final, np.linspace(2, 0.5, 281).astype(np.float32), header)
    red["final_spectrum"] = final
    # Dispersion law and the reference list copy
    (session / "CALIB_x_dispersion_law.yaml").write_text(
        "# Dispersion law of lamp series CALIB_x, computed by Rosace.\n"
        "# 4 reference lines, RMS 0.100 A (0.010 px); valid between 4300 and 6500 A (extrapolated outside).\n"
        f"unit: A\nA0: {LAW[0]}\nA1: {LAW[1]}\n")
    (session / "CALIB_x_reference_lines.txt").write_text("\n".join(str(r) for r in REFS) + "\n")
    red["dispersion_law_file"] = "CALIB_x_dispersion_law.yaml"
    # Stacked and calibrated target and lamp
    _spectrum_1d(session / SUB / "lamp_stack.fits", _lamp())
    red["stack_calib_spectrum"] = f"{SUB}/lamp_stack.fits"
    _spectrum_1d(session / SUB / "target_lambda.fits", np.linspace(5000, 1000, WIDTH), 4000.0, 10.0)
    _spectrum_1d(session / SUB / "lamp_lambda.fits", _lamp(), 4000.0, 10.0)
    red["lambda_target_spectrum"], red["lambda_calib_spectrum"] = f"{SUB}/target_lambda.fits", f"{SUB}/lamp_lambda.fits"
    # The series: 1D spectra and corrected 2D frames
    spectra, frames = [], []
    for i in range(3):
        _spectrum_1d(session / SUB / f"t-{i}_1d.fits", np.linspace(500, 100, WIDTH) * (1 + 0.1 * i),
                     date=f"2026-09-25T21:{40 + 2 * i}:00.000")
        fits.writeto(session / SUB / f"t-{i}_smile.fits", np.full((HEIGHT, WIDTH), 5.0 + i, dtype=np.float32))
        spectra.append(f"{SUB}/t-{i}_1d.fits")
        frames.append(f"{SUB}/t-{i}_smile.fits")
    red["extract_target_spectra"], red["smile_target_images"] = spectra, frames
    red["extract"] = {"sky_zones": [[10, 30], [80, 100]]}
    red["stack"] = {"rejected": [{"file": spectra[2], "flux_fraction": 0.2}]}
    # Guide image after the science exposures (no astrometric solution here)
    if with_guide:
        fits.writeto(session / "GUIDING_Vega.fits", np.random.default_rng(0).normal(1000, 10, (100, 120)).astype(np.float32))
        obs.sequencer_data["stop_phd2_guiding"] = {"guide_image": "GUIDING_Vega.fits", "residual_arcsec": 2.8}
    monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", PARAMS)
    monkeypatch.setattr(module, "OBSERVATORY", PARAMS["observatory"])
    monkeypatch.setattr(acquisition_helpers, "SUBPLUGIN_PARAMS", PARAMS)
    (tmp_path / "log").mkdir()
    (tmp_path / "log" / "rosace.log").write_text(log_text)
    monkeypatch.setattr(session_log_extractor, "LOG_DIR", tmp_path / "log")
    return obs, session


def _page_count(path):
    return len(re.findall(rb"/Type\s*/Page[^s]", path.read_bytes()))


class TestSummaryPlugin:

    async def test_four_pages(self, tmp_path, monkeypatch):
        obs, session = _setup(tmp_path, monkeypatch)

        result = await SummaryPlugin().execute(obs)

        name = result.reduction_data["summary_file"]
        assert name == "20260925T213751_Vega_0e37017f_summary.pdf"
        assert _page_count(session / name) == 4

    async def test_missing_guide_image_does_not_fail(self, tmp_path, monkeypatch, caplog):
        obs, session = _setup(tmp_path, monkeypatch, with_guide=False)

        result = await SummaryPlugin().execute(obs)

        assert _page_count(session / result.reduction_data["summary_file"]) == 4
        assert "Guide image not available" in caplog.text


LOG = """2026-09-25 21:37:40 - WARNING - [CROP] warning of the previous observation's reduction
2026-09-25 21:37:51 - INFO - [DB] Observation inserted: 0e37017f
2026-09-25 21:38:00 - WARNING - Sanitizing exposition time: cannot accept 0.0
2026-09-25 21:38:05 - WARNING - [CROP] previous observation's reduction, still running
2026-09-25 21:38:06 - ERROR - [FOCUS] No usable star
  continuation of the focus error
2026-09-25 21:49:13 - INFO - [DB] sequencer_done stamped for 0e37017f
2026-09-25 21:49:14 - WARNING - [FOCUS] next observation's acquisition
2026-09-25 21:49:20 - INFO - [REDUCTION] Starting reduction 'Be_stars' - 3 step(s)
2026-09-25 21:49:21 - WARNING - [STACK] Target spectrum rejected
2026-09-25 21:49:22 - WARNING - [SLEW_72ED] next observation, during our reduction
"""


class TestLogEvents:

    def test_acquisition_and_reduction_warnings_of_this_observation_only(self, tmp_path, monkeypatch):
        obs, _ = _setup(tmp_path, monkeypatch, log_text=LOG)

        events = log_events(obs)

        assert [(phase, level, message[:30]) for phase, _, level, message in events] == [
            ("acquisition", "WARNING", "Sanitizing exposition time: ca"),
            ("acquisition", "ERROR", "[FOCUS] No usable star continu"),
            ("reduction", "WARNING", "[STACK] Target spectrum reject"),
        ]
