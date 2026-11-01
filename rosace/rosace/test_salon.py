"""
Tests for salon.py: the salon manifest built from one real night (a small
synthetic installation: rosace.db, targets.db, a night directory), the
cloudy frame, the catalogue and observation list, and the refusals.
Run: pytest rosace/test_salon.py -v
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import yaml
from astropy.io import fits

from rosace import salon
from rosace.config import SUBPLUGIN_PARAMS
from rosace.salon import SalonError, build_night, check_no_session_running, find_night_dir, write_cloudy_frame
from rosace.targets import program_get_by_name, program_get_stars, star_get_by_name, star_list_aliases

NIGHT = "2026-10-16"
NIGHT_DIR = f"{NIGHT}_TestSite"


def _fits(path: Path, value: int = 100) -> str:
    fits.PrimaryHDU(np.full((6, 8), value, dtype=np.uint16)).writeto(path, overwrite=True)
    return path.name


def _dossier(night_dir: Path, oid: str, name: str, program: str, stamp: str, ra: float) -> dict:
    files = {}
    for role, minute in (("slew", "00"), ("pointing", "02"), ("stop", "20")):
        guide = f"GUIDING_{name}_10s_{stamp}_21{minute}00.fits"
        _fits(night_dir / guide.replace("GUIDING_", "GUIDING_RAW_", 1))
        files[role] = guide
    _fits(night_dir / f"GUIDING_RAW_10s_{stamp}_210300.fits")       # focus, no target name
    slit = _fits(night_dir / f"GUIDING_RAW_{name}_0_01s_{stamp}_210100.fits")
    science = [_fits(night_dir / f"{name}_2x180s_{stamp}_210500-{i}.fits") for i in (1, 2)]
    calibrations = {kind: [_fits(night_dir / f"{kind.upper()}_{stamp}-{i}.fits") for i in (1, 2, 3)]
                    for kind in ("bias", "dark", "calib", "flat")}
    return {
        "id": oid,
        "created_at": f"{NIGHT}T21:00:00Z",
        "scheduler_data": {"target": {"name": name, "ra": ra, "dec": 40.0, "magnitude_v": 5.0},
                           "queried_name": name, "program": program, "exposure": {"exposure_time": None}},
        "sequencer_data": {
            "guiding_dark_acquisition": {"origin": "camera", "dark_files": [],
                                         "median_dark_file": _fits(night_dir / "GUIDINGDARK_median.fits")},
            "slew_72ed": {"guide_image": files["slew"]},
            "slit_xy": {"image": slit},
            "pointing_72ed": {"guide_image": files["pointing"]},
            "star_spectra_images": science,
            "stop_phd2_guiding": {"guide_image": files["stop"]},
            **{f"{kind}_images": names for kind, names in calibrations.items()},
        },
        "measurements": {"ambient_temp_c": 12.0},
        "session_data": {"camera_target_temperature_deg_c": -5},
    }


@pytest.fixture
def source(tmp_path, monkeypatch):
    """A real installation's data: a night with a Be star, a PN and a broken observation."""
    root = tmp_path / "source"
    night_dir = root / "observations" / NIGHT_DIR
    night_dir.mkdir(parents=True)
    (root / "databases").mkdir()
    dossiers = [
        _dossier(night_dir, "obs1", "Star_A", "Be_stars", "20261016", 10.0),
        _dossier(night_dir, "obs2", "M57", "PN", "20261017", 283.0),
        _dossier(night_dir, "obs3", "Star_C", "Be_stars", "20261018", 50.0),
    ]
    dossiers[1]["created_at"] = f"{NIGHT}T22:00:00Z"
    dossiers[2]["created_at"] = f"{NIGHT}T23:00:00Z"
    (night_dir / dossiers[2]["sequencer_data"]["star_spectra_images"][0]).unlink()
    for dossier in dossiers:
        (night_dir / f"{dossier['id']}.yaml").write_text(yaml.safe_dump(dossier))
    (night_dir / "20261016T200000_s1_session.log").write_text(
        "2026-10-16 21:02:10 - INFO - [POINTING_72ED] PHD2 lock position for Star_A: (612.5, 470.25)\n")

    db = sqlite3.connect(root / "databases" / "rosace.db")
    db.execute("CREATE TABLE sessions (id TEXT, started_at TEXT, status TEXT, session_dir TEXT)")
    db.execute("CREATE TABLE observations (id TEXT, session_id TEXT, yaml_file TEXT, created_at TEXT, "
               "sequencer_status TEXT)")
    db.execute("INSERT INTO sessions VALUES ('s1', '2026-10-16T20:00:00+00:00', 'closed', ?)", (str(night_dir),))
    for dossier, status in zip(dossiers, ("done", "done", "done")):
        db.execute("INSERT INTO observations VALUES (?, 's1', ?, ?, ?)",
                   (dossier["id"], f"{dossier['id']}.yaml", dossier["created_at"], status))
    db.execute("INSERT INTO observations VALUES ('obs4', 's1', 'obs4.yaml', '2026-10-16T23:30:00Z', 'error')")
    db.commit()
    db.close()
    targets = sqlite3.connect(root / "databases" / "targets.db")
    targets.execute("CREATE TABLE stars (name TEXT, spectral_type TEXT)")
    targets.execute("INSERT INTO stars VALUES ('Star_A', 'B2Ve')")
    targets.commit()
    targets.close()

    monkeypatch.setitem(SUBPLUGIN_PARAMS, "salon", {
        "source_data_dir": str(root), "cycles": 2,
        "programs": {"Be_stars": "Be_stars", "PN": "salon_PN"},
    })
    return root


