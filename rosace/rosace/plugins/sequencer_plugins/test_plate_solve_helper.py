"""
Tests for plate_solve_helper.py. solve-field itself is never actually run --
subprocess.run() is monkeypatched to mimic its real side effects (writing
<base>.new and touching <base>.solved on success), so these tests work
without astrometry.net installed.

Launch: pytest plugins/sequencer_plugins/test_plate_solve_helper.py -v
"""

import subprocess
import warnings
from pathlib import Path

import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import FITSFixedWarning

from rosace.database import db_init
from rosace.plugins.sequencer_plugins import plate_solve_helper
from rosace.plugins.sequencer_plugins.plate_solve_helper import (
    PlateSolveError,
    pixel_scale_arcsec,
    solve_result,
    solve_wcs,
    world_to_pixel,
)

NAXIS = 99  # odd, so (NAXIS-1)/2 is an exact integer pixel


def _make_guide_fits(tmp_path: Path, ra=279.23, dec=38.78, target_name="Vega", with_radec=True) -> Path:
    """A minimal FITS file with RA/DEC/OBJECT stamped in the primary header --
    mimics what acquisition_helpers.py::capture_guide_image() will eventually
    stamp at capture time, before any WCS solution exists."""
    data = np.zeros((NAXIS, NAXIS), dtype=np.float32)
    header = fits.Header()
    if with_radec:
        header["RA"] = ra
        header["DEC"] = dec
    header["OBJECT"] = target_name
    path = tmp_path / "guide.fits"
    fits.PrimaryHDU(data, header=header).writeto(path, overwrite=True)
    return path


def _stub_which(monkeypatch, found: bool = True) -> None:
    monkeypatch.setattr(
        plate_solve_helper.shutil, "which", lambda name: "/usr/bin/solve-field" if found else None
    )


def _install_fake_solve(monkeypatch, captured_cmds: list | None = None):
    """Patches subprocess.run to mimic solve-field's real side effects on
    success: reads the input FITS's existing header (RA/DEC/OBJECT), adds a
    tangent-plane WCS centered exactly on that RA/DEC at the image's center
    pixel, writes it to <base>.new, and touches <base>.solved."""

    def _fake_run(cmd, **kwargs):
        if captured_cmds is not None:
            captured_cmds.append(cmd)
        fits_path = Path(cmd[-1])
        base = fits_path.with_suffix("")
        with fits.open(fits_path) as hdul:
            header = hdul[0].header.copy()
            data = hdul[0].data
        naxis1, naxis2 = header["NAXIS1"], header["NAXIS2"]
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRVAL1"] = float(header.get("RA", 279.23))
        header["CRVAL2"] = float(header.get("DEC", 38.78))
        header["CRPIX1"] = (naxis1 + 1) / 2  # FITS/WCS CRPIX is 1-indexed
        header["CRPIX2"] = (naxis2 + 1) / 2
        header["CDELT1"] = -0.001
        header["CDELT2"] = 0.001
        fits.PrimaryHDU(data, header=header).writeto(f"{base}.new", overwrite=True)
        Path(f"{base}.solved").touch()
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(plate_solve_helper.subprocess, "run", _fake_run)


def _install_failing_solve(monkeypatch, returncode=1):
    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=returncode, stdout="", stderr="")

    monkeypatch.setattr(plate_solve_helper.subprocess, "run", _fake_run)


def _install_timeout_solve(monkeypatch):
    def _fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(plate_solve_helper.subprocess, "run", _fake_run)


