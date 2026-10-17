"""
REDUCTION sub-plugin — Dispersion law
Computes the pixel-to-wavelength law from the stacked lamp spectrum, or reuses the one already computed for that lamp series.

Input: the stacked lamp spectrum (shared key stack_calib_spectrum) and the
reference lines of the lamp (subplugin_params.dispersion_law.reference_lines,
a text file in <base_data_dir>/reference_lines/: one air wavelength per line,
"#" comments, an optional label after the value). Wavelengths increase from
left to right on the sensor.

1. Line detection: the lamp's continuous background (straight lines between
   the local minima of the spectrum smoothed over 3 pixels) is subtracted,
   the noise estimated by the MAD (median absolute deviation x 1.4826), and
   every peak above DETECTION_SIGMA times the noise kept (the MAX_LINES
   strongest). Each line's position comes from a Gaussian + constant fit on
   the raw spectrum; a line whose fit fails, whose width is implausible or
   whose centre moves away from its peak (a blend) is dropped.
2. Matching, the plate-solve way: four detected lines and four reference
   lines, in the same order, give a provisional law (the cubic through the
   four pairs). The reference quadruplets always include the two extreme
   lines of the list, which spread the law over the whole domain and keep
   extrapolation small. Only the quadruplets whose two spacing ratios
   (x2 - x1) / (x4 - x1) and (x3 - x1) / (x4 - x1) -- independent of the
   dispersion and of the offset -- agree within RATIO_TOLERANCE are tried.
   An optional approximate dispersion drops at once the hypotheses whose
   scale is too far from it.
3. Score: each hypothesis is applied to the detected lines; for every
   reference line, d = distance to the nearest detected line; the smallest
   sum of every d wins (Demetra's criterion).
4. Final law, from the best hypothesis: each reference line is paired with
   the nearest detected line within the tolerance, a polynomial of the
   chosen degree is fitted by least squares, points beyond CLIP_SIGMA are
   rejected, and the pairing is redone until stable. The law must keep both
   imposed extreme lines; when it does not (an extreme line missing from the
   lamp spectrum), the neighbouring line is imposed instead, with a warning.
   No law with min_lines lines and its imposed lines is an error, which
   stops the reduction.

5. Resolving power, R = lambda / FWHM, the usual convention (the smallest
   separable wavelength difference taken as the width at half maximum of an
   unresolved lamp line). Measured on the lines kept for the law: the
   Gaussian sigma of each (pixels, from step 1) is fitted by a straight line
   against the pixel position -- the width grows by about 12 % from blue to
   red on the Alpy 600 -- lines whose residual exceeds R_CLIP_SIGMA robust
   sigmas (1.4826 x median absolute deviation) being rejected until stable
   (a blend reads wider); never below R_MIN_TOLERANCE_FRACTION of the median
   width, since lines of nearly identical widths would make the robust
   sigma tiny and reject sound lines for differences of no consequence. That line is read at the pixel of the reference
   wavelength (subplugin_params.dispersion_law.resolving_power_wavelength,
   6563 A / 656.3 nm by default: H-alpha, BeSS's example), converted to a
   width in wavelength with the law's derivative there: FWHM = 2.355 x sigma
   x local dispersion, R = wavelength / FWHM. No correction is made for the
   pixel's own width (it is part of what the delivered spectrum went
   through). The measurement is made before resampling: lambda_calibration's
   resampling widens the lines a little more, so the delivered spectrum is
   about 2.7 % less resolved than R says (measured on 2026-10-16). Fewer than
   R_MIN_LINES lines is only a warning: the law is written without R.

Written to the session directory, named after the lamp series:
"<lamp series>_dispersion_law.yaml" (coefficients A0..An and, when
measured, resolving_power and resolving_power_wavelength; a comment header
saying what they are) and "<lamp series>_reference_lines.txt" (the copy of
the reference list used). When the law file already exists it is reused,
not recomputed. Shared key dispersion_law_file; casier
reduction_data["dispersion_law"] (degree, unit, coefficients, RMS, lines
used, coverage = wavelengths of the first and last line used -- outside,
the law is extrapolated -- and the resolving power with its wavelength).
"""

from __future__ import annotations

DISPLAY_NAME = "Disp. law"

