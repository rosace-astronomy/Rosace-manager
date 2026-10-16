"""A tiny synthetic replay dataset, made the shape
scripts/build_demo_dataset.py writes -- for the tests (hardware_replay's own,
test_demo_seed.py)."""

from __future__ import annotations

import numpy as np
import yaml
from astropy.io import fits

SHAPE = (6, 8)


def _frame(directory, relative, value, exptime):
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    hdu = fits.PrimaryHDU(np.full(SHAPE, value, dtype=np.uint16))
    hdu.header["EXPTIME"] = exptime
    hdu.writeto(path, overwrite=True)
    return relative


def make_dataset(directory):
    """Two observations (values 100+ and 200+), calibrations (bias 1, dark 2,
    calib 3, flat 4, guiding dark 5), homing 9; returns the dataset dir."""
    stars = []
    for n, (name, ra, dec) in enumerate((("Star A", 10.0, 50.0), ("Star B", 80.0, 20.0)), start=1):
        base = 100 * n
        stars.append({
            "observation_id": f"obs{n}", "name": name, "queried_name": name,
            "ra_deg": ra, "dec_deg": dec, "magnitude_v": 5.0, "spectral_type": "B2Ve",
            "program": "Be_stars", "exposure_time": None, "ambient_temp_c": 10.0 + n,
            "lock_position": [500.0 + n, 400.0 + n],
            "science": [_frame(directory, f"science/{name}-{i}.fits", base + i, 180.0) for i in (1, 2)],
            "guiding": {role: _frame(directory, f"guiding/{name}_{role}.fits", base + 10 + k, 10.0)
                        for k, role in enumerate(("slew", "slit", "pointing", "focus", "stop"))},
        })
    manifest = {
        "source": "2026-10-14_Test, observations obs1, obs2",
        "ambient_temp_c": 15.0,
        "camera_temperature_c": -5,
        "homing": _frame(directory, "guiding/homing.fits", 9, 10.0),
        "calibrations": {kind: [_frame(directory, f"calibrations/{kind}-{i}.fits", value, exptime) for i in (1, 2)]
                         for kind, value, exptime in (("bias", 1, 0.0), ("dark", 2, 180.0), ("calib", 3, 3.0),
                                                      ("flat", 4, 3.0), ("guiding_dark", 5, 10.0))},
        "reduction": {},
        "astrometry": [],
        "stars": stars,
    }
    (directory / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    return directory