class TestSolveWcs:

    async def test_rejects_ra_hint_without_dec_hint(self, tmp_path):
        path = _make_guide_fits(tmp_path)
        with pytest.raises(ValueError, match="ra_hint and dec_hint"):
            await solve_wcs(str(path), ra_hint=10.0)

    async def test_rejects_non_fits_extension(self, tmp_path):
        path = tmp_path / "guide.txt"
        path.write_text("not a fits file")
        with pytest.raises(ValueError, match=r"\.fits"):
            await solve_wcs(str(path))

    async def test_raises_if_solve_field_not_on_path(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch, found=False)
        path = _make_guide_fits(tmp_path)
        with pytest.raises(PlateSolveError, match="not found on PATH"):
            await solve_wcs(str(path))

    async def test_success_replaces_file_with_solved_wcs(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path)

        await solve_wcs(str(path))

        with fits.open(path) as hdul:
            assert "CRVAL1" in hdul[0].header
            assert hdul[0].header["OBJECT"] == "Vega"  # original header preserved

    async def test_success_logs_info(self, tmp_path, monkeypatch, caplog):
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path)

        with caplog.at_level("INFO", logger="spectro.subplugin.plate_solve_helper"):
            await solve_wcs(str(path))

        assert any("WCS solved" in r.message for r in caplog.records)

    async def test_success_cleans_up_byproducts(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path)
        base = str(path.with_suffix(""))
        for suffix in (".solved", ".axy", ".rdls", "-indx.xyls", ".match", ".corr", ".wcs"):
            Path(f"{base}{suffix}").touch()

        await solve_wcs(str(path))

        for suffix in (".solved", ".axy", ".rdls", "-indx.xyls", ".match", ".corr", ".wcs"):
            assert not Path(f"{base}{suffix}").exists()

    async def test_failure_raises_and_keeps_original_file(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_failing_solve(monkeypatch)
        path = _make_guide_fits(tmp_path)

        with pytest.raises(PlateSolveError, match="no WCS solution"):
            await solve_wcs(str(path))

        assert path.exists()
        with fits.open(path) as hdul:
            assert "CRVAL1" not in hdul[0].header  # untouched, never solved

    async def test_failure_logs_error(self, tmp_path, monkeypatch, caplog):
        _stub_which(monkeypatch)
        _install_failing_solve(monkeypatch)
        path = _make_guide_fits(tmp_path)

        with caplog.at_level("ERROR", logger="spectro.subplugin.plate_solve_helper"):
            with pytest.raises(PlateSolveError):
                await solve_wcs(str(path))

        assert any("no WCS solution" in r.message for r in caplog.records)

    async def test_logs_before_attempting_the_solve(self, tmp_path, monkeypatch, caplog):
        """The solve itself can take a while (up to timeout) -- this log line
        must appear before the outcome is known, not just on success/failure,
        so a caller isn't left wondering whether anything is happening."""
        _stub_which(monkeypatch)
        _install_failing_solve(monkeypatch)
        path = _make_guide_fits(tmp_path)

        with caplog.at_level("INFO", logger="spectro.subplugin.plate_solve_helper"):
            with pytest.raises(PlateSolveError):
                await solve_wcs(str(path), timeout=42)

        assert any("Solving" in r.message and "42s" in r.message for r in caplog.records)

    async def test_failure_message_includes_star_count_when_axy_present(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_failing_solve(monkeypatch)
        path = _make_guide_fits(tmp_path)
        base = str(path.with_suffix(""))
        col = fits.Column(name="X", format="D", array=np.arange(7))
        fits.BinTableHDU.from_columns([col]).writeto(f"{base}.axy", overwrite=True)

        with pytest.raises(PlateSolveError, match="stars_detected=7"):
            await solve_wcs(str(path))

    async def test_timeout_raises_plate_solve_error(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_timeout_solve(monkeypatch)
        path = _make_guide_fits(tmp_path)

        with pytest.raises(PlateSolveError, match="timed out"):
            await solve_wcs(str(path))

    async def test_explicit_hints_used_in_command(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        cmds: list = []
        _install_fake_solve(monkeypatch, captured_cmds=cmds)
        path = _make_guide_fits(tmp_path)

        await solve_wcs(str(path), ra_hint=100.0, dec_hint=-20.0, radius_deg=2.0)

        cmd = cmds[0]
        assert "--ra" in cmd and cmd[cmd.index("--ra") + 1] == "100.0"
        assert "--dec" in cmd and cmd[cmd.index("--dec") + 1] == "-20.0"
        assert "--radius" in cmd and cmd[cmd.index("--radius") + 1] == "2.0"

    async def test_downsample_always_in_command(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        cmds: list = []
        _install_fake_solve(monkeypatch, captured_cmds=cmds)
        path = _make_guide_fits(tmp_path)

        await solve_wcs(str(path))

        cmd = cmds[0]
        assert "--downsample" in cmd and cmd[cmd.index("--downsample") + 1] == "2"

    async def test_falls_back_to_header_radec_when_hints_omitted(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        cmds: list = []
        _install_fake_solve(monkeypatch, captured_cmds=cmds)
        path = _make_guide_fits(tmp_path, ra=42.5, dec=12.0)

        await solve_wcs(str(path))

        cmd = cmds[0]
        assert cmd[cmd.index("--ra") + 1] == "42.5"
        assert cmd[cmd.index("--dec") + 1] == "12.0"

    async def test_no_position_hint_when_header_lacks_radec(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        cmds: list = []
        _install_fake_solve(monkeypatch, captured_cmds=cmds)
        path = _make_guide_fits(tmp_path, with_radec=False)

        await solve_wcs(str(path))

        assert "--ra" not in cmds[0]
        assert "--dec" not in cmds[0]

    async def test_sampling_arcsec_bounds_scale_at_5_percent(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        cmds: list = []
        _install_fake_solve(monkeypatch, captured_cmds=cmds)
        path = _make_guide_fits(tmp_path)

        await solve_wcs(str(path), sampling_arcsec=2.0)

        cmd = cmds[0]
        assert float(cmd[cmd.index("--scale-low") + 1]) == pytest.approx(1.9)
        assert float(cmd[cmd.index("--scale-high") + 1]) == pytest.approx(2.1)

    async def test_record_error_requires_observation_id(self, tmp_path):
        # No stubbing needed -- this must fail before solve-field is ever invoked.
        path = _make_guide_fits(tmp_path)

        with pytest.raises(ValueError, match="observation_id"):
            await solve_wcs(str(path), record_error=True)

    async def test_record_error_missing_radec_header_raises_before_solving(self, tmp_path):
        # Same: no stubbing -- must fail before solve-field runs.
        path = _make_guide_fits(tmp_path, with_radec=False)

        with pytest.raises(ValueError, match="RA/DEC"):
            await solve_wcs(str(path), record_error=True, observation_id="obs1")

    async def test_record_error_computes_and_logs_pointing_error(self, tmp_path, monkeypatch, caplog):
        await db_init()
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path, ra=279.23, dec=38.78, target_name="Vega")

        with caplog.at_level("INFO", logger="spectro.subplugin.plate_solve_helper"):
            await solve_wcs(str(path), record_error=True, observation_id="obs1")

        assert any("Pointing error for Vega" in r.message for r in caplog.records)
        assert path.exists()  # solve_wcs never deletes the file, record_error or not

    async def test_record_error_false_computes_nothing(self, tmp_path, monkeypatch, caplog):
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path)

        with caplog.at_level("INFO", logger="spectro.subplugin.plate_solve_helper"):
            await solve_wcs(str(path))

        assert not any("Pointing error" in r.message for r in caplog.records)

    async def test_record_error_writes_to_pointing_errors_table(self, tmp_path, monkeypatch):
        import aiosqlite
        from rosace.config import DB_PATH

        await db_init()
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path, ra=279.23, dec=38.78, target_name="Vega")

        await solve_wcs(str(path), record_error=True, observation_id="obs-plate-solve-test")

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM pointing_errors WHERE observation_id = ?", ("obs-plate-solve-test",)
            ) as cursor:
                rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["target_name"] == "Vega"
        assert rows[0]["mount_ra_deg"] == pytest.approx(279.23)
        assert rows[0]["mount_dec_deg"] == pytest.approx(38.78)
        assert rows[0]["solved_ra_deg"] == pytest.approx(279.23, abs=1e-6)
        assert rows[0]["solved_dec_deg"] == pytest.approx(38.78, abs=1e-6)
        assert rows[0]["pier_side"] is None

    async def test_record_error_stores_pier_side(self, tmp_path, monkeypatch):
        import aiosqlite
        from rosace.config import DB_PATH

        await db_init()
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path, ra=279.23, dec=38.78, target_name="Vega")

        await solve_wcs(str(path), record_error=True, observation_id="obs-pier-side", pier_side="east")

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT pier_side FROM pointing_errors WHERE observation_id = ?", ("obs-pier-side",)
            ) as cursor:
                row = await cursor.fetchone()
        assert row[0] == "east"

    async def test_record_error_false_does_not_write_to_db(self, tmp_path, monkeypatch):
        import aiosqlite
        from rosace.config import DB_PATH

        await db_init()
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path)

        await solve_wcs(str(path), observation_id="obs-not-recorded")

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM pointing_errors WHERE observation_id = ?", ("obs-not-recorded",)
            ) as cursor:
                row = await cursor.fetchone()
        assert row[0] == 0


