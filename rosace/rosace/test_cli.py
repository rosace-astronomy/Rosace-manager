"""
Tests for cli.py (the `rosace` command, same as `python -m rosace.main`).
Run: pytest test_cli.py -v

--check-config, --help and --create-admin are covered through the real
process in test_rosace_core.py. This file covers the rest: the options that
start the server (tested in-process on run_cli(), which returns instead of
starting anything), the usage errors argparse reports, Tab completion, and
--list-subplugins/--purge-simulated/--init-config (real process).
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from rosace.cli import main, run_cli


class TestServerStartOptions:
    """run_cli() only sets ROSACE_CONFIG for these and returns: main.py then
    loads that config and starts the server."""

    @pytest.fixture(autouse=True)
    def _no_rosace_config(self, monkeypatch):
        # delenv records the original value, so whatever run_cli() sets is undone afterwards.
        monkeypatch.delenv("ROSACE_CONFIG", raising=False)

    def test_demo_mode_points_at_the_packaged_demo_config(self):
        run_cli(["--demo-mode"])

        demo_config = Path(os.environ["ROSACE_CONFIG"])
        assert demo_config == Path(__file__).parent / "conf" / "config_demo.yaml"
        assert demo_config.is_file()

    def test_config_path_argument_sets_rosace_config(self):
        run_cli(["/some/where/config.yaml"])

        assert os.environ["ROSACE_CONFIG"] == "/some/where/config.yaml"

    def test_no_argument_leaves_rosace_config_alone(self):
        run_cli([])

        assert "ROSACE_CONFIG" not in os.environ


class TestMain:
    """main() takes --host/--port, lets run_cli() handle the rest, then
    starts the server — uvicorn.run replaced here by a recorder."""

    @pytest.fixture(autouse=True)
    def _server_calls(self, monkeypatch):
        monkeypatch.delenv("ROSACE_CONFIG", raising=False)
        self.calls = []
        import uvicorn
        monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: self.calls.append((app, kwargs)))

    def test_defaults(self):
        main([])

        assert self.calls == [("rosace.main:app", {"host": "0.0.0.0", "port": 8000, "access_log": False})]

    def test_host_and_port(self):
        main(["--port", "8001", "--host", "127.0.0.1"])

        assert self.calls == [("rosace.main:app", {"host": "127.0.0.1", "port": 8001, "access_log": False})]

    def test_port_before_a_config_path(self):
        main(["--port", "8001", "/some/where/config.yaml"])

        assert os.environ["ROSACE_CONFIG"] == "/some/where/config.yaml"
        assert self.calls[0][1]["port"] == 8001

    def test_demo_mode_on_another_port(self):
        main(["--demo-mode", "--port", "8001"])

        assert Path(os.environ["ROSACE_CONFIG"]).name == "config_demo.yaml"
        assert self.calls[0][1]["port"] == 8001

    def test_port_with_an_equals_sign(self):
        main(["--port=8001"])

        assert self.calls[0][1]["port"] == 8001

    @pytest.mark.parametrize("argv", [["--port", "abc"], ["--port", "0"], ["--port", "70000"], ["--port"]])
    def test_bad_port_fails_without_starting(self, argv):
        with pytest.raises(SystemExit) as exit_info:
            main(argv)

        assert exit_info.value.code == 2
        assert self.calls == []


class TestUsageErrors:
    """Mistakes in the command line: argparse's error on stderr, exit code 2,
    nothing run."""

    @pytest.mark.parametrize("argv, message", [
        (["--prot", "8001"], "unrecognized arguments: --prot"),
        (["--apply"], "--apply only goes with"),
        (["--purge-demo", "--init-config"], "not allowed with argument"),
        (["--list-subplugins", "foo"], "invalid choice: 'foo'"),
        (["--demo-mode", "/some/where/config.yaml"], "--demo-mode and a config path"),
        (["/some/where/config.yaml", "--purge-simulated"], "a config path cannot go with --purge-simulated"),
        (["--demo-mode", "--purge-demo"], "--demo-mode cannot go with --purge-demo"),
        (["--purge-failed", "29/10/2026"], "not a YYYY-MM-DD date"),
        (["--purge-failed"], "expected one argument"),
    ])
    def test_reported_with_exit_code_2(self, argv, message, capsys, monkeypatch):
        monkeypatch.delenv("ROSACE_CONFIG", raising=False)
        with pytest.raises(SystemExit) as exit_info:
            run_cli(argv)

        assert exit_info.value.code == 2
        assert message in capsys.readouterr().err
        assert "ROSACE_CONFIG" not in os.environ


class TestCompletion:

    def test_the_families_are_pipeline_loaders(self):
        from rosace.cli import SUBPLUGIN_FAMILIES
        from rosace.plugins.pipeline_loader import _SUBPLUGIN_FAMILY_DIRS

        assert set(SUBPLUGIN_FAMILIES) == set(_SUBPLUGIN_FAMILY_DIRS)

    def test_config_paths_complete_with_yaml_files(self):
        pytest.importorskip("argcomplete")
        from rosace.cli import build_parser

        actions = {action.dest: action for action in build_parser()._actions}
        for dest in ("config_path", "check_config"):
            assert type(actions[dest].completer).__name__ == "FilesCompleter"

    def test_the_marker_argcomplete_looks_for_is_at_the_top(self):
        # argcomplete only completes a console script whose module carries
        # this marker in its first 1024 bytes.
        head = (Path(__file__).parent / "cli.py").read_bytes()[:1024]
        assert b"PYTHON_ARGCOMPLETE_OK" in head


def _run(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "rosace.main", *args],
        capture_output=True, text=True, env=env,
    )


class TestInitConfigCLI:

    def test_copies_the_packaged_config_once_then_refuses(self, tmp_path):
        env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path)}
        target = tmp_path / "rosace" / "config.yaml"
        packaged = Path(__file__).parent / "conf" / "config.yaml"

        first = _run("--init-config", env=env)
        assert first.returncode == 0, first.stdout + first.stderr
        assert target.read_text() == packaged.read_text()

        target.write_text("# edited by the observer\n")
        second = _run("--init-config", env=env)
        assert second.returncode == 1
        assert "already exists" in second.stdout
        assert target.read_text() == "# edited by the observer\n"


class TestVersionCLI:

    def test_prints_the_installed_version(self):
        from importlib import metadata

        result = _run("--version")

        assert result.returncode == 0
        assert result.stdout.strip() == f"Rosace {metadata.version('rosace')}"


class TestListSubpluginsCLI:

    def test_lists_every_family(self):
        result = _run("--list-subplugins")
        assert result.returncode == 0
        families = {line.split()[0] for line in result.stdout.splitlines() if line.strip()}
        assert families == {"sequencer", "reduction", "sharing"}
        assert any(line.split()[1] == "focus" for line in result.stdout.splitlines())

    def test_restricts_to_one_family(self):
        result = _run("--list-subplugins", "sequencer")
        assert result.returncode == 0
        assert {line.split()[0] for line in result.stdout.splitlines() if line.strip()} == {"sequencer"}

    def test_unknown_family_fails(self):
        result = _run("--list-subplugins", "nope")
        assert result.returncode == 2
        assert "invalid choice: 'nope'" in result.stderr


class TestPurgeSimulatedCLI:
    """Run against a database of its own (ROSACE_CONFIG on a tmp config), never
    the real one."""

    def _env(self, tmp_path: Path) -> dict:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(f"""