import dataclasses
import itertools
import logging
import shutil
from pathlib import Path

import numpy as np
import yaml
from astropy.io import fits
from numpy.polynomial import Polynomial
from scipy.signal import find_peaks, peak_widths

from ....config import BASE_DATA_DIR, SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import find_shared_input, read_law, series_root
from ..spectrum_flux_helpers import _fit_gaussian

log = logging.getLogger("spectro.subplugin.dispersion_law")

DETECTION_SIGMA = 7.0
MAX_LINES = 80
LINE_SIGMA_RANGE_PX = (0.5, 6.0)
MAX_CENTRE_SHIFT_PX = 2.0
FIT_HALF_WIDTH_SIGMA = 3.0
RATIO_TOLERANCE = 0.03
APPROX_DISPERSION_TOLERANCE = 0.20
MONOTONY_PROBES = 9                # points across the sensor where the law must increase
CLIP_SIGMA = 3.0
MAX_ITERATIONS = 20
CHUNK = 5000                       # hypotheses scored at once (memory bound)
FWHM_PER_SIGMA = 2.0 * np.sqrt(2.0 * np.log(2.0))   # 2.355, for a Gaussian
R_CLIP_SIGMA = 3.0
R_MIN_TOLERANCE_FRACTION = 0.05    # a width within 5 % of the line is never rejected
R_MIN_LINES = 3

DEFAULT_TOLERANCE = {"A": 1.0, "nm": 0.1}
DEFAULT_R_WAVELENGTH = {"A": 6563.0, "nm": 656.3}
REFERENCE_LINES_DIR = "reference_lines"


@dataclasses.dataclass
class DetectedLines:
    x: np.ndarray            # centres, pixels
    x_error: np.ndarray      # centre errors from the fit, pixels
    sigma: np.ndarray        # Gaussian widths from the fit, pixels
    amplitude: np.ndarray
    background: np.ndarray   # the continuous background subtracted for detection
    noise: float


@dataclasses.dataclass
class DispersionSolution:
    coefficients: list[float]        # A0, A1, ...
    x: np.ndarray                    # pixels of the lines used
    x_error: np.ndarray
    sigma: np.ndarray                # their Gaussian widths, pixels
    wavelength: np.ndarray           # their reference wavelengths
    residual: np.ndarray             # wavelength - law(x)
    rms: float                       # in the wavelength unit
    rms_px: float
    coverage: tuple[float, float]
    anchors: tuple[float, float]     # the two imposed reference lines
    hypotheses: int                  # provisional laws scored

    def law(self, x):
        return Polynomial(self.coefficients)(x)


def read_reference_lines(path: Path) -> np.ndarray:
    """Sorted wavelengths of a reference line file (see module docstring)."""
    values = []
    for number, line in enumerate(Path(path).read_text().splitlines(), start=1):
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        try:
            values.append(float(text.split()[0]))
        except ValueError:
            raise RuntimeError(f"Reference line file {path}, line {number}: not a wavelength: {line!r}") from None
    if len(values) < 4:
        raise RuntimeError(f"Reference line file {path}: {len(values)} line(s), at least 4 needed")
    return np.sort(np.array(values))


def local_minima_background(spectrum: np.ndarray) -> np.ndarray:
    """Straight lines between the local minima of the spectrum smoothed over
    3 pixels (both ends included)."""
    smooth = np.convolve(spectrum, np.ones(3) / 3, mode="same")
    smooth[0], smooth[-1] = spectrum[0], spectrum[-1]
    inner = np.nonzero((smooth[1:-1] <= smooth[:-2]) & (smooth[1:-1] <= smooth[2:]))[0] + 1
    minima = np.concatenate(([0], inner, [spectrum.size - 1]))
    return np.interp(np.arange(spectrum.size), minima, smooth[minima])