class TestBuildNight:

    def test_successful_observations_kept_in_order(self, source):
        night = build_night(NIGHT)
        assert [s["observation_id"] for s in night.manifest["stars"]] == ["obs1", "obs2"]
        assert [s["program"] for s in night.manifest["stars"]] == ["Be_stars", "salon_PN"]
        assert night.skipped == [("obs3 (obs3.yaml)", "science frame missing: Star_C_2x180s_20261018_210500-1.fits")]

    def test_entry_names_the_night_files_by_absolute_path(self, source):
        star = build_night(NIGHT).manifest["stars"][0]
        night_dir = source / "observations" / NIGHT_DIR
        assert star["guiding"]["slew"] == str(night_dir / "GUIDING_RAW_Star_A_10s_20261016_210000.fits")
        assert star["guiding"]["focus"] == str(night_dir / "GUIDING_RAW_10s_20261016_210300.fits")
        assert star["calibrations"]["dark"][0] == str(night_dir / "DARK_20261016-1.fits")
        assert star["recorded_at"].startswith("2026-10-16T21:00:00")
        assert star["lock_position"] == [612.5, 470.25]
        assert star["spectral_type"] == "B2Ve"

    def test_program_without_focus_skips_the_focus_image(self, source):
        stars = build_night(NIGHT).manifest["stars"]
        assert stars[0]["guide_order"] == ["slew", "pointing", "focus", "stop"]
        assert stars[1]["guide_order"] == ["slew", "pointing", "stop"]

    def test_missing_lock_position_is_a_warning(self, source):
        night = build_night(NIGHT)
        assert "lock_position" not in night.manifest["stars"][1]
        assert any("M57" in w for w in night.warnings)

    def test_night_wide_calibrations_and_guiding_dark(self, source):
        manifest = build_night(NIGHT).manifest
        assert manifest["calibrations"]["guiding_dark"][0].endswith("GUIDINGDARK_median.fits")
        assert manifest["camera_temperature_c"] == -5

    def test_unknown_night(self, source):
        with pytest.raises(SalonError, match="no night directory 2026-10-01"):
            build_night("2026-10-01")


class TestFindNightDir:

    def test_full_name_accepted(self, source):
        assert find_night_dir(NIGHT_DIR, source / "observations", source / "databases" / "rosace.db").name == NIGHT_DIR

    def test_several_directories_of_the_date(self, source):
        (source / "observations" / f"{NIGHT}_Other").mkdir()
        (source / "observations" / f"{NIGHT}_Third").mkdir()
        # only one holds successful observations: that one
        assert find_night_dir(NIGHT, source / "observations", source / "databases" / "rosace.db").name == NIGHT_DIR

    def test_ambiguous(self, tmp_path):
        observations = tmp_path / "observations"
        for name in ("A", "B"):
            (observations / f"{NIGHT}_{name}").mkdir(parents=True)
        db = sqlite3.connect(tmp_path / "rosace.db")
        db.execute("CREATE TABLE sessions (id TEXT, started_at TEXT, status TEXT, session_dir TEXT)")
        db.execute("CREATE TABLE observations (id TEXT, session_id TEXT, yaml_file TEXT, created_at TEXT, "
                   "sequencer_status TEXT)")
        db.commit()
        db.close()
        with pytest.raises(SalonError, match="several night directories"):
            find_night_dir(NIGHT, observations, tmp_path / "rosace.db")


class TestRunningSession:

    def test_running_session_refused(self, source):
        db = sqlite3.connect(source / "databases" / "rosace.db")
        db.execute("INSERT INTO sessions VALUES ('s2', '2026-11-01T10:00:00+00:00', 'running', NULL)")
        db.commit()
        db.close()
        now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        with pytest.raises(SalonError, match="running"):
            check_no_session_running(source / "databases" / "rosace.db", now=now)

    def test_stale_running_session_ignored(self, source):
        now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        check_no_session_running(source / "databases" / "rosace.db", now=now)