base_data_dir: {tmp_path / "data"}
databases_dir: databases
log_dir: log
observations_dir: observations
dark_library_dir: dark_library
observatory:
  site_name: TestSite
  latitude_deg: 45.0
  longitude_deg: 5.0
  elevation_m: 950
active_hardware: hardware_indi_simu
active_scheduler: blank
sequencer_backend: internal
scheduler_retry_seconds: 60
hardware_connect_timeout_seconds: 5
camera_cooldown_timeout_seconds: 300
session_ttl_hours: 336
session_cookie_secure: false
simbad_tap_urls:
  - https://simbad.example/sync
""")
        env = {**os.environ, "ROSACE_CONFIG": str(config_path)}
        created = subprocess.run(
            [sys.executable, "-c", "import asyncio; from rosace.database import db_init; asyncio.run(db_init())"],
            capture_output=True, text=True, env=env,
        )
        assert created.returncode == 0, created.stderr
        return env

    def _db(self, tmp_path: Path) -> Path:
        return tmp_path / "data" / "databases" / "rosace.db"

    def test_nothing_to_purge(self, tmp_path):
        result = _run("--purge-simulated", env=self._env(tmp_path))
        assert result.returncode == 0
        assert "[OK] no simulated session to purge" in result.stdout

    def test_dry_run_lists_a_simulated_session_and_deletes_nothing(self, tmp_path):
        env = self._env(tmp_path)
        with sqlite3.connect(self._db(tmp_path)) as db:
            db.execute(
                "INSERT INTO sessions (id, started_at, closed_at, status, simulated) VALUES (?, ?, ?, ?, ?)",
                ("sim00001", "2026-10-01T20:00:00+00:00", "2026-10-01T21:00:00+00:00", "closed", 1),
            )

        result = _run("--purge-simulated", env=env)

        assert result.returncode == 0
        assert "sim00001" in result.stdout
        assert "Dry run: nothing deleted" in result.stdout
        with sqlite3.connect(self._db(tmp_path)) as db:
            assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


class TestBuildValidationDataCLI(TestPurgeSimulatedCLI):
    """Same temporary installation as --purge-simulated (its _env)."""

    def test_empty_list(self, tmp_path):
        result = _run("--build-validation-data", env=self._env(tmp_path))
        assert result.returncode == 0, result.stderr
        assert "0 spectra, 0 failed" in result.stdout
        assert "Dry run: nothing written" in result.stdout

    def test_refused_while_a_session_runs(self, tmp_path):
        from datetime import datetime, timezone
        env = self._env(tmp_path)
        with sqlite3.connect(self._db(tmp_path)) as db:
            db.execute("INSERT INTO sessions (id, started_at, status) VALUES (?, ?, ?)",
                       ("run00001", datetime.now(timezone.utc).isoformat(), "running"))

        result = _run("--build-validation-data", "--apply", env=env)

        assert result.returncode == 1
        assert "[FAIL] a session is running" in result.stdout


class TestPurgeFailedCLI(TestPurgeSimulatedCLI):
    """Same temporary installation as --purge-simulated (its _env)."""

    def test_nothing_to_purge(self, tmp_path):
        result = _run("--purge-failed", "2026-10-29", env=self._env(tmp_path))
        assert result.returncode == 0, result.stderr
        assert "[OK] nothing to purge for the night of 2026-10-29" in result.stdout

    def test_dry_run_lists_a_failed_observation_and_deletes_nothing(self, tmp_path):
        env = self._env(tmp_path)
        night = tmp_path / "2026-10-29_TestSite"
        night.mkdir()
        with sqlite3.connect(self._db(tmp_path)) as db:
            db.execute("INSERT INTO sessions (id, started_at, closed_at, status, session_dir) VALUES (?, ?, ?, ?, ?)",
                       ("ses00001", "2026-10-29T19:00:00+00:00", "2026-10-29T21:00:00+00:00", "closed", str(night)))
            db.execute("INSERT INTO observations (id, session_id, target_name, created_at, sequencer_status) "
                       "VALUES (?, ?, ?, ?, ?)",
                       ("obs00001", "ses00001", "Vega", "2026-10-29T19:10:00+00:00", "error"))

        result = _run("--purge-failed", "2026-10-29", env=env)

        assert result.returncode == 0, result.stderr
        assert "ses00001" in result.stdout and "Dry run: nothing deleted" in result.stdout
        with sqlite3.connect(self._db(tmp_path)) as db:
            assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1

    def test_refused_while_a_session_runs(self, tmp_path):
        from datetime import datetime, timezone
        env = self._env(tmp_path)
        with sqlite3.connect(self._db(tmp_path)) as db:
            db.execute("INSERT INTO sessions (id, started_at, status) VALUES (?, ?, ?)",
                       ("run00001", datetime.now(timezone.utc).isoformat(), "running"))

        result = _run("--purge-failed", "2026-10-29", "--apply", env=env)

        assert result.returncode == 1
        assert "[FAIL] a session is running" in result.stdout