def detect_lines(spectrum: np.ndarray) -> DetectedLines:
    """The lamp's emission lines, positioned by Gaussian fits (see module docstring)."""
    spectrum = np.asarray(spectrum, dtype=np.float64)
    background = local_minima_background(spectrum)
    signal = spectrum - background
    noise = 1.4826 * float(np.median(np.abs(signal - np.median(signal))))
    if noise <= 0:
        noise = float(np.std(signal)) or 1.0
    peaks, properties = find_peaks(signal, height=DETECTION_SIGMA * noise)
    strongest = np.argsort(properties["peak_heights"])[::-1][:MAX_LINES]
    peaks = np.sort(peaks[strongest])
    widths = peak_widths(signal, peaks, rel_height=0.5)[0] if peaks.size else np.array([])

    x, x_error, sigmas, amplitude = [], [], [], []
    for peak, fwhm in zip(peaks, widths):
        sigma_guess = max(fwhm / 2.355, LINE_SIGMA_RANGE_PX[0])
        half = max(3, int(np.ceil(FIT_HALF_WIDTH_SIGMA * sigma_guess)))
        t = np.arange(max(0, peak - half), min(spectrum.size, peak + half + 1))
        fit = _fit_gaussian(t.astype(np.float64), spectrum[t], float(peak), sigma_guess)
        if fit is None:
            continue
        (amp, centre, sigma, _), errors = fit
        if amp <= 0 or not LINE_SIGMA_RANGE_PX[0] <= sigma <= LINE_SIGMA_RANGE_PX[1]:
            continue
        if abs(centre - peak) > MAX_CENTRE_SHIFT_PX or not np.isfinite(errors[1]):
            continue
        x.append(centre)
        x_error.append(errors[1])
        sigmas.append(sigma)
        amplitude.append(amp)
    order = np.argsort(x)
    return DetectedLines(
        np.array(x)[order], np.array(x_error)[order], np.array(sigmas)[order], np.array(amplitude)[order],
        background, noise,
    )


def _spacing_ratios(values: np.ndarray) -> np.ndarray:
    """(n, 2) ratios (v2 - v1) / (v4 - v1) and (v3 - v1) / (v4 - v1) of (n, 4) quadruplets."""
    span = values[:, 3] - values[:, 0]
    return np.stack([(values[:, 1] - values[:, 0]) / span, (values[:, 2] - values[:, 0]) / span], axis=1)


def _cubics(xs: np.ndarray, ls: np.ndarray) -> np.ndarray:
    """(n, 4) coefficients A0..A3 of the cubic through four points per row."""
    vandermonde = np.stack([xs ** k for k in range(4)], axis=2)
    return np.linalg.solve(vandermonde, ls[..., None])[..., 0]


def best_hypothesis(
    x: np.ndarray,
    refs: np.ndarray,
    width: int,
    anchors: tuple[int, int],
    approx_dispersion: float | None = None,
) -> tuple[np.ndarray | None, int]:
    """Best provisional law (cubic coefficients A0..A3) whose reference
    quadruplet starts and ends on refs[anchors[0]] and refs[anchors[1]], and
    the number of hypotheses scored."""
    if x.size < 4:
        return None, 0
    det = np.array(list(itertools.combinations(range(x.size), 4)), dtype=np.int32)
    xq = x[det]
    det_ratios = _spacing_ratios(xq)
    first, last = anchors
    middles = itertools.combinations(range(first + 1, last), 2)
    ref = np.array([[first, i, j, last] for i, j in middles], dtype=np.int32).reshape(-1, 4)
    probes = np.linspace(0.0, width - 1.0, MONOTONY_PROBES)

    best, best_score, tested = None, np.inf, 0
    for quad, ratios in zip(refs[ref], _spacing_ratios(refs[ref])):
        selected = np.nonzero(np.all(np.abs(det_ratios - ratios) <= RATIO_TOLERANCE, axis=1))[0]
        if approx_dispersion and selected.size:
            mean = (quad[3] - quad[0]) / (xq[selected, 3] - xq[selected, 0])
            selected = selected[np.abs(mean / approx_dispersion - 1.0) <= APPROX_DISPERSION_TOLERANCE]
        for start in range(0, selected.size, CHUNK):
            block = selected[start:start + CHUNK]
            c = _cubics(xq[block], np.broadcast_to(quad, (block.size, 4)).copy())
            # Wavelength must increase across the whole sensor
            slopes = c[:, 1, None] + 2 * c[:, 2, None] * probes + 3 * c[:, 3, None] * probes ** 2
            c = c[np.all(slopes > 0, axis=1)]
            if not c.size:
                continue
            tested += len(c)
            wavelengths = c[:, 0, None] + c[:, 1, None] * x + c[:, 2, None] * x ** 2 + c[:, 3, None] * x ** 3
            # Distance of every reference line to the nearest detected line, summed
            scores = np.abs(wavelengths[:, None, :] - refs[None, :, None]).min(axis=2).sum(axis=1)
            i = int(np.argmin(scores))
            if scores[i] < best_score:
                best_score, best = scores[i], c[i]
    return best, tested


