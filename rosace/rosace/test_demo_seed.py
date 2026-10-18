# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
demo_seed.py writes real, persistent rows (targets.db, ObservationList.txt,
an admin account) from a replay dataset (here the synthetic one of
hardware_replay/testing.py, named by a temporary hardware profile) — testing it meaningfully against the shared, session-scoped
tmp_data_dir (conftest.py) would be unreliable, since other test modules may
already have populated that shared targets.db by the time this one runs, and
seed_demo_data()'s idempotency check ("skip once any program exists") would
then trivially skip. Instead, same isolation pattern as
TestCreateAdminCLI (test_rosace_core.py): each test points ROSACE_CONFIG at
its own fresh tmp_path and runs in a subprocess.
"""

import os
import subprocess
import sys
from pathlib import Path

from rosace.hardware_replay.testing import make_dataset

_SEED_SCRIPT = """
import asyncio, sys
from pathlib import Path
import rosace.hardware_replay.bench as bench
bench.HARDWARE_CONFIG_PATH = Path(sys.argv[1])
from rosace.database import db_init
from rosace.targets import targets_db_init, program_list, program_get_by_name, program_get_stars
from rosace.auth import user_count
from rosace.demo_seed import seed_demo_data

async def main():
    await db_init()
    await targets_db_init()
    await seed_demo_data()
    programs = await program_list()
    be = await program_get_by_name("Be_stars")
    stars = sorted((s.name, s.exposure_time) for s in (await program_get_stars(be.id) if be else []))
    print("programs=", len(programs))
    print("stars=", stars)
    print("users=", await user_count())

asyncio.run(main())
"""


class TestSeedDemoData:
    def _write_tmp_config(self, tmp_path: Path, with_dataset: bool = True) -> tuple[Path, Path]:
        data = tmp_path / "data"
        dataset = tmp_path / "replay"
        if with_dataset:
            make_dataset(dataset)
        profile = tmp_path / "hardware_replay.yaml"
        profile.write_text(f"replay_dataset_dir: {dataset}\n")
        config_path = tmp_path / "config.yaml"
        config_path.write_text(f"""
is_demo: true
base_data_dir: {data}
databases_dir: databases
log_dir: log
observations_dir: observations
dark_library_dir: dark_library
observatory:
  site_name: TestSite
  latitude_deg: 45.0
  longitude_deg: 5.0
  elevation_m: 950
active_hardware: hardware_replay
active_scheduler: scheduler_rosace_2026
sequencer_backend: indi
scheduler_retry_seconds: 60
hardware_connect_timeout_seconds: 5
camera_cooldown_timeout_seconds: 300
session_ttl_hours: 336
session_cookie_secure: false
subplugin_params:
  observation_list_path: {data / "ObservationList.txt"}
""")
        return config_path, profile

    def _run(self, tmp_path: Path, with_dataset: bool = True) -> subprocess.CompletedProcess:
        config_path, profile = self._write_tmp_config(tmp_path, with_dataset)
        return subprocess.run(
            [sys.executable, "-c", _SEED_SCRIPT, str(profile)],
            capture_output=True, text=True,
            env={**os.environ, "ROSACE_CONFIG": str(config_path)},
        )

    def test_seeds_the_dataset_targets_list_and_previews_on_empty_db(self, tmp_path):
        result = self._run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert "programs= 1" in result.stdout
        assert "stars= [('Star A', None), ('Star B', None)]" in result.stdout
        assert "users= 1" in result.stdout

        lines = (tmp_path / "data" / "ObservationList.txt").read_text(encoding="utf-8").strip().splitlines()
        assert lines == [
            "Priority, Program, Star, commentaire",
            "1, Be_stars, Star A, replay of observation obs1",
            "2, Be_stars, Star B, replay of observation obs2",
        ]
        for preview in ("guidage.fits", "spectre.fits"):     # plain FITS, from the dataset's .fits.gz frames
            assert (tmp_path / "data" / "gui_preview" / preview).read_bytes()[:6] == b"SIMPLE"

    def test_idempotent_on_second_run(self, tmp_path):
        first = self._run(tmp_path)
        assert first.returncode == 0, first.stderr

        second = self._run(tmp_path)
        assert second.returncode == 0, second.stderr
        assert "programs= 1" in second.stdout
        assert "users= 1" in second.stdout

    def test_without_dataset_only_the_account_and_a_warning(self, tmp_path):
        result = self._run(tmp_path, with_dataset=False)
        assert result.returncode == 0, result.stderr
        assert "programs= 0" in result.stdout
        assert "users= 1" in result.stdout
        assert "build_demo_dataset.py" in result.stderr + result.stdout
