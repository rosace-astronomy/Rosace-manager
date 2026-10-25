"""
Tests for the anonymization of the demo's replay dataset
(build_demo_dataset.py::anonymize_dataset, check_anonymized), on a small
temporary dataset. Run: pytest scripts/test_build_demo_dataset.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_demo_dataset as builder  # noqa: E402


def _dataset(root: Path) -> Path:
    header = fits.Header({"SITELAT": 45.28968, "SITELONG": 5.855436, "SITEELEV": 955.0,
                          "OBSERVER": "F. Cochard", "OBJECT": "Vega", "RA": 279.23, "DEC": 38.78,
                          "OBJCTALT": 66.41526, "OBJCTAZ": 248.0397, "AIRMASS": 1.091006,
                          "COMMENT": "Taken at St-Pancrasse"})
    (root / "science").mkdir()
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.uint16), header).writeto(root / "science" / "frame.fits")
    response = fits.Header({"SITELAT": "0", "SITELONG": "0", "SITEELEV": "0", "SITENAME": "OHP",
                            "OBSERVER": "F. Cochard"})
    fits.PrimaryHDU(np.ones(8, dtype=np.float32), response).writeto(root / "response.fit")
    (root / "manifest.yaml").write_text("source: 2026-10-14_St-Pancrasse, observations 6a794eb7\nstars: []\n")
    (root / "guide.txt").write_text(
        "Equipment Profile = St-Pan 72ED\n"
        "RA = 3.64 hr, Dec = 48.2 deg, Hour angle = -1.09 hr, Pier side = West, Rotator pos = N/A, "
        "Alt = 78.5 deg, Az = 69.4 deg\n"
        "175.85,0.3\nthe first panel\n")
    return root


def test_dataset_is_anonymized(tmp_path):
    builder.anonymize_dataset(_dataset(tmp_path))

    header = fits.getheader(tmp_path / "science" / "frame.fits")
    assert (header["SITELAT"], header["SITELONG"], header["SITEELEV"]) == (45.0, 6.0, 1000)
    assert header["OBSERVER"] == "rosace" and header["OBJECT"] == "Vega"
    assert (header["RA"], header["DEC"]) == (279.23, 38.78)
    assert not any(key in header for key in ("OBJCTALT", "OBJCTAZ", "AIRMASS"))
    assert "Taken at Rosace-Demo" in str(header["COMMENT"])
    response = fits.getheader(tmp_path / "response.fit")
    assert (response["SITELAT"], response["SITEELEV"], response["SITENAME"], response["OBSERVER"]) == ("0", "0", "SiteLambda", "rosace")
    assert (tmp_path / "manifest.yaml").read_text().startswith("source: night 2026-10-14, observations 6a794eb7")
    guide = (tmp_path / "guide.txt").read_text()
    assert "Rosace-Demo 72ED" in guide and "175.85" in guide and "the first panel" in guide
    assert "RA = 3.64 hr, Dec = 48.2 deg, Pier side = West, Rotator pos = N/A\n" in guide


def test_anonymizing_twice_changes_nothing(tmp_path, capsys):
    builder.anonymize_dataset(_dataset(tmp_path))
    builder.anonymize_dataset(tmp_path)
    assert "Anonymized: 0 change(s)" in capsys.readouterr().out


def test_check_refuses_a_leftover(tmp_path):
    (tmp_path / "notes.txt").write_text("observer: F. Cochard\n")
    with pytest.raises(SystemExit, match="Cochard"):
        builder.check_anonymized(tmp_path)
    (tmp_path / "notes.txt").write_text("lat 45.28968\n")
    with pytest.raises(SystemExit, match="45.289"):
        builder.check_anonymized(tmp_path)
    (tmp_path / "notes.txt").write_text("RA = 3.64 hr, Dec = 48.2 deg, Alt = 78.5 deg, Az = 69.4 deg\n")
    with pytest.raises(SystemExit, match="Alt = 78.5"):
        builder.check_anonymized(tmp_path)


def test_check_refuses_a_horizontal_card(tmp_path):
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.uint16),
                    fits.Header({"OBJCTALT": 66.4})).writeto(tmp_path / "frame.fits")
    with pytest.raises(SystemExit, match="card OBJCTALT"):
        builder.check_anonymized(tmp_path)


def test_gzip_frame_is_anonymized_and_stays_compressed(tmp_path):
    path = tmp_path / "frame.fits.gz"
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.uint16),
                    fits.Header({"SITELAT": 45.28968, "OBSERVER": "F. Cochard"})).writeto(path)
    builder.anonymize_dataset(tmp_path)

    assert path.read_bytes()[:2] == b"\x1f\x8b"           # still gzip
    header = fits.getheader(path)
    assert (header["SITELAT"], header["OBSERVER"]) == (45.0, "rosace")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["frame.fits.gz"]   # no temporary file left
