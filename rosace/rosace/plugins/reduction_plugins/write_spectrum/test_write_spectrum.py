"""
Tests for the WRITE_SPECTRUM sub-plugin (WriteSpectrumPlugin), on a synthetic cropped spectrum.
Run: pytest plugins/reduction_plugins/write_spectrum/test_write_spectrum.py -v

The final header is also checked against the rules file of the BeSS
specification (version 1.3, annex B): keyword types and valid ranges, and the
rules that make a spectrum rejected (E).
"""

import re
from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation, Target
from rosace.plugins.reduction_plugins.write_spectrum import write_spectrum as module
from rosace.plugins.reduction_plugins.write_spectrum.write_spectrum import WriteSpectrumPlugin

SUB = "16Peg_ba79e32c"
OBSERVATORY = {
    "observer": "F. Cochard", "site": "St-Pancrasse", "equipment": "Alpy600-72ED-AM3",
    "telescope": "72ED refractor", "spectrograph": "Alpy 600", "camera": "Atik 314L", "resolving_power": 600,
}

# Annex B of the BeSS specification: keyword -> (type, range)
BESS_KEYWORDS = {
    "NAXIS": (int, (1, 1)), "NAXIS1": (int, (1, 500000)),
    "RA": (float, (0, 360)), "DEC": (float, (-90, 90)), "EQUINOX": (float, (2000, 2000)),
    "OBJNAME": (str, (1, 40)), "EXPTIME": (float, (0, 20000)),
    "CRVAL1": (float, (300, 10000)), "CDELT1": (float, (1e-4, 30)), "CRPIX1": (float, None),
    "CTYPE1": (str, ["Wavelength"]), "CUNIT1": (str, ["Angstrom", "nm"]), "OBSERVER": (str, None),
    "BSS_INST": (str, (1, 40)), "TELESCOP": (str, (1, 40)), "DETNAM": (str, (1, 40)), "INSTRUME": (str, (1, 40)),
    "BSS_SITE": (str, (1, 40)), "BSS_LAT": (float, (-90, 90)), "BSS_LONG": (float, (0, 360)),
    "BSS_ELEV": (float, (-200, 5500)), "BSS_VHEL": (float, (-200, 200)),
    "BSS_TELL": (str, None), "BSS_COSM": (str, None), "BSS_NORM": (str, None),
    "BSS_STYP": (str, None), "BSS_VMAG": (float, (-27, 30)), "BSS_ITRP": (float, None),
    "BSS_ESRP": (float, (0, 1e6)), "BSS_SRPW": (float, (0, 1e6)),
    "SNR1": (float, (0, 1e6)), "SNR1LO": (float, (0, 1e6)), "SNR1HI": (float, (0, 1e6)),
    "SNR2": (float, (0, 1e6)), "SNR2LO": (float, (0, 1e6)), "SNR2HI": (float, (0, 1e6)),
    "GUIDRMS": (float, (0, 1e4)), "GUIDRMSR": (float, (0, 1e4)), "GUIDRMSD": (float, (0, 1e4)),
    "GUIDMAXR": (float, (0, 1e4)), "GUIDMAXD": (float, (0, 1e4)),
}
# Spectra passed to the GUI publication, mocked by _setup()
PUBLISHED: list = []

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")


def bess_errors(header: fits.Header) -> list[str]:
    """The annex B checks that would make BeSS reject the spectrum."""
    errors = []
    for keyword, (kind, valid) in BESS_KEYWORDS.items():
        if keyword not in header:
            continue
        value = header[keyword]
        if kind is str:
            if not isinstance(value, str):
                errors.append(f"{keyword} not a string")
            elif isinstance(valid, list) and value not in valid:
                errors.append(f"{keyword}={value!r} not in {valid}")
            elif isinstance(valid, tuple) and not valid[0] <= len(value) <= valid[1]:
                errors.append(f"{keyword} length {len(value)} out of {valid}")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"{keyword} not a number")
            elif valid and not valid[0] <= value <= valid[1]:
                errors.append(f"{keyword}={value} out of {valid}")
    for keyword in ("DATE-OBS", "DATE-END"):
        if keyword in header and not ISO_DATE.match(header[keyword]):
            errors.append(f"{keyword} not an ISO date")
    has = lambda *keys: all(k in header for k in keys)
    rules = {
        "object": has("RA", "DEC") and (has("EQUINOX") or has("RADECSYS")) or has("OBJNAME"),
        "time": has("DATE-OBS", "DATE-END") or has("DATE-OBS", "EXPTIME") or has("EXPTIME", "DATE-END"),
        "axis": has("CRVAL1", "CDELT1", "CRPIX1", "CTYPE1", "CUNIT1"),
        "observer": has("OBSERVER"),
        "equipment": has("BSS_INST") or has("TELESCOP", "INSTRUME", "DETNAM"),
        "site": has("BSS_SITE") or has("BSS_LAT", "BSS_LONG", "BSS_ELEV"),
        "heliocentric": has("BSS_VHEL"),
        "naxis": header.get("NAXIS") == 1,
    }
    errors += [f"rule {name} not met" for name, met in rules.items() if not met]
    return errors