class TestCloudyFrame:

    def test_background_and_noise_no_star(self, tmp_path):
        rng = np.random.default_rng(1)
        data = rng.normal(1000, 20, (200, 300))
        data[100:103, 150:153] = 50000                      # a star
        source = tmp_path / "guide.fits"
        fits.PrimaryHDU(np.clip(data, 0, 65535).astype(np.uint16)).writeto(source)
        write_cloudy_frame(source, tmp_path / "cloudy.fits")
        cloudy = fits.getdata(tmp_path / "cloudy.fits")
        assert cloudy.dtype == np.uint16 and cloudy.shape == (200, 300)
        assert np.median(cloudy) == pytest.approx(1000, abs=3)
        assert cloudy.max() < 1200


class TestCatalogue:

    def test_targets_in_their_salon_programs_and_cycled_list(self, source, tmp_path, monkeypatch):
        list_path = tmp_path / "list.txt"
        monkeypatch.setitem(SUBPLUGIN_PARAMS, "observation_list_path", str(list_path))
        stars = build_night(NIGHT).manifest["stars"]
        asyncio.run(salon._seed_catalogue(stars, cycles=2))
        asyncio.run(salon._seed_catalogue(stars, cycles=2))   # idempotent
        lines = list_path.read_text().splitlines()
        assert lines[0] == "Priority, Program, Star, commentaire"
        assert [line.split(", ")[1:3] for line in lines[1:]] == [
            ["Be_stars", "Star_A"], ["salon_PN", "M57"], ["Be_stars", "Star_A"], ["salon_PN", "M57"]]
        program = asyncio.run(program_get_by_name("salon_PN"))
        assert [s.name for s in asyncio.run(program_get_stars(program.id))] == ["M57"]

    def test_list_names_the_target_as_the_recorded_night_did(self, source, tmp_path, monkeypatch):
        list_path = tmp_path / "list.txt"
        monkeypatch.setitem(SUBPLUGIN_PARAMS, "observation_list_path", str(list_path))
        stars = build_night(NIGHT).manifest["stars"][:1]
        stars[0].update(name="* alf Lyr Salon", queried_name="Vega Salon")
        asyncio.run(salon._seed_catalogue(stars, cycles=1))
        asyncio.run(salon._seed_catalogue(stars, cycles=1))   # alias declared once
        assert list_path.read_text().splitlines()[1].split(", ")[2] == "Vega Salon"
        star = asyncio.run(star_get_by_name("Vega Salon"))
        assert star.name == "* alf Lyr Salon"
        assert asyncio.run(star_list_aliases(star.id)) == ["Vega Salon"]


class TestSalonPrograms:

    @pytest.mark.parametrize("program", ["salon_PN", "salon_Cataclysmic", "salon_All_stars"])
    def test_program_file_is_valid_and_differs_from_be_stars(self, program):
        programs = Path(salon.__file__).parent / "programs"
        be = yaml.safe_load((programs / "Be_stars.yaml").read_text())
        mine = yaml.safe_load((programs / f"{program}.yaml").read_text())
        assert mine["program"] == program
        assert "scheduler" not in mine
        assert set(mine["reduction"]) < set(be["reduction"])
        assert "bess_validation" not in mine["reduction"] and "export_demetra" not in mine["reduction"]


class TestSalonProfiles:

    @pytest.mark.parametrize("profile", ["hardware_salon", "hardware_salon_replay"])
    def test_every_device_class_exists(self, profile):
        import importlib
        conf = Path(salon.__file__).parent / "conf"
        data = yaml.safe_load((conf / f"{profile}.yaml").read_text())
        assert data["simulated"] is True and data["failure_every"] == 5
        assert data["replay_dataset_dir"] == "~/.rosace-salon/replay"
        for name, device in data["INDI_drivers"].items():
            module = importlib.import_module(f"{device['module_dir']}.{device['module']}")
            assert hasattr(module, device["class_name"]), (profile, name)

    def test_real_focuser_has_the_fields_its_class_requires(self):
        conf = Path(salon.__file__).parent / "conf"
        focuser = yaml.safe_load((conf / "hardware_salon.yaml").read_text())["INDI_drivers"]["focuser"]
        assert {"port", "focus_range", "autofocus_step", "autofocus_range"} <= focuser.keys()

    def test_without_hardware_nothing_is_an_indi_device(self):
        conf = Path(salon.__file__).parent / "conf"
        data = yaml.safe_load((conf / "hardware_salon_replay.yaml").read_text())
        assert {d["module_dir"] for d in data["INDI_drivers"].values()} == {"rosace.hardware_replay"}
        assert {d["module"] for d in data["INDI_drivers"].values()} == {"devices"}

