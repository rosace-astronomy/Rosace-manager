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
from rosace.phd2.guide_log import CSV_HEADER
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


def _setup(tmp_path, monkeypatch, with_guide=True, log_text="", with_sky=True):
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
    red["extract"] = {"window": [45, 65], "sky_zones": [[10, 30], [80, 100]]}
    red["stack"] = {"rejected": [{"file": spectra[2], "flux_fraction": 0.2}]}
    # Guide image after the science exposures (no astrometric solution here)
    if with_guide:
        fits.writeto(session / "GUIDING_Vega.fits", np.random.default_rng(0).normal(1000, 10, (100, 120)).astype(np.float32))
        obs.sequencer_data["stop_phd2_guiding"] = {"guide_image": "GUIDING_Vega.fits", "residual_arcsec": 2.8}
        _guide_log(session / "Vega_PHD2_GuideLog.txt")
        obs.sequencer_data["guide_log_file"] = "Vega_PHD2_GuideLog.txt"
        red["guiding_stats"] = {"total_rms_arcsec": 1.04, "ra_rms_arcsec": 0.86, "dec_rms_arcsec": 0.58,
                                "ra_max_arcsec": 0.86, "dec_max_arcsec": 0.58, "frames": 199, "dropped": 1,
                                "pixel_scale": 2.88}
    # FRIPON image, as fripon_image leaves it
    if with_sky:
        from matplotlib.image import imsave
        imsave(session / "Vega_fripon_annotated.png", np.full((30, 40), 0.5), cmap="gray")
        red["sky_camera"] = {"station": "FRRA12", "capture_time": "2026-09-25T21:45:17+00:00", "offset_s": 125,
                             "file": "FRRA12_20260925T214517_UT.jpg", "annotated_file": "Vega_fripon_annotated.png",
                             "model": "fitted", "matched_stars": 80, "rms_px": 1.1, "target_pixel": [20.0, 15.0]}
        # wide-field image, as wide_field_image leaves it
        imsave(session / "Vega_wide_field.png", np.full((30, 40), 0.4), cmap="gray")
        red["wide_field"] = {"file": "WIDEFIELD_Vega_5s_20260925_214000.fits", "annotated_file": "Vega_wide_field.png",
                             "capture_time": "2026-09-25T21:40:00+00:00", "exposure_s": 5.0, "solved": True,
                             "scale_arcsec": 31.2, "field_deg": [5.65, 4.23], "target_offset_arcmin": 12.0,
                             "target_pixel": [20.0, 15.0]}
    monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", PARAMS)
    monkeypatch.setattr(module, "OBSERVATORY", PARAMS["observatory"])
    monkeypatch.setattr(acquisition_helpers, "SUBPLUGIN_PARAMS", PARAMS)
    (tmp_path / "log").mkdir()
    (tmp_path / "log" / "rosace.log").write_text(log_text)
    monkeypatch.setattr(session_log_extractor, "LOG_DIR", tmp_path / "log")
    return obs, session