def _setup(tmp_path, monkeypatch, observatory=OBSERVATORY, queried_name="16 Peg", longitude=5.855):
    (tmp_path / SUB).mkdir()
    header = fits.Header()
    header["CRVAL1"], header["CDELT1"], header["CRPIX1"] = 3752.73, 3.4723, 1
    header["CTYPE1"], header["CUNIT1"] = "Wavelength", "Angstrom"
    header["DATE-OBS"], header["DATE-END"] = "2026-09-25T21:39:44.232", "2026-09-25T21:48:51.703"
    header["EXPTIME"], header["EXPTIME2"] = 547.5, "5x107.7s"
    header["CCD-TEMP"], header["ROWORDER"], header["YBINNING"], header["PIERSIDE"] = 0.04, "TOP-DOWN", 1, "EAST"
    header["COMMENT"] = "  FITS (Flexible Image Transport System) format is defined in 'Astronomy"
    header["COMMENT"] = "Generated by INDI"
    header["COMMENT"] = "Sum of 5 1D spectra"
    fits.writeto(tmp_path / SUB / "16Peg_crop.fits", np.linspace(1, 2, 1080).astype(np.float32), header)
    obs = Observation(id="ba79e32c", created_at=datetime(2026, 9, 25, 21, 37, 51, tzinfo=timezone.utc),
                      session_id="0493029c", obs_dir=str(tmp_path),
                      yaml_file=str(tmp_path / "20260925T213751_16_Peg.yaml"))
    obs.scheduler_data["target"] = Target(name="*  16 Peg", ra=328.2657026, dec=25.9251358, magnitude_v=5.08)
    obs.scheduler_data["queried_name"] = queried_name
    obs.reduction_data["crop_target_spectrum"] = f"{SUB}/16Peg_crop.fits"
    monkeypatch.setattr(module, "OBSERVATORY", observatory)
    monkeypatch.setattr(module, "SITE_LATITUDE_DEG", 45.2895)
    monkeypatch.setattr(module, "SITE_LONGITUDE_DEG", longitude)
    monkeypatch.setattr(module, "SITE_ELEVATION_M", 955)

    async def star(name):
        return type("Star", (), {"spectral_type": "B6IIIe"})() if name == "*  16 Peg" else None
    monkeypatch.setattr(module, "star_get_by_name", star)
    PUBLISHED.clear()

    async def publish(obs, path):
        PUBLISHED.append(path)
    monkeypatch.setattr(module, "publish_spectrum_preview", publish)
    return obs


