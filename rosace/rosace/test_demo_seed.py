# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
demo_seed.py writes real, persistent rows (targets.db, ObservationList.txt,
an admin account) — testing it meaningfully against the shared, session-scoped
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

_SEED_SCRIPT = """
import asyncio
from rosace.database import db_init
from rosace.targets import targets_db_init, program_list, program_get_by_name, program_get_stars
from rosace.auth import user_count
from rosace.demo_seed import seed_demo_data, OBSERVATION_LIST_PATH

async def main():
    await db_init()
    await targets_db_init()
    await seed_demo_data()
    programs = await program_list()
    demo = await program_get_by_name("demo")
    stars = sorted(s.name for s in (await program_get_stars(demo.id) if demo else []))
    print("programs=", len(programs))
    print("stars=", stars)
    print("users=", await user_count())
    print("obs_list_exists=", OBSERVATION_LIST_PATH.exists())

asyncio.run(main())
"""


class TestSeedDemoData:
    def _write_tmp_config(self, tmp_path: Path) -> Path:
        config_path = tmp_path / "config.yaml"
        obs_list_path = tmp_path / "data" / "ObservationList.txt"
        config_path.write_text(f"""
base_data_dir: {tmp_path / "data"}
databases_dir: databases
log_dir: log
observations_dir: observations
dark_library_dir: dark_library
site_name: TestSite
site_latitude_deg: 45.0
site_longitude_deg: 5.0
site_elevation_m: 950
active_hardware: hardware_indi_simu
active_scheduler: scheduler_rosace_2026
sequencer_backend: internal
scheduler_retry_seconds: 60
hardware_connect_timeout_seconds: 5
camera_cooldown_timeout_seconds: 300
session_ttl_hours: 336
session_cookie_secure: false
subplugin_params:
  scheduler_rosace_2026:
    observation_list_path: {obs_list_path}
""")
        return config_path

    def _run(self, tmp_path: Path) -> subprocess.CompletedProcess:
        config_path = self._write_tmp_config(tmp_path)
        return subprocess.run(
            [sys.executable, "-c", _SEED_SCRIPT],
            capture_output=True, text=True,
            env={**os.environ, "ROSACE_CONFIG": str(config_path)},
        )

    def test_seeds_stars_program_admin_and_observation_list_on_empty_db(self, tmp_path):
        result = self._run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert "programs= 1" in result.stdout
        assert "stars= ['Arcturus', 'Vega']" in result.stdout
        assert "users= 1" in result.stdout
        assert "obs_list_exists= True" in result.stdout

        obs_list = (tmp_path / "data" / "ObservationList.txt").read_text(encoding="utf-8")
        lines = obs_list.strip().splitlines()
        assert lines[0] == "Priority, Program, Star, commentaire"
        assert "1, demo, Vega, demo seed" in lines
        assert "2, demo, Arcturus, demo seed" in lines

    def test_idempotent_on_second_run(self, tmp_path):
        first = self._run(tmp_path)
        assert first.returncode == 0, first.stderr

        second = self._run(tmp_path)
        assert second.returncode == 0, second.stderr
        assert "programs= 1" in second.stdout
        assert "users= 1" in second.stdout