def _pair_lines(law: Polynomial, x: np.ndarray, refs: np.ndarray, tolerance: float) -> tuple[np.ndarray, np.ndarray]:
    """(detected index, reference index) of every reference line whose
    nearest detected line, through law, lies within the tolerance; a
    detected line goes to the closest reference only."""
    wavelengths = law(x)
    distance = np.abs(wavelengths[None, :] - refs[:, None])
    nearest = distance.argmin(axis=1)
    d = distance[np.arange(refs.size), nearest]
    pairs = {}
    for r in np.argsort(d):
        if d[r] <= tolerance and nearest[r] not in pairs:
            pairs[nearest[r]] = r
    det = np.array(sorted(pairs), dtype=int)
    return det, np.array([pairs[j] for j in det], dtype=int)


def _fit_law(lines: DetectedLines, refs: np.ndarray, start: np.ndarray, degree: int, tolerance: float, min_lines: int):
    """Iterative pairing + least-squares fit + clipping from a provisional
    law; returns (law, detected index, reference index). Raises RuntimeError
    below min_lines lines."""
    law = Polynomial(start)
    previous = None
    floor = max(min_lines, degree + 1)
    for _ in range(MAX_ITERATIONS):
        det, ref = _pair_lines(law, lines.x, refs, tolerance)
        if det.size < floor:
            raise RuntimeError(
                f"DISPERSION_LAW: {det.size} reference line(s) matched within {tolerance:g}, at least {floor} needed"
            )
        while True:
            law = Polynomial.fit(lines.x[det], refs[ref], degree).convert()
            residual = refs[ref] - law(lines.x[det])
            worst = int(np.argmax(np.abs(residual)))
            if det.size <= floor or abs(residual[worst]) <= CLIP_SIGMA * residual.std():
                break
            det, ref = np.delete(det, worst), np.delete(ref, worst)
        key = (tuple(det), tuple(ref))
        if key == previous:
            break
        previous = key
    return law, det, ref


def _anchor_pairs(count: int) -> list[tuple[int, int]]:
    """Imposed (first, last) reference indices, in order of preference: the
    two extremes, then one of them replaced by its neighbour, then both."""
    pairs = [(0, count - 1), (1, count - 1), (0, count - 2), (1, count - 2)]
    return [p for p in pairs if p[1] - p[0] >= 3]


def solve_dispersion(
    spectrum: np.ndarray,
    refs: np.ndarray,
    *,
    degree: int,
    tolerance: float,
    approx_dispersion: float | None = None,
    min_lines: int = 7,
    lines: DetectedLines | None = None,
) -> DispersionSolution:
    """The dispersion law of a lamp spectrum (see module docstring). Raises
    RuntimeError when no law keeps min_lines lines and its imposed lines."""
    lines = lines or detect_lines(spectrum)
    if lines.x.size < 4:
        raise RuntimeError(f"DISPERSION_LAW: only {lines.x.size} lamp line(s) detected, at least 4 needed")
    failures = []
    for anchors in _anchor_pairs(refs.size):
        start, tested = best_hypothesis(lines.x, refs, len(spectrum), anchors, approx_dispersion)
        if start is None:
            failures.append(f"{refs[anchors[0]]:g}/{refs[anchors[1]]:g}: no line matching")
            continue
        try:
            law, det, ref = _fit_law(lines, refs, start, degree, tolerance, min_lines)
        except RuntimeError as error:
            failures.append(f"{refs[anchors[0]]:g}/{refs[anchors[1]]:g}: {error}")
            continue
        if not {anchors[0], anchors[1]} <= set(ref.tolist()):
            failures.append(f"{refs[anchors[0]]:g}/{refs[anchors[1]]:g}: an imposed line was not kept")
            continue
        if anchors != (0, refs.size - 1):
            log.warning(
                f"[DISPERSION_LAW] Extreme reference line(s) not kept, law imposed on "
                f"{refs[anchors[0]]:g} and {refs[anchors[1]]:g} instead ({'; '.join(failures)})"
            )
        coefficients = [float(c) for c in np.pad(law.coef, (0, degree + 1 - law.coef.size))]
        residual = refs[ref] - law(lines.x[det])
        local_dispersion = law.deriv()(lines.x[det])
        return DispersionSolution(
            coefficients=coefficients,
            x=lines.x[det],
            x_error=lines.x_error[det],
            sigma=lines.sigma[det],
            wavelength=refs[ref],
            residual=residual,
            rms=float(np.sqrt(np.mean(residual ** 2))),
            rms_px=float(np.sqrt(np.mean((residual / local_dispersion) ** 2))),
            coverage=(float(refs[ref].min()), float(refs[ref].max())),
            anchors=(float(refs[anchors[0]]), float(refs[anchors[1]])),
            hypotheses=tested,
        )
    raise RuntimeError(
        f"DISPERSION_LAW: no law found ({lines.x.size} lines detected, {refs.size} references): {'; '.join(failures)}"
    )