def _guide_log(path):
    """A guide log in PHD2's format covering the series (local times, like PHD2's)."""
    local = datetime(2026, 9, 25, 21, 39, 0, tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    rows = [f'{i},{3.0 * i:.3f},"Mount",0.0,0.0,{0.3 * (-1) ** i},{-0.2 * (-1) ** i},{0.2 * (-1) ** i},0.000,'
            f'{100 if i % 2 else 0},{"W" if i % 2 else ""},0,,,,1000,30.00,0' for i in range(1, 200)]
    rows.insert(3, "INFO: SET LOCK POSITION, new lock pos = 50.000, 40.000")
    rows.insert(50, '50,150.500,"DROP",,,,,,,,,,,,,0,0.00,2,"Star lost - low SNR"')
    path.write_text("\n".join([f"Guiding Begins at {local}", "Pixel scale = 2.88 arc-sec/px", CSV_HEADER, *rows]) + "\n")


def _page_count(path):
    return len(re.findall(rb"/Type\s*/Page[^s]", path.read_bytes()))


class TestSummaryPlugin:

    async def test_five_pages(self, tmp_path, monkeypatch, caplog):
        obs, session = _setup(tmp_path, monkeypatch)

        result = await SummaryPlugin().execute(obs)

        name = result.reduction_data["summary_file"]
        assert name == "20260925T213751_Vega_0e37017f_summary.pdf"
        assert _page_count(session / name) == 5
        assert "PHD2 guiding not available" not in caplog.text
        assert "FRIPON sky image not available" not in caplog.text
        assert "Wide-field image not available" not in caplog.text

    async def test_missing_guide_image_does_not_fail(self, tmp_path, monkeypatch, caplog):
        obs, session = _setup(tmp_path, monkeypatch, with_guide=False)

        result = await SummaryPlugin().execute(obs)

        assert _page_count(session / result.reduction_data["summary_file"]) == 5
        assert "Guide image not available" in caplog.text
        assert "PHD2 guiding not available: no guide log recorded" in caplog.text


    async def test_missing_sky_image_does_not_fail(self, tmp_path, monkeypatch, caplog):
        obs, session = _setup(tmp_path, monkeypatch, with_sky=False)

        result = await SummaryPlugin().execute(obs)

        assert _page_count(session / result.reduction_data["summary_file"]) == 5
        assert "FRIPON sky image not available: no FRIPON image" in caplog.text
        assert "Wide-field image not available: no wide-field image" in caplog.text

    async def test_unsolved_wide_field_image_is_drawn(self, tmp_path, monkeypatch, caplog):
        obs, session = _setup(tmp_path, monkeypatch)
        obs.reduction_data["wide_field"].update(solved=False, scale_arcsec=None, field_deg=None,
                                                target_offset_arcmin=None, target_pixel=None)

        result = await SummaryPlugin().execute(obs)

        assert _page_count(session / result.reduction_data["summary_file"]) == 5
        assert "Wide-field image not available" not in caplog.text


class TestQualityBand:

    def test_resolving_power_from_the_final_header(self, tmp_path, monkeypatch):
        obs, session = _setup(tmp_path, monkeypatch)
        final = session / obs.reduction_data["final_spectrum"]
        fits.setval(final, "BSS_ESRP", value=630.0)
        fits.setval(final, "BSS_SRPW", value=6563.0)

        assert module.quality_items(module.SummaryContext(obs)) == [("R = 630 at 6563 Å", True),
                                                               ("SNR: not measured", False),
                                                               ("Guiding: not measured", False)]

    def test_signal_to_noise_ratios_from_the_final_header(self, tmp_path, monkeypatch):
        obs, session = _setup(tmp_path, monkeypatch)
        final = session / obs.reduction_data["final_spectrum"]
        for keyword, value in (("BSS_ESRP", 630.0), ("BSS_SRPW", 6563.0), ("SNR1", 78.0), ("SNR1LO", 3750.0),
                               ("SNR1HI", 7500.0), ("SNR2", 38.0), ("SNR2LO", 6000.0), ("SNR2HI", 7000.0)):
            fits.setval(final, keyword, value=value)

        assert module.quality_items(module.SummaryContext(obs)) == [
            ("R = 630 at 6563 Å", True), ("SNR 78 (3750–7500 Å)", True), ("SNR 38 (6000–7000 Å)", True),
            ("Guiding: not measured", False)]

    def test_resolving_power_not_measured(self, tmp_path, monkeypatch):
        obs, _ = _setup(tmp_path, monkeypatch)

        assert module.quality_items(module.SummaryContext(obs)) == [("R: not measured", False),
                                                               ("SNR: not measured", False),
                                                               ("Guiding: not measured", False)]

    def test_guiding_rms_from_the_final_header(self, tmp_path, monkeypatch):
        obs, session = _setup(tmp_path, monkeypatch)
        fits.setval(session / obs.reduction_data["final_spectrum"], "GUIDRMS", value=1.24)

        assert module.quality_items(module.SummaryContext(obs))[-1] == ("Guiding RMS 1.24\"", True)


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
