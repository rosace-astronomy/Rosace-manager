"""
REDUCTION sub-plugin — Signal-to-noise ratio
Measures the signal-to-noise ratio of the stacked target spectrum by the DER_SNR method, in one or more wavelength zones.

Method: DER_SNR (Stoehr, Seaman, White et al. 2008, ADASS XVII, ASP Conf.
Ser. 394, 505), a measurement from the spectrum alone, the same for any
spectrum: signal = median of the flux; noise = 1.482602 / sqrt(6) x median of
|2 f(i) - f(i-2) - f(i+2)|; SNR = signal / noise. Zero points (dead columns)
are left out. It skips adjacent pixels on purpose (their noise is often
correlated), and assumes the spectrum varies slowly over two pixels.

Measured on the stacked spectrum in pixels (stack_target_spectrum), before
lambda_calibration's resampling, which correlates neighbouring pixels: on
the final spectrum DER_SNR reads about 8 % higher over 3750-7500 A and 20 %
higher over 6000-7000 A (73 spectra, 2026-10-17). Each zone is converted to
pixels with the dispersion law (dispersion_law_file).

Known limit, measured on HD 60848 (14 x 180 s, 2026-10-16): at high SNR,
DER_SNR takes the real fine structure of the spectrum (lines and telluric
bands, only about 3 pixels wide on the Alpy 600) for noise, and plateaus --
78 for the 14 frames summed over 3750-7500 A where the noise, measured
otherwise, gives about 130. Low and moderate values are reliable; high ones
are underestimated. A wide zone gives a typical value, dominated by its
brightest part (the SNR varies several-fold along a spectrum).

Zones: subplugin_params.snr.zones, a list of [wavelength_min,
wavelength_max] in the law's unit, as many as wanted, all of equal
importance. Nothing here is specific to one scientific program.

Never blocking: a missing input, no zones configured, or a zone outside the
spectrum or with fewer than MIN_POINTS points is a warning (that zone, or
the whole measurement, left out). Output: the shared key spectrum_snr, a
list of {wavelength_min, wavelength_max, snr} (written into the final FITS
by write_spectrum); casier reduction_data["snr"] = {"method": "DER_SNR"}.
"""

from __future__ import annotations

DISPLAY_NAME = "SNR"

import logging
from pathlib import Path

import numpy as np
from astropy.io import fits
from numpy.polynomial import Polynomial

from ....config import SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import find_shared_input, read_law

log = logging.getLogger("spectro.subplugin.snr")

MIN_POINTS = 10
DER_SNR_NOISE_FACTOR = 1.482602 / np.sqrt(6.0)


def der_snr(flux: np.ndarray) -> float | None:
    """DER_SNR of a 1D spectrum (see module docstring); None with fewer than
    MIN_POINTS non-zero points or a noise of zero."""
    flux = np.asarray(flux, dtype=np.float64)
    flux = flux[flux != 0]
    if flux.size < MIN_POINTS:
        return None
    noise = DER_SNR_NOISE_FACTOR * float(np.median(np.abs(2.0 * flux[2:-2] - flux[:-4] - flux[4:])))
    if noise <= 0:
        return None
    return float(np.median(flux)) / noise


class SnrPlugin(SubPlugin):
    """Measures the signal-to-noise ratio of the stacked target spectrum by the DER_SNR method, in one or more wavelength zones."""

    async def execute(self, obs: Observation) -> Observation:
        zones = SUBPLUGIN_PARAMS.get("snr", {}).get("zones") or []
        if not zones:
            log.warning("[SNR] Not measured: subplugin_params.snr.zones is not set")
            return obs
        try:
            session_dir = Path(obs.obs_dir or "")
            spectrum = fits.getdata(session_dir / find_shared_input(obs, "stack_target_spectrum")).astype(np.float64)
            law = read_law(session_dir / find_shared_input(obs, "dispersion_law_file"))
        except Exception as e:
            log.warning(f"[SNR] Not measured: {e}")
            return obs

        wavelengths = Polynomial(law["coefficients"])(np.arange(spectrum.size))
        results = []
        for zone in zones:
            low, high = float(zone[0]), float(zone[1])
            snr = der_snr(spectrum[(wavelengths >= low) & (wavelengths <= high)])
            if snr is None:
                log.warning(f"[SNR] Zone {low:g}-{high:g} {law['unit']} left out: fewer than {MIN_POINTS} points "
                            f"of signal in it")
                continue
            results.append({"wavelength_min": low, "wavelength_max": high, "snr": float(round(snr))})
        if not results:
            return obs

        obs.reduction_data["spectrum_snr"] = results
        obs.reduction_data["snr"] = {"method": "DER_SNR"}
        values = ", ".join(f"{r['snr']:.0f} ({r['wavelength_min']:g}-{r['wavelength_max']:g} {law['unit']})"
                           for r in results)
        log.info(f"[SNR] SNR {values}, DER_SNR on the stacked spectrum")
        return obs