class TestSolveResult:

    async def test_returns_a_skycoord(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path, ra=10.0, dec=20.0)
        await solve_wcs(str(path))

        result = solve_result(str(path))

        assert isinstance(result, SkyCoord)

    async def test_default_is_image_center(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path, ra=10.0, dec=20.0)
        await solve_wcs(str(path))

        coord = solve_result(str(path))

        assert coord.ra.degree == pytest.approx(10.0, abs=1e-6)
        assert coord.dec.degree == pytest.approx(20.0, abs=1e-6)

    async def test_explicit_center_pixel_matches_default(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path, ra=10.0, dec=20.0)
        await solve_wcs(str(path))
        center = (NAXIS - 1) / 2

        coord = solve_result(str(path), center, center)

        assert coord.ra.degree == pytest.approx(10.0, abs=1e-6)
        assert coord.dec.degree == pytest.approx(20.0, abs=1e-6)

    async def test_off_center_pixel_differs_from_center(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path, ra=10.0, dec=20.0)
        await solve_wcs(str(path))

        coord = solve_result(str(path), 0, 0)

        assert (coord.ra.degree, coord.dec.degree) != pytest.approx((10.0, 20.0))

    def test_rejects_x_without_y(self, tmp_path):
        # No WCS needed -- validation happens before the header is even read.
        path = _make_guide_fits(tmp_path)
        with pytest.raises(ValueError, match="x and y"):
            solve_result(str(path), x=10)

    async def test_does_not_emit_fitsfixedwarning(self, tmp_path, monkeypatch):
        """astropy.wcs.WCS() emits a FITSFixedWarning when it derives
        MJD-OBS from DATE-OBS -- routine on real-world FITS files, silenced
        by _wcs_from_header() rather than left to leak into CLI/log output."""
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path)
        await solve_wcs(str(path))
        with fits.open(path, mode="update") as hdul:
            hdul[0].header["DATE-OBS"] = "2026-08-09T20:00:00"

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            solve_result(str(path))

        assert not any(issubclass(w.category, FITSFixedWarning) for w in caught)

    def test_result_is_usable_with_spherical_offsets_to(self, tmp_path):
        """Real solve-field output includes an EQUINOX card, which astropy
        interprets as FK5 before converting to ICRS -- the SkyCoord that
        WCS.pixel_to_world().icrs then returns carries a leftover
        `equinox` attribute even after the frame itself becomes ICRS
        (which has no frame attributes at all). SkyCoord.is_equivalent_frame()
        compares *every* frame attribute known anywhere in astropy's
        transform graph, so that leftover equinox makes
        spherical_offsets_to()/spherical_offsets_by() reject this result
        as a "non-matching frame" against a coordinate built fresh from
        plain ra/dec (e.g. a Target's) -- even though both report frame
        'icrs'. Found via a real observation. _install_fake_solve()
        (used by every other test here) doesn't set EQUINOX, so it never
        reproduces this -- this test builds its own solved header."""
        data = np.zeros((NAXIS, NAXIS), dtype=np.float32)
        header = fits.Header()
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRVAL1"] = 10.0
        header["CRVAL2"] = 20.0
        header["CRPIX1"] = (NAXIS + 1) / 2
        header["CRPIX2"] = (NAXIS + 1) / 2
        header["CDELT1"] = -0.001
        header["CDELT2"] = 0.001
        header["EQUINOX"] = 2000.0  # what real solve-field output actually includes
        path = tmp_path / "solved.fits"
        fits.PrimaryHDU(data, header=header).writeto(path, overwrite=True)

        result = solve_result(str(path))

        assert getattr(result, "equinox", None) is None
        other = SkyCoord(ra=10.0, dec=20.0, unit="deg", frame="icrs")
        other.spherical_offsets_to(result)  # must not raise