@dataclasses.dataclass
class ResolvingPower:
    value: float             # R, rounded to the unit
    wavelength: float        # where it is given
    fwhm_px: float           # line width there, pixels
    lines_used: int
    rejected: list[float]    # reference wavelengths of the lines rejected (too wide or too narrow)


def resolving_power(solution: DispersionSolution, wavelength: float, width: int) -> ResolvingPower | None:
    """R = wavelength / FWHM at the given wavelength, from the widths of the
    law's lines (see module docstring, step 5); None below R_MIN_LINES lines.
    width is the spectrum's number of pixels (where the law is inverted)."""
    keep = np.ones(solution.x.size, dtype=bool)
    while True:
        if keep.sum() < R_MIN_LINES:
            return None
        line = Polynomial.fit(solution.x[keep], solution.sigma[keep], 1).convert()
        residual = solution.sigma - line(solution.x)
        spread = 1.4826 * float(np.median(np.abs(residual[keep])))
        tolerance = max(R_CLIP_SIGMA * spread, R_MIN_TOLERANCE_FRACTION * float(np.median(solution.sigma[keep])))
        new_keep = np.abs(residual) <= tolerance
        if np.array_equal(new_keep, keep):
            break
        keep = new_keep
    pixels = np.arange(width, dtype=np.float64)
    law = Polynomial(solution.coefficients)
    x = float(np.interp(wavelength, law(pixels), pixels))
    fwhm_px = FWHM_PER_SIGMA * float(line(x))
    fwhm = fwhm_px * float(law.deriv()(x))
    if fwhm <= 0:
        return None
    return ResolvingPower(
        value=float(round(wavelength / fwhm)),
        wavelength=float(wavelength),
        fwhm_px=fwhm_px,
        lines_used=int(keep.sum()),
        rejected=[float(w) for w in solution.wavelength[~keep]],
    )


def write_law(path: Path, solution: DispersionSolution, lamp_series: str, unit: str,
              power: ResolvingPower | None = None) -> None:
    """The law as a small YAML file: a comment header, the unit, A0..An,
    and the resolving power when measured."""
    names = " + ".join(["A0", "A1*x"] + [f"A{k}*x^{k}" for k in range(2, len(solution.coefficients))])
    header = [
        f"# Dispersion law of lamp series {lamp_series}, computed by Rosace.",
        f"# lambda(x) = {names}, x = pixel (0 = first column), lambda in air, in {unit}.",
        f"# {solution.x.size} reference lines, RMS {solution.rms:.3f} {unit} ({solution.rms_px:.3f} px); "
        f"valid between {solution.coverage[0]:g} and {solution.coverage[1]:g} {unit} (extrapolated outside).",
    ]
    body = {"unit": unit} | {f"A{k}": c for k, c in enumerate(solution.coefficients)}
    if power is not None:
        header.append(
            f"# Resolving power R = {power.value:.0f} at {power.wavelength:g} {unit} (FWHM of {power.lines_used} "
            f"lamp lines, linear fit of sigma vs pixel, before resampling)."
        )
        body |= {"resolving_power": power.value, "resolving_power_wavelength": power.wavelength}
    path.write_text("\n".join(header) + "\n" + yaml.safe_dump(body, sort_keys=False))


