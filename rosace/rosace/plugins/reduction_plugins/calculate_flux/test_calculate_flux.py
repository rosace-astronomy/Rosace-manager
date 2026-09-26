"""
Tests for the CALCULATE_FLUX sub-plugin (CalculateFluxPlugin).
Run: pytest plugins/reduction_plugins/calculate_flux/test_calculate_flux.py -v
"""

import logging
from datetime import datetime, timezone

import aiosqlite
import numpy as np
from astropy.io import fits

from rosace.database import db_init
from rosace.models import Observation, Target
from rosace.plugins.reduction_plugins.calculate_flux import calculate_flux
from rosace.plugins.reduction_plugins.calculate_flux.calculate_flux import CalculateFluxPlugin
from rosace.plugins.reduction_plugins.spectrum_flux_helpers import METHOD_VERSION

SHAPE = (200, 500)


def _write_frame(path, with_trace=True, exposure=30.0, seed=0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    data = 300.0 + rng.normal(0.0, 8.0, SHAPE)
    if with_trace:
        data += 400.0 * np.exp(-0.5 * ((yy - 100.0) / 3.0) ** 2)
    hdu = fits.PrimaryHDU(data.astype(np.float32))
    hdu.header["EXPTIME"] = exposure
    hdu.writeto(path)


def _make_obs(obs_dir, files, obs_id="obs-flux"):
    obs = Observation(id=obs_id, created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(obs_dir))
    obs.scheduler_data["target"] = Target(name="*  16 Peg", ra=344.0, dec=25.9, magnitude_v=5.08)
    obs.scheduler_data["queried_name"] = "16 Peg"
    obs.sequencer_data["star_spectra_images"] = files
    return obs


async def _rows(obs_id):
    from rosace.config import DB_PATH  # read at call time: conftest.py redirects it to a temporary database

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM spectrum_flux WHERE observation_id = ? ORDER BY id", (obs_id,)) as cursor:
            return await cursor.fetchall()


class TestCalculateFluxPlugin:

    async def test_every_sub_exposure_goes_to_casier_and_database(self, tmp_path):
        await db_init()
        _write_frame(tmp_path / "a-1.fits", exposure=30.0, seed=1)
        _write_frame(tmp_path / "a-2.fits", exposure=30.0, seed=2)
        obs = _make_obs(tmp_path, ["a-1.fits", "a-2.fits"], obs_id="obs-flux-both")

        await CalculateFluxPlugin().execute(obs)

        casier = obs.reduction_data["calculate_flux"]
        assert casier["method_version"] == METHOD_VERSION
        assert set(casier["images"]) == {"a-1.fits", "a-2.fits"}
        image = casier["images"]["a-1.fits"]
        assert image["exposure_s"] == 30.0
        assert image["total_adu"] > 0
        assert "method_version" not in image and "notes" not in image
        rows = await _rows("obs-flux-both")
        assert [row["image_name"] for row in rows] == ["a-1.fits", "a-2.fits"]
        assert rows[0]["target_name"] == "16 Peg"
        assert rows[0]["magnitude_v"] == 5.08
        assert rows[0]["total_adu"] == image["total_adu"]

    async def test_unreadable_file_is_skipped_not_raised(self, tmp_path, caplog):
        await db_init()
        (tmp_path / "broken.fits").write_text("not a FITS file")
        _write_frame(tmp_path / "good.fits")
        obs = _make_obs(tmp_path, ["broken.fits", "good.fits"], obs_id="obs-flux-broken")

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.calculate_flux"):
            await CalculateFluxPlugin().execute(obs)

        assert set(obs.reduction_data["calculate_flux"]["images"]) == {"good.fits"}
        assert "broken.fits: not measured" in caplog.text

    async def test_frame_without_trace_is_skipped(self, tmp_path, caplog):
        await db_init()
        _write_frame(tmp_path / "empty.fits", with_trace=False)
        obs = _make_obs(tmp_path, ["empty.fits"], obs_id="obs-flux-empty")

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.calculate_flux"):
            await CalculateFluxPlugin().execute(obs)

        assert "calculate_flux" not in obs.reduction_data
        assert "no trace found" in caplog.text
        assert await _rows("obs-flux-empty") == []

    async def test_database_error_keeps_the_casier(self, tmp_path, monkeypatch, caplog):
        async def failing_insert(*args, **kwargs):
            raise RuntimeError("database locked")

        monkeypatch.setattr(calculate_flux, "db_insert_spectrum_flux", failing_insert)
        _write_frame(tmp_path / "a-1.fits")
        obs = _make_obs(tmp_path, ["a-1.fits"])

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.calculate_flux"):
            await CalculateFluxPlugin().execute(obs)

        assert "a-1.fits" in obs.reduction_data["calculate_flux"]["images"]
        assert "not recorded in the database" in caplog.text

    async def test_no_science_file_does_nothing(self, tmp_path):
        obs = _make_obs(tmp_path, [])

        await CalculateFluxPlugin().execute(obs)

        assert "calculate_flux" not in obs.reduction_data

    async def test_unexpected_error_never_raises(self, tmp_path, monkeypatch, caplog):
        async def boom(self, obs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(CalculateFluxPlugin, "_measure_all", boom)
        obs = _make_obs(tmp_path, ["a-1.fits"])

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.calculate_flux"):
            result = await CalculateFluxPlugin().execute(obs)

        assert result is obs
        assert "Flux measurement abandoned: unexpected" in caplog.text