class TestWorldToPixel:

    async def test_inverse_of_solve_result(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path, ra=10.0, dec=20.0)
        await solve_wcs(str(path))
        coord = solve_result(str(path), 5, 7)

        x, y = world_to_pixel(str(path), coord)

        assert x == pytest.approx(5.0, abs=1e-6)
        assert y == pytest.approx(7.0, abs=1e-6)

    async def test_target_coord_gives_center_pixel(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path, ra=10.0, dec=20.0)
        await solve_wcs(str(path))
        center = (NAXIS - 1) / 2
        coord = SkyCoord(ra=10.0, dec=20.0, unit="deg", frame="icrs")

        x, y = world_to_pixel(str(path), coord)

        assert x == pytest.approx(center, abs=1e-6)
        assert y == pytest.approx(center, abs=1e-6)

    async def test_does_not_emit_fitsfixedwarning(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)
        path = _make_guide_fits(tmp_path)
        await solve_wcs(str(path))
        with fits.open(path, mode="update") as hdul:
            hdul[0].header["DATE-OBS"] = "2026-08-09T20:00:00"
        coord = solve_result(str(path))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            world_to_pixel(str(path), coord)

        assert not any(issubclass(w.category, FITSFixedWarning) for w in caught)


class TestPixelScaleArcsec:

    async def test_reports_configured_scale(self, tmp_path, monkeypatch):
        _stub_which(monkeypatch)
        _install_fake_solve(monkeypatch)  # writes CDELT1=-0.001, CDELT2=0.001 deg/px
        path = _make_guide_fits(tmp_path)
        await solve_wcs(str(path))

        scale = pixel_scale_arcsec(str(path))

        assert scale == pytest.approx(3.6, abs=1e-6)  # 0.001 deg/px -> 3.6 arcsec/px