def _reference_path(name: str) -> Path:
    path = Path(name).expanduser()
    return path if path.is_absolute() else BASE_DATA_DIR / REFERENCE_LINES_DIR / path


class DispersionLawPlugin(SubPlugin):
    """Computes the pixel-to-wavelength law from the stacked lamp spectrum, or reuses the one already computed for that lamp series."""

    async def execute(self, obs: Observation) -> Observation:
        params = SUBPLUGIN_PARAMS.get("dispersion_law", {})
        session_dir = Path(obs.obs_dir or "")
        lamp_series = series_root(find_shared_input(obs, "calib_images"))
        law_name = f"{lamp_series}_dispersion_law.yaml"
        law_path = session_dir / law_name

        if law_path.exists():
            law = read_law(law_path)
            obs.reduction_data["dispersion_law_file"] = law_name
            obs.reduction_data["dispersion_law"] = {
                "degree": len(law["coefficients"]) - 1,
                "unit": law["unit"],
                "coefficients": law["coefficients"],
                "reused": True,
            }
            if law["resolving_power"] is not None:
                obs.reduction_data["dispersion_law"] |= {
                    "resolving_power": law["resolving_power"],
                    "resolving_power_wavelength": law["resolving_power_wavelength"],
                }
            log.info(f"[DISPERSION_LAW] Reused {law_name}")
            return obs

        if not params.get("reference_lines"):
            raise RuntimeError("DISPERSION_LAW: subplugin_params.dispersion_law.reference_lines is not set")
        unit = params.get("unit", "A")
        if unit not in DEFAULT_TOLERANCE:
            raise RuntimeError(f"DISPERSION_LAW: unit {unit!r} unknown, expected A or nm")
        reference_file = _reference_path(params["reference_lines"])
        refs = read_reference_lines(reference_file)
        spectrum = fits.getdata(session_dir / find_shared_input(obs, "stack_calib_spectrum")).astype(np.float64)
        solution = solve_dispersion(
            spectrum,
            refs,
            degree=int(params.get("degree", 3)),
            tolerance=float(params.get("tolerance", DEFAULT_TOLERANCE[unit])),
            approx_dispersion=params.get("approx_dispersion"),
            min_lines=int(params.get("min_lines", 7)),
        )

        r_wavelength = float(params.get("resolving_power_wavelength", DEFAULT_R_WAVELENGTH[unit]))
        power = resolving_power(solution, r_wavelength, spectrum.size)
        if power is None:
            log.warning(
                f"[DISPERSION_LAW] Resolving power not measured: fewer than {R_MIN_LINES} lines of consistent width"
            )
        write_law(law_path, solution, lamp_series, unit, power)
        shutil.copyfile(reference_file, session_dir / f"{lamp_series}_reference_lines.txt")
        obs.reduction_data["dispersion_law_file"] = law_name
        obs.reduction_data["dispersion_law"] = {
            "degree": len(solution.coefficients) - 1,
            "unit": unit,
            "coefficients": solution.coefficients,
            "rms": round(solution.rms, 4),
            "rms_px": round(solution.rms_px, 4),
            "lines_used": int(solution.x.size),
            "coverage": [solution.coverage[0], solution.coverage[1]],
        }
        r_text = ""
        if power is not None:
            obs.reduction_data["dispersion_law"] |= {
                "resolving_power": power.value,
                "resolving_power_wavelength": power.wavelength,
                "fwhm_px": round(power.fwhm_px, 3),
            }
            if power.rejected:
                obs.reduction_data["dispersion_law"]["lines_rejected_for_r"] = power.rejected
            r_text = f", R {power.value:.0f} at {power.wavelength:g} {unit}"
        log.info(
            f"[DISPERSION_LAW] {solution.x.size}/{refs.size} lines, RMS {solution.rms:.3f} {unit} "
            f"({solution.rms_px:.3f} px), valid {solution.coverage[0]:g}-{solution.coverage[1]:g} {unit}, "
            f"extrapolated outside{r_text}"
        )
        return obs
