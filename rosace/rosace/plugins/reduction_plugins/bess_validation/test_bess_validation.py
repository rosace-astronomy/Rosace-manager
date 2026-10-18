"""
Tests for the BeSS validation sub-plugin (BessValidationPlugin,
build_validation_data), on the summary tests' synthetic observation.
Run: pytest plugins/reduction_plugins/bess_validation/test_bess_validation.py -v
"""

import json

import aiosqlite
import pytest

from rosace.database import db_init
from rosace.plugins.reduction_plugins.bess_validation import bess_validation as module
from rosace.plugins.reduction_plugins.bess_validation.bess_validation import BessValidationPlugin, build_validation_data
from rosace.plugins.reduction_plugins.summary.test_summary import _setup

FINAL = "20260925T213751_Vega_0e37017f"


async def _row(obs_id):
    from rosace.config import DB_PATH  # read at call time: conftest.py redirects it to a temporary database
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bess_validation WHERE observation_id = ?", (obs_id,)) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


class TestBuildValidationData:

    def test_every_part(self, tmp_path, monkeypatch):
        obs, session = _setup(tmp_path, monkeypatch)

        data = build_validation_data(obs)

        assert json.loads((session / f"{FINAL}_validation.json").read_text()) == data
        assert data["header"]["star"] == "Vega"
        assert data["header"]["exposures"] == "5x100s"
        assert data["quality"]["guiding"]["total_rms_arcsec"] == 1.04
        assert data["quality"]["star_slit_offset_arcsec"] == 2.8
        assert data["quality"]["rejected_frames"] == ["t-2_1d.fits"]
        assert data["spectrum"]["file"] == f"{FINAL}.fits"
        assert {line["name"] for line in data["spectrum"]["overlay"]["lines"]} >= {"He I 5876"}
        assert data["guide"]["file"] == "GUIDING_Vega.fits"
        assert data["guide"]["overlay"]["point"] == [50.0, 40.0]
        assert data["guide"]["overlay"]["star"] is None          # no astrometric solution here
        assert data["plots"] == {"guiding": f"{FINAL}_validation_guiding.png",
                                 "frames": f"{FINAL}_validation_frames.png"}
        for name in data["plots"].values():
            assert (session / name).read_bytes()[:4] == b"\x89PNG"
        frame2d = obs.reduction_data["smile_target_images"][-1]           # the last corrected frame
        assert data["frame2d"]["file"] == frame2d
        assert data["frame2d"]["overlay"] == {"window": [-0.5, 44.5, 299.5, 65.5],
                                              "zones": [[-0.5, 9.5, 299.5, 30.5], [-0.5, 79.5, 299.5, 100.5]]}
        assert data["sky"]["fripon"] == {"file": "Vega_fripon_annotated.png", "station": "FRRA12",
                                         "capture_time": "2026-09-25T21:45:17+00:00", "offset_s": 125,
                                         "model": "fitted", "matched_stars": 80}
        assert data["sky"]["wide_field"] == {"file": "Vega_wide_field.png", "capture_time": "2026-09-25T21:40:00+00:00",
                                             "exposure_s": 5.0, "solved": True, "scale_arcsec": 31.2,
                                             "field_deg": [5.65, 4.23], "target_offset_arcmin": 12.0}
        assert set(data["files"]) == {f"{FINAL}.fits", "GUIDING_Vega.fits", frame2d, "Vega_fripon_annotated.png",
                                      "Vega_wide_field.png", *data["plots"].values()}
        assert data["unavailable"] == {}

    def test_missing_parts_never_raise(self, tmp_path, monkeypatch, caplog):
        obs, session = _setup(tmp_path, monkeypatch, with_guide=False)

        data = build_validation_data(obs)

        assert data["guide"] is None
        assert data["plots"]["guiding"] is None
        assert "no guide log recorded" in data["unavailable"]["guiding_plot"]
        assert data["plots"]["frames"] == f"{FINAL}_validation_frames.png"
        assert "guide not available" in caplog.text

    def test_one_sky_image_missing_keeps_the_other(self, tmp_path, monkeypatch):
        obs, session = _setup(tmp_path, monkeypatch)
        del obs.reduction_data["sky_camera"]

        data = build_validation_data(obs)

        assert data["sky"]["fripon"] is None
        assert "no FRIPON image" in data["unavailable"]["sky"]
        assert data["sky"]["wide_field"]["file"] == "Vega_wide_field.png"
        assert "Vega_wide_field.png" in data["files"] and "Vega_fripon_annotated.png" not in data["files"]

    def test_missing_wide_field_image_is_reported(self, tmp_path, monkeypatch):
        obs, session = _setup(tmp_path, monkeypatch)
        (session / "Vega_wide_field.png").unlink()

        data = build_validation_data(obs)

        assert data["sky"]["wide_field"] is None
        assert "Vega_wide_field.png not found" in data["unavailable"]["wide_field"]
        assert data["sky"]["fripon"] is not None

    def test_missing_frame_never_raises(self, tmp_path, monkeypatch):
        obs, session = _setup(tmp_path, monkeypatch)
        (session / obs.reduction_data["smile_target_images"][-1]).unlink()

        data = build_validation_data(obs)

        assert data["frame2d"] is None
        assert "corrected frame not found" in data["unavailable"]["frame2d"]