class TestWriteSpectrumPlugin:

    async def test_bess_header(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch)

        result = await WriteSpectrumPlugin().execute(obs)

        name = result.reduction_data["final_spectrum"]
        assert name == "20260925T213751_16_Peg_ba79e32c.fits"
        data, h = fits.getdata(tmp_path / name, header=True)
        assert data.size == 1080 and h["NAXIS"] == 1 and h["BITPIX"] == -32
        assert h["OBJNAME"] == "16 Peg" and h["RA"] == pytest.approx(328.265703) and h["EQUINOX"] == 2000.0
        assert h["BSS_VMAG"] == 5.08 and h["BSS_STYP"] == "B6IIIe"
        assert h["EXPTIME"] == 547.5 and h["EXPTIME2"] == "5x107.7s"
        assert h["JD-OBS"] == pytest.approx(2461309.402595, abs=1e-6)
        assert h["JD-MID"] == pytest.approx(2461309.405763, abs=2e-6)
        assert (h["OBSERVER"], h["BSS_SITE"], h["BSS_INST"]) == ("F. Cochard", "St-Pancrasse", "Alpy600-72ED-AM3")
        assert (h["TELESCOP"], h["INSTRUME"], h["DETNAM"]) == ("72ED refractor", "Alpy 600", "Atik 314L")
        assert (h["BSS_LAT"], h["BSS_LONG"], h["BSS_ELEV"], h["BSS_ITRP"]) == (45.2895, 5.855, 955.0, 600.0)
        assert (h["BSS_VHEL"], h["BSS_TELL"], h["BSS_COSM"], h["BSS_NORM"]) == (0.0, "none", "none", "none")
        assert h["CTYPE1"] == "Wavelength" and h["CUNIT1"] == "Angstrom" and h["CRPIX1"] == 1.0
        assert h["SWCREATE"].startswith("Rosace")
        for camera_card in ("ROWORDER", "YBINNING", "PIERSIDE"):
            assert camera_card not in h
        assert [str(c) for c in h["COMMENT"]] == ["Sum of 5 1D spectra"]
        assert "BSS_ESRP" not in h and "BSS_SRPW" not in h
        assert "SNR1" not in h and "GUIDRMS" not in h
        assert bess_errors(h) == []

    async def test_signal_to_noise_ratio(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch)
        obs.reduction_data["spectrum_snr"] = [{"wavelength_min": 3750.0, "wavelength_max": 7500.0, "snr": 78.0},
                                              {"wavelength_min": 6000.0, "wavelength_max": 7000.0, "snr": 38.0}]

        result = await WriteSpectrumPlugin().execute(obs)

        h = fits.getheader(tmp_path / result.reduction_data["final_spectrum"])
        assert (h["SNR1"], h["SNR1LO"], h["SNR1HI"]) == (78.0, 3750.0, 7500.0)
        assert (h["SNR2"], h["SNR2LO"], h["SNR2HI"]) == (38.0, 6000.0, 7000.0)
        assert "SNR3" not in h
        assert bess_errors(h) == []

    async def test_guiding_statistics(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch)
        obs.reduction_data["guiding_stats"] = {"total_rms_arcsec": 1.24, "ra_rms_arcsec": 0.89, "dec_rms_arcsec": 0.85,
                                               "ra_max_arcsec": 10.56, "dec_max_arcsec": 10.47}

        result = await WriteSpectrumPlugin().execute(obs)

        h = fits.getheader(tmp_path / result.reduction_data["final_spectrum"])
        assert (h["GUIDRMS"], h["GUIDRMSR"], h["GUIDRMSD"], h["GUIDMAXR"], h["GUIDMAXD"]) == (
            1.24, 0.89, 0.85, 10.56, 10.47)
        assert bess_errors(h) == []

    async def test_measured_resolving_power(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch)
        (tmp_path / "CALIB_law.yaml").write_text(
            "# a law\nunit: A\nA0: 3000.0\nA1: 3.5\nresolving_power: 630.0\nresolving_power_wavelength: 6563.0\n")
        obs.reduction_data["dispersion_law_file"] = "CALIB_law.yaml"

        result = await WriteSpectrumPlugin().execute(obs)

        h = fits.getheader(tmp_path / result.reduction_data["final_spectrum"])
        assert (h["BSS_ESRP"], h["BSS_SRPW"], h["BSS_ITRP"]) == (630.0, 6563.0, 600.0)
        assert bess_errors(h) == []

    async def test_law_without_resolving_power(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch)
        (tmp_path / "CALIB_law.yaml").write_text("# a law\nunit: A\nA0: 3000.0\nA1: 3.5\n")
        obs.reduction_data["dispersion_law_file"] = "CALIB_law.yaml"

        result = await WriteSpectrumPlugin().execute(obs)

        h = fits.getheader(tmp_path / result.reduction_data["final_spectrum"])
        assert "BSS_ESRP" not in h and "BSS_SRPW" not in h

    async def test_west_longitude_brought_into_0_360(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, longitude=-70.5)

        result = await WriteSpectrumPlugin().execute(obs)

        h = fits.getheader(tmp_path / result.reduction_data["final_spectrum"])
        assert h["BSS_LONG"] == pytest.approx(289.5)

    @pytest.mark.parametrize("missing", ["observer", "site", "equipment"])
    async def test_missing_bess_name_is_an_error(self, tmp_path, monkeypatch, missing):
        obs = _setup(tmp_path, monkeypatch, observatory={k: v for k, v in OBSERVATORY.items() if k != missing})

        with pytest.raises(RuntimeError, match=f"observatory.{missing}"):
            await WriteSpectrumPlugin().execute(obs)

    async def test_text_longer_than_40_characters_is_an_error(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, observatory=OBSERVATORY | {"equipment": "x" * 41})

        with pytest.raises(RuntimeError, match="longer than 40"):
            await WriteSpectrumPlugin().execute(obs)

    async def test_non_ascii_is_an_error(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch, queried_name="16 Pégase")

        with pytest.raises(RuntimeError, match="non-ASCII"):
            await WriteSpectrumPlugin().execute(obs)

    async def test_spectrum_published_to_the_gui(self, tmp_path, monkeypatch):
        obs = _setup(tmp_path, monkeypatch)

        result = await WriteSpectrumPlugin().execute(obs)

        assert PUBLISHED == [tmp_path / result.reduction_data["final_spectrum"]]

    async def test_gui_publication_failure_is_only_a_warning(self, tmp_path, monkeypatch, caplog):
        obs = _setup(tmp_path, monkeypatch)

        async def failing(obs, path):
            raise OSError("disk full")
        monkeypatch.setattr(module, "publish_spectrum_preview", failing)

        result = await WriteSpectrumPlugin().execute(obs)

        assert result.reduction_data["final_spectrum"]
        assert "Could not show the spectrum in the GUI" in caplog.text

    def test_rules_checker_catches_a_bad_header(self):
        header = fits.Header()
        header["NAXIS"], header["CTYPE1"], header["BSS_LONG"] = 1, "Pixel", -5.0
        errors = bess_errors(header)
        assert "CTYPE1='Pixel' not in ['Wavelength']" in errors
        assert "BSS_LONG=-5.0 out of (0, 360)" in errors
        assert "rule observer not met" in errors
