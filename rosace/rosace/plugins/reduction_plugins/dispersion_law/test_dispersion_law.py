"""
Tests for the DISPERSION_LAW sub-plugin (DispersionLawPlugin), on a synthetic lamp spectrum.
Run: pytest plugins/reduction_plugins/dispersion_law/test_dispersion_law.py -v
"""

import logging
from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits
from numpy.polynomial import Polynomial

from rosace.models import Observation
from rosace.plugins.reduction_plugins.dispersion_law import dispersion_law as module
from rosace.plugins.reduction_plugins.reduction_helpers import read_law
from rosace.plugins.reduction_plugins.dispersion_law.dispersion_law import (
    DispersionLawPlugin,
    detect_lines,
    local_minima_background,
    read_reference_lines,
    solve_dispersion,
)

WIDTH = 1304
TRUE_LAW = Polynomial([3054.8, 3.4976, 1.94e-4, -1.64e-7])     # same shape as the 72ED's Alpy 600
REFS = np.array([4158.59, 4200.67, 4426.00, 4545.05, 4657.90, 4764.87,
                 5852.48, 6099.16, 6677.28, 6965.43, 7067.22, 7147.04])
SPURIOUS = [4300.0, 5000.0, 6300.0, 7400.0]                     # lamp lines absent from the reference list
SIGMA_PX = 2.0
SUB = "Vega_0e37017f"


def _pixel_of(wavelength, law=TRUE_LAW):
    roots = (law - wavelength).roots()
    return float(next(r.real for r in roots if abs(r.imag) < 1e-9 and -50 < r.real < WIDTH + 50))


def _lamp(wavelengths=REFS, shifts=None, seed=0, law=TRUE_LAW):
    """A lamp spectrum: a slow continuum, one Gaussian per line, noise."""
    rng = np.random.default_rng(seed)
    x = np.arange(WIDTH)
    spectrum = 2000.0 + 0.5 * x + rng.normal(0, 30, WIDTH)
    shifts = shifts or {}
    for k, w in enumerate(list(wavelengths) + SPURIOUS):
        centre = _pixel_of(w + shifts.get(w, 0.0), law)
        spectrum += (20000 + 3000 * (k % 5)) * np.exp(-0.5 * ((x - centre) / SIGMA_PX) ** 2)
    return spectrum


def _write_refs(path, values=REFS, unit_scale=1.0):
    path.write_text("# test list\n" + "\n".join(f"{v * unit_scale:.4f} label" for v in values) + "\n")
    return path


class TestHelpers:

    def test_read_reference_lines(self, tmp_path):
        refs = read_reference_lines(_write_refs(tmp_path / "refs.txt", REFS[::-1]))
        assert np.allclose(refs, REFS)

    def test_read_reference_lines_rejects_bad_line(self, tmp_path):
        path = tmp_path / "refs.txt"
        path.write_text("4158.59\nabc\n4200.67\n4426\n4545\n")
        with pytest.raises(RuntimeError, match="line 2"):
            read_reference_lines(path)

    def test_background_follows_the_continuum_under_lines(self):
        spectrum = _lamp()
        background = local_minima_background(spectrum)
        x = np.arange(WIDTH)
        assert np.median(np.abs(background - (2000.0 + 0.5 * x))) < 60

    def test_line_positions_better_than_a_twentieth_of_a_pixel(self):
        lines = detect_lines(_lamp())
        for w in REFS:
            centre = _pixel_of(w)
            assert np.min(np.abs(lines.x - centre)) < 0.05


class TestSolveDispersion:

    def test_recovers_the_law_with_every_line(self):
        solution = solve_dispersion(_lamp(), REFS, degree=3, tolerance=1.0)
        assert solution.x.size == REFS.size
        assert solution.rms < 0.2
        x = np.linspace(0, WIDTH - 1, 50)
        assert np.max(np.abs(solution.law(x) - TRUE_LAW(x))) < 0.5
        assert solution.coverage == (REFS[0], REFS[-1])
        assert solution.anchors == (REFS[0], REFS[-1])

    def test_with_approximate_dispersion(self):
        free = solve_dispersion(_lamp(), REFS, degree=3, tolerance=1.0)
        hinted = solve_dispersion(_lamp(), REFS, degree=3, tolerance=1.0, approx_dispersion=3.6)
        assert np.allclose(free.coefficients, hinted.coefficients)
        assert hinted.hypotheses < free.hypotheses

    def test_displaced_lines_rejected_extremes_kept(self):
        # Four lines seen ~2 A away from their reference (blends at low resolution)
        shifts = {4200.67: -2.0, 4426.00: +2.4, 6099.16: -2.0, 6677.28: +2.0}
        solution = solve_dispersion(_lamp(shifts=shifts), REFS, degree=3, tolerance=1.0)
        assert set(np.round(solution.wavelength, 2)) == set(REFS) - set(shifts)
        assert solution.coverage == (REFS[0], REFS[-1])

    def test_missing_middle_line_tolerated(self):
        lamp = _lamp(wavelengths=[w for w in REFS if w != 5852.48])
        solution = solve_dispersion(lamp, REFS, degree=3, tolerance=1.0)
        assert solution.x.size == REFS.size - 1
        assert 5852.48 not in solution.wavelength

    def test_missing_extreme_line_falls_back_to_its_neighbour(self, caplog):
        lamp = _lamp(wavelengths=REFS[1:])
        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.dispersion_law"):
            solution = solve_dispersion(lamp, REFS, degree=3, tolerance=1.0)
        assert solution.anchors == (REFS[1], REFS[-1])
        assert solution.coverage == (REFS[1], REFS[-1])
        assert "imposed on 4200.67" in caplog.text

    def test_too_few_lines_is_an_error(self):
        lamp = _lamp(wavelengths=REFS[[0, 3, 6, 11]])
        with pytest.raises(RuntimeError, match="no law found"):
            solve_dispersion(lamp, REFS, degree=3, tolerance=1.0, min_lines=7)

    def test_degree_two_on_a_parabolic_law(self):
        parabola = Polynomial([3060.0, 3.30, 3.0e-4])
        solution = solve_dispersion(_lamp(law=parabola), REFS, degree=2, tolerance=1.0)
        assert len(solution.coefficients) == 3
        assert np.allclose(solution.coefficients, parabola.coef, rtol=1e-3)

    def test_degree_two_refused_on_a_cubic_law(self):
        # A parabola cannot follow the cubic law within the tolerance while
        # keeping both extreme lines: refused rather than extrapolated
        with pytest.raises(RuntimeError, match="no law found"):
            solve_dispersion(_lamp(), REFS, degree=2, tolerance=1.0)