class TestBessValidationPlugin:

    async def test_queues_the_final_spectrum(self, tmp_path, monkeypatch):
        await db_init()
        obs, session = _setup(tmp_path, monkeypatch)
        obs.id = "bv000001"
        await BessValidationPlugin().execute(obs)
        row = await _row("bv000001")
        assert row["status"] == "pending"
        assert row["star"] == "Vega"
        assert row["session_id"] == "sess1234"
        assert row["final_spectrum"] == f"{FINAL}.fits"
        assert (session / f"{FINAL}_validation.json").exists()

    async def test_run_again_keeps_the_decision(self, tmp_path, monkeypatch):
        await db_init()
        obs, _ = _setup(tmp_path, monkeypatch)
        obs.id = "bv000002"
        await BessValidationPlugin().execute(obs)
        from rosace.config import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE bess_validation SET status = 'accepted' WHERE observation_id = 'bv000002'")
            await db.commit()
        await BessValidationPlugin().execute(obs)
        assert (await _row("bv000002"))["status"] == "accepted"

    async def test_missing_final_spectrum_is_an_error(self, tmp_path, monkeypatch):
        await db_init()
        obs, _ = _setup(tmp_path, monkeypatch)
        obs.id = "bv000003"
        del obs.reduction_data["final_spectrum"]
        with pytest.raises(RuntimeError, match="final_spectrum"):
            await BessValidationPlugin().execute(obs)
        assert await _row("bv000003") is None


class TestRebuildValidationData:
    """The --build-validation-data command: the observation is read back from
    its YAML dossier, exactly as an earlier night recorded it. Its own
    database: other tests leave sessions marked running in the shared one."""

    async def test_dry_run_then_apply(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone
        from rosace import database
        from rosace.database import (db_close_session, db_insert_observation, db_insert_session,
                                     db_queue_bess_validation, db_set_yaml_file)
        from rosace.models import SessionContext
        from rosace.observation_file import write_observation_file

        db_path = tmp_path / "rebuild.db"
        monkeypatch.setattr(database, "DB_PATH", db_path)
        monkeypatch.setattr(module, "DB_PATH", db_path)
        await db_init()
        obs, session = _setup(tmp_path, monkeypatch)
        ctx = SessionContext(site_name="St-Pancrasse", session_dir=str(session), started_at=datetime.now(timezone.utc))
        obs.session_id = ctx.session_id
        obs.yaml_file = "20260925T213751_Vega.yaml"
        write_observation_file(obs)
        await db_insert_session(ctx)
        await db_insert_observation(obs, ctx.session_id)
        await db_set_yaml_file(obs.id, obs.yaml_file)
        await db_queue_bess_validation(obs.id, ctx.session_id, "Vega", obs.reduction_data["final_spectrum"])

        with pytest.raises(RuntimeError, match="a session is running"):
            module.rebuild_validation_data(apply=True)
        await db_close_session(ctx)

        assert module.rebuild_validation_data(apply=False) == [(obs.id, "Vega", "ready")]
        assert not (session / f"{FINAL}_validation.json").exists()
        assert module.rebuild_validation_data(apply=True) == [(obs.id, "Vega", "written")]
        data = json.loads((session / f"{FINAL}_validation.json").read_text())
        assert data["header"]["star"] == "Vega"
        assert data["guide"]["overlay"]["point"] == [50.0, 40.0]