def _setup(tmp_path, monkeypatch, params, refs_file):
    session = tmp_path / "session"
    (session / SUB).mkdir(parents=True)
    fits.writeto(session / SUB / "CALIB_3x3s_20260925_194111_stack.fits", _lamp().astype(np.float32))
    obs = Observation(id="0e37017f", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(session))
    obs.sequencer_data["calib_images"] = [f"CALIB_3x3s_20260925_194111-{i}.fits" for i in (1, 2, 3)]
    obs.reduction_data["stack_calib_spectrum"] = f"{SUB}/CALIB_3x3s_20260925_194111_stack.fits"
    monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {"dispersion_law": {"reference_lines": str(refs_file)} | params})
    return obs, session


class TestDispersionLawPlugin:

    async def test_writes_law_and_reference_copy(self, tmp_path, monkeypatch):
        refs_file = _write_refs(tmp_path / "Alpy600_ArNe.txt")
        obs, session = _setup(tmp_path, monkeypatch, {}, refs_file)

        result = await DispersionLawPlugin().execute(obs)

        name = "CALIB_3x3s_20260925_194111_dispersion_law.yaml"
        assert result.reduction_data["dispersion_law_file"] == name
        text = (session / name).read_text()
        assert text.startswith("# Dispersion law of lamp series CALIB_3x3s_20260925_194111")
        law = read_law(session / name)
        assert law["unit"] == "A" and len(law["coefficients"]) == 4
        assert (session / "CALIB_3x3s_20260925_194111_reference_lines.txt").read_text() == refs_file.read_text()
        casier = result.reduction_data["dispersion_law"]
        assert casier["lines_used"] == 12 and casier["coverage"] == [REFS[0], REFS[-1]]

    async def test_reference_file_in_the_data_directory(self, tmp_path, monkeypatch):
        (tmp_path / "reference_lines").mkdir()
        _write_refs(tmp_path / "reference_lines" / "Alpy600_ArNe.txt")
        obs, session = _setup(tmp_path, monkeypatch, {}, "Alpy600_ArNe.txt")
        monkeypatch.setattr(module, "BASE_DATA_DIR", tmp_path)

        result = await DispersionLawPlugin().execute(obs)

        assert result.reduction_data["dispersion_law"]["lines_used"] == 12

    async def test_existing_law_is_reused(self, tmp_path, monkeypatch):
        obs, session = _setup(tmp_path, monkeypatch, {}, tmp_path / "missing.txt")
        (session / "CALIB_3x3s_20260925_194111_dispersion_law.yaml").write_text(
            "# a law\nunit: A\nA0: 3000.0\nA1: 3.5\nA2: 0.0002\nA3: -1.0e-07\n")

        result = await DispersionLawPlugin().execute(obs)

        assert result.reduction_data["dispersion_law"]["reused"] is True
        assert result.reduction_data["dispersion_law"]["coefficients"] == [3000.0, 3.5, 0.0002, -1.0e-07]

    async def test_nanometres(self, tmp_path, monkeypatch):
        refs_file = _write_refs(tmp_path / "refs_nm.txt", unit_scale=0.1)
        obs, session = _setup(tmp_path, monkeypatch, {"unit": "nm"}, refs_file)

        result = await DispersionLawPlugin().execute(obs)

        casier = result.reduction_data["dispersion_law"]
        assert casier["unit"] == "nm"
        assert casier["coefficients"][0] == pytest.approx(305.48, abs=0.1)

    async def test_missing_reference_setting_is_an_error(self, tmp_path, monkeypatch):
        obs, _ = _setup(tmp_path, monkeypatch, {}, tmp_path / "x.txt")
        monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {})

        with pytest.raises(RuntimeError, match="reference_lines"):
            await DispersionLawPlugin().execute(obs)
