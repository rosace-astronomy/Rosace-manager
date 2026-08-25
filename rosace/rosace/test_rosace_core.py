"""
test_rosace_core.py — Regression tests for Rosace's core module.
Scope: models, config, database, targets, simbad (excluding plugins).

Run:
    pytest test_rosace_core.py -v
    pytest test_rosace_core.py -v --tb=short   # concise traceback
    pytest test_rosace_core.py -v -k "models"  # one group only

Dependencies:
    pip install pytest pytest-asyncio aiosqlite pydantic httpx
"""

import asyncio
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ─────────────────────────────────────────────────────────────
# The tmp_data_dir fixture (patches BASE_DATA_DIR) now lives in
# conftest.py — shared by every test file in the package.
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# Package imports (after the patch)
# ─────────────────────────────────────────────────────────────

from rosace.models import (
    OrchestratorStage,
    Target,
    ExposureConfig,
    Observation,
    SessionContext,
)
from rosace.database import (
    db_init,
    db_insert_session,
    db_close_session,
    db_insert_observation,
    db_set_yaml_file,
    db_stamp_stage,
    db_set_error,
    db_set_stage_status,
    db_get_recent_observations,
    db_count_observations_for_target,
    db_insert_pointing_error,
)
from rosace.targets import (
    targets_db_init,
    StarCreate, Star, StarInProgram, StarLookup,
    ProgramCreate, Program,
    star_insert, star_get, star_get_by_name, star_list,
    star_update, star_delete,
    star_add_alias, star_remove_alias, star_list_aliases,
    program_insert, program_get, program_get_by_name, program_list, program_delete,
    program_add_star, program_remove_star,
    program_get_stars, star_get_programs,
)
from rosace.simbad import (
    simbad_search,
    SimbadNotFoundError,
    SimbadQueryError,
)

from rosace.config import ACTIVE_HARDWARE, HARDWARE_CONFIG_PATH, SEQUENCER_BACKEND


# ═════════════════════════════════════════════════════════════
# GROUP 0 — Configuration
# ═════════════════════════════════════════════════════════════

class TestHardwareConfig:
    """Selection of the active hardware configuration file."""

    def test_active_hardware_matches_config_path_stem(self):
        assert HARDWARE_CONFIG_PATH.stem == ACTIVE_HARDWARE

    def test_default_hardware_config_file_exists(self):
        assert HARDWARE_CONFIG_PATH.exists()


class TestSequencerBackend:
    """SEQUENCER execution backend (internal/indi), the default at startup."""

    def test_backend_loaded_from_config_is_valid(self):
        """conf/config.yaml is free to choose 'internal' or 'indi' — no fixed
        value to test here, just that whatever config.py loaded and
        validated is indeed one of the two."""
        assert SEQUENCER_BACKEND in ("internal", "indi")

    def test_validate_accepts_known_values(self):
        from rosace.config import _validate_sequencer_backend
        assert _validate_sequencer_backend("internal") == "internal"
        assert _validate_sequencer_backend("indi") == "indi"

    def test_validate_rejects_unknown_value(self):
        from rosace.config import _validate_sequencer_backend
        with pytest.raises(ValueError, match="inte"):
            _validate_sequencer_backend("inte")

    def test_fatal_startup_error_reaches_log_file(self, tmp_path, monkeypatch):
        """Safety net: the message still lands in rosace.log even without configure_logging()."""
        import rosace.config as config_module

        monkeypatch.setattr(config_module, "BASE_DATA_DIR", tmp_path)
        config_module._log_fatal_startup_error("boom de test")

        log_content = (tmp_path / "log" / "rosace.log").read_text()
        assert "boom de test" in log_content
        assert "ERROR" in log_content
        assert "[CONFIG]" in log_content

    def test_fatal_startup_error_never_raises_even_if_logging_broken(self, monkeypatch):
        """Best-effort: an error during the safety-net logging must never
        mask/replace the original error — it's just silently swallowed."""
        import rosace.config as config_module

        monkeypatch.setattr(config_module, "BASE_DATA_DIR", "/chemin/inexistant/impossible")
        config_module._log_fatal_startup_error("ne doit pas lever")  # must not raise


class TestConfigResolution:
    """_resolve_config_path(): ROSACE_CONFIG > <base_data_dir>/conf/config.yaml > packaged default."""

    def test_explicit_rosace_config_env_wins(self, tmp_path, monkeypatch):
        explicit = tmp_path / "explicit.yaml"
        explicit.write_text("base_data_dir: /tmp/whatever\n")
        monkeypatch.setenv("ROSACE_CONFIG", str(explicit))

        import rosace.config as config_module
        assert config_module._resolve_config_path() == explicit

    def test_falls_back_to_base_data_dir_conf_when_present(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ROSACE_CONFIG", raising=False)

        install_dir = tmp_path / "install"
        (install_dir / "conf").mkdir(parents=True)
        real_config = install_dir / "conf" / "config.yaml"
        real_config.write_text(f"base_data_dir: {install_dir}\n")

        boot_file = tmp_path / "base_data_dir.yaml"
        boot_file.write_text(f"base_data_dir: {install_dir}\n")

        import rosace.config as config_module
        monkeypatch.setattr(config_module, "_BASE_DATA_DIR_FILE", boot_file)

        assert config_module._resolve_config_path() == real_config

    def test_falls_back_to_packaged_default_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ROSACE_CONFIG", raising=False)

        boot_file = tmp_path / "base_data_dir.yaml"
        boot_file.write_text(f"base_data_dir: {tmp_path / 'nonexistent_install'}\n")

        import rosace.config as config_module
        monkeypatch.setattr(config_module, "_BASE_DATA_DIR_FILE", boot_file)

        assert config_module._resolve_config_path() == config_module._DEFAULT_CONFIG_PATH


class TestCheckConfigCLI:
    """`python -m rosace.main --check-config [file]` — validates and exits, never starts the server."""

    def test_no_argument_validates_active_config_and_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "rosace.main", "--check-config"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "[OK]" in result.stdout

    def test_missing_file_exits_nonzero_with_clear_message(self, tmp_path):
        missing = tmp_path / "does_not_exist.yaml"
        result = subprocess.run(
            [sys.executable, "-m", "rosace.main", "--check-config", str(missing)],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "[FAIL]" in result.stdout
        assert str(missing) in result.stdout


class TestHelpCLI:
    """`python -m rosace.main --help` / `-h` — prints usage and exits, never starts the server."""

    @pytest.mark.parametrize("flag", ["--help", "-h"])
    def test_prints_usage_and_exits_zero(self, flag):
        result = subprocess.run(
            [sys.executable, "-m", "rosace.main", flag],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--check-config" in result.stdout
        assert "config_path" in result.stdout

    def test_help_takes_priority_over_check_config(self):
        """--help alongside --check-config prints usage rather than validating."""
        result = subprocess.run(
            [sys.executable, "-m", "rosace.main", "--check-config", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Usage:" in result.stdout
        assert "config valid" not in result.stdout  # would appear if --check-config actually ran


# ═════════════════════════════════════════════════════════════
# GROUP 1 — Pydantic models
# ═════════════════════════════════════════════════════════════

class TestOrchestratorStage:
    """State machine stage enumeration."""

    def test_all_stages_present(self):
        """The 9 expected stages are indeed declared."""
        expected = {
            "idle", "init_session", "scheduler", "sequencer",
            "reduction", "sharing", "archive", "close_session", "error",
        }
        actual = {s.value for s in OrchestratorStage}
        assert actual == expected

    def test_stage_is_string_enum(self):
        """OrchestratorStage inherits from str — usable as a string."""
        assert OrchestratorStage.IDLE == "idle"
        assert isinstance(OrchestratorStage.SCHEDULER, str)

    def test_stage_comparison(self):
        assert OrchestratorStage.ERROR != OrchestratorStage.IDLE


class TestTargetModel:
    """Target model (celestial coordinates)."""

    def test_valid_target(self):
        t = Target(name="Vega", ra=279.23, dec=38.78)
        assert t.name == "Vega"
        assert t.ra == 279.23
        assert t.dec == 38.78
        assert t.magnitude_v is None

    def test_target_with_magnitude(self):
        t = Target(name="Sirius", ra=101.28, dec=-16.71, magnitude_v=-1.46)
        assert t.magnitude_v == pytest.approx(-1.46)

    def test_ra_lower_bound(self):
        t = Target(name="Test", ra=0.0, dec=0.0)
        assert t.ra == 0.0

    def test_ra_upper_bound_exclusive(self):
        """ra must be strictly < 360."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Target(name="Bad", ra=360.0, dec=0.0)

    def test_ra_negative_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Target(name="Bad", ra=-1.0, dec=0.0)

    def test_dec_bounds(self):
        Target(name="NP",  ra=0.0, dec=90.0)
        Target(name="SP",  ra=0.0, dec=-90.0)

    def test_dec_out_of_bounds(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Target(name="Bad", ra=0.0, dec=91.0)
        with pytest.raises(ValidationError):
            Target(name="Bad", ra=0.0, dec=-91.0)


class TestExposureConfig:
    """Exposure configuration."""

    def test_defaults(self):
        e = ExposureConfig(exposure_time=300.0)
        assert e.num_exposures == 1
        assert e.binning == 1
        assert e.grating == "R1200"
        assert e.central_wavelength == pytest.approx(6563.0)

    def test_custom_values(self):
        e = ExposureConfig(exposure_time=600.0, num_exposures=3, binning=2, grating="R600")
        assert e.num_exposures == 3
        assert e.binning == 2
        assert e.grating == "R600"

    def test_exposure_time_must_be_positive(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ExposureConfig(exposure_time=0.0)
        with pytest.raises(ValidationError):
            ExposureConfig(exposure_time=-1.0)

    def test_exposure_time_optional(self):
        """exposure_time can be absent — the case where no specific exposure
        time is set in the database for this program/star pair (to be
        computed later from magnitude, not yet implemented)."""
        e = ExposureConfig()
        assert e.exposure_time is None

    def test_binning_bounds(self):
        from pydantic import ValidationError
        ExposureConfig(exposure_time=1.0, binning=4)       # ok
        with pytest.raises(ValidationError):
            ExposureConfig(exposure_time=1.0, binning=5)   # > 4
        with pytest.raises(ValidationError):
            ExposureConfig(exposure_time=1.0, binning=0)   # < 1


class TestObservation:
    """Full observation file (filled in by the stage plugins)."""

    def _make(self, obs_id="abc12345") -> Observation:
        obs = Observation(
            id=obs_id,
            created_at=datetime.now(timezone.utc),
            session_id="sess1234",
        )
        obs.scheduler_data["target"] = Target(name="Altair", ra=297.69, dec=8.87)
        obs.scheduler_data["exposure"] = ExposureConfig(exposure_time=120.0)
        obs.scheduler_data["program"] = "demo"
        return obs

    def test_basic_creation(self):
        obs = self._make()
        assert obs.id == "abc12345"
        assert obs.scheduler_data["target"].name == "Altair"
        assert obs.sequencer_data == {}

    def test_stage_data_is_independent_per_instance(self):
        """Two instances don't share the same <stage>_data casiers."""
        obs1 = self._make("id1")
        obs2 = self._make("id2")
        obs1.sequencer_data["raw_files"] = ["a.fits"]
        assert obs2.sequencer_data == {}

    def test_hardware_defaults_to_empty_dict_and_is_independent_per_instance(self):
        obs1 = self._make("id1")
        obs2 = self._make("id2")
        assert obs1.hardware == {}
        obs1.hardware["mount"] = object()
        assert obs2.hardware == {}

    def test_hardware_excluded_from_model_dump(self):
        obs = self._make()
        obs.hardware["mount"] = object()
        assert "hardware" not in obs.model_dump(mode="json")

    def test_hardware_config_defaults_to_empty_dict_and_is_independent_per_instance(self):
        obs1 = self._make("id1")
        obs2 = self._make("id2")
        assert obs1.hardware_config == {}
        obs1.hardware_config["mount"] = {"slit_x": 0}
        assert obs2.hardware_config == {}

    def test_hardware_config_excluded_from_model_dump(self):
        obs = self._make()
        obs.hardware_config["mount"] = {"slit_x": 0}
        assert "hardware_config" not in obs.model_dump(mode="json")

    def test_suggested_filename_prefers_queried_name_over_canonical(self):
        obs = self._make()
        obs.scheduler_data["queried_name"] = "Altair"  # _make()'s target is Target(name="Altair", ...)
        assert obs.suggested_filename.endswith("_Altair.yaml")

    def test_suggested_filename_uses_queried_name_not_canonical_when_they_differ(self):
        obs = Observation(id="x", created_at=datetime.now(timezone.utc), session_id="sess1234")
        obs.scheduler_data["target"] = Target(name="* bet Lyr", ra=1.0, dec=1.0)
        obs.scheduler_data["queried_name"] = "Sheliak"
        assert obs.suggested_filename.endswith("_Sheliak.yaml")
        assert "bet_Lyr" not in obs.suggested_filename

    def test_suggested_filename_falls_back_to_target_name_when_no_queried_name(self):
        """Should not happen once SCHEDULER has run (both are set together) -- still
        a defensive fallback, same as falling back to "target" when no target at all."""
        obs = self._make()  # sets target, not queried_name
        assert obs.suggested_filename.endswith("_Altair.yaml")

    def test_suggested_filename_falls_back_when_no_target(self):
        obs = Observation(id="x", created_at=datetime.now(timezone.utc), session_id="sess1234")
        assert obs.suggested_filename.endswith("_target.yaml")

    def test_suggested_filename_sanitizes_special_characters(self):
        obs = Observation(id="x", created_at=datetime.now(timezone.utc), session_id="sess1234")
        obs.scheduler_data["target"] = Target(name="V/380 Cyg", ra=1.0, dec=1.0)
        obs.scheduler_data["queried_name"] = "V/380 Cyg"
        assert "/" not in obs.suggested_filename
        assert " " not in obs.suggested_filename

    def test_suggested_filename_reflects_current_scheduler_data(self):
        """The property recomputes on every read — not frozen at creation."""
        obs = Observation(id="x", created_at=datetime.now(timezone.utc), session_id="sess1234")
        assert obs.suggested_filename.endswith("_target.yaml")
        obs.scheduler_data["target"] = Target(name="Deneb", ra=1.0, dec=1.0)
        assert obs.suggested_filename.endswith("_Deneb.yaml")


class TestSessionContext:
    """Session context passed between stages."""

    def test_default_values(self):
        ctx = SessionContext()
        assert ctx.site_name == "St-Pancrasse"
        assert ctx.session_dir is None
        assert len(ctx.session_id) == 8      # UUID[:8]

    def test_session_ids_are_unique(self):
        ids = {SessionContext().session_id for _ in range(50)}
        assert len(ids) == 50                # very high probability

    def test_started_at_is_utc(self):
        ctx = SessionContext()
        assert ctx.started_at.tzinfo is not None

    def test_custom_site(self):
        ctx = SessionContext(site_name="Pic du Midi")
        assert ctx.site_name == "Pic du Midi"

    def test_hardware_defaults_to_empty_dict_and_is_independent_per_instance(self):
        ctx1, ctx2 = SessionContext(), SessionContext()
        assert ctx1.hardware == {}
        ctx1.hardware["mount"] = object()
        assert ctx2.hardware == {}

    def test_hardware_config_defaults_to_empty_dict_and_is_independent_per_instance(self):
        ctx1, ctx2 = SessionContext(), SessionContext()
        assert ctx1.hardware_config == {}
        ctx1.hardware_config["mount"] = {"slit_x": 0}
        assert ctx2.hardware_config == {}


class TestObservationSessionId:
    """Observation.session_id — traceability of the originating session."""

    def test_session_id_is_required(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Observation(id="x", created_at=datetime.now(timezone.utc))  # session_id missing


# ═════════════════════════════════════════════════════════════
# GROUP 2 — Sessions / observations database
# ═════════════════════════════════════════════════════════════

class TestDatabase:
    """Tests for database.py's functions (SQLite via aiosqlite)."""

    @pytest_asyncio.fixture(autouse=True)
    async def init_db(self):
        """Initializes the tables before every test."""
        await db_init()

    def _make_ctx(self) -> SessionContext:
        return SessionContext(site_name="St-Pancrasse")

    def _make_obs(self, session_id: str, created_at: datetime | None = None) -> Observation:
        import uuid
        obs = Observation(
            id=str(uuid.uuid4())[:8],
            created_at=created_at or datetime.now(timezone.utc),
            session_id=session_id,
        )
        obs.scheduler_data["target"] = Target(name="Vega", ra=279.23, dec=38.78)
        obs.scheduler_data["exposure"] = ExposureConfig(exposure_time=300.0)
        obs.scheduler_data["program"] = "demo"
        return obs

    async def test_db_init_idempotent(self):
        """db_init can be called more than once without error."""
        await db_init()
        await db_init()

    async def test_db_init_enables_wal_mode(self):
        """WAL: an external reader (e.g. DB Browser for SQLite) must no
        longer block Rosace's concurrent writes."""
        import aiosqlite
        from rosace.config import DB_PATH

        await db_init()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("PRAGMA journal_mode") as cursor:
                row = await cursor.fetchone()
        assert row[0].lower() == "wal"

    async def test_insert_and_close_session(self):
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        await db_close_session(ctx, status="closed")

    async def test_close_session_error_status(self):
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        await db_close_session(ctx, status="error")

    async def test_insert_observation(self):
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        await db_insert_observation(obs, ctx.session_id)

    async def test_set_yaml_file(self):
        import aiosqlite
        from rosace.database import DB_PATH

        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        await db_insert_observation(obs, ctx.session_id)
        await db_set_yaml_file(obs.id, "obs.yaml")

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT yaml_file FROM observations WHERE id = ?", (obs.id,)) as cursor:
                row = await cursor.fetchone()
        assert row[0] == "obs.yaml"

    async def test_stamp_stages(self):
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        await db_insert_observation(obs, ctx.session_id)
        for stage in (
            OrchestratorStage.SEQUENCER,
            OrchestratorStage.REDUCTION,
            OrchestratorStage.SHARING,
            OrchestratorStage.ARCHIVE,
        ):
            await db_stamp_stage(obs.id, stage)

    async def test_stamp_ignored_stage(self):
        """An unmapped stage (e.g. IDLE) doesn't raise an error."""
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        await db_insert_observation(obs, ctx.session_id)
        await db_stamp_stage(obs.id, OrchestratorStage.IDLE)   # must pass silently

    async def test_set_error(self):
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        await db_insert_observation(obs, ctx.session_id)
        await db_set_error(obs.id, "Timeout séquenceur")

    async def test_insert_observation_scheduler_status_is_done(self):
        """Observation inserted with scheduler_status='done' and scheduler_done timestamp."""
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        await db_insert_observation(obs, ctx.session_id)
        rows = await db_get_recent_observations(limit=1)
        assert rows[0]["stages"]["scheduler"]["status"] == "done"
        assert rows[0]["stages"]["scheduler"]["done_at"] is not None

    async def test_insert_pointing_error(self):
        import aiosqlite
        from rosace.config import DB_PATH

        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        await db_insert_observation(obs, ctx.session_id)

        await db_insert_pointing_error(
            observation_id=obs.id,
            target_name="Vega",
            mount_ra_deg=279.20,
            mount_dec_deg=38.75,
            solved_ra_deg=279.23,
            solved_dec_deg=38.78,
        )

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM pointing_errors WHERE observation_id = ?", (obs.id,)
            ) as cursor:
                rows = await cursor.fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row["observation_id"] == obs.id
        assert row["target_name"] == "Vega"
        assert row["mount_ra_deg"] == pytest.approx(279.20)
        assert row["mount_dec_deg"] == pytest.approx(38.75)
        assert row["solved_ra_deg"] == pytest.approx(279.23)
        assert row["solved_dec_deg"] == pytest.approx(38.78)
        assert row["recorded_at"] is not None

    async def test_insert_pointing_error_allows_multiple_rows_per_observation(self):
        """Successive plate-solve measurements for the same observation each get
        their own row -- this is a history table, not a one-row-per-observation
        summary."""
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        await db_insert_observation(obs, ctx.session_id)

        await db_insert_pointing_error(obs.id, "Vega", 279.20, 38.75, 279.23, 38.78)
        await db_insert_pointing_error(obs.id, "Vega", 279.22, 38.77, 279.23, 38.78)

        import aiosqlite
        from rosace.config import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM pointing_errors WHERE observation_id = ?", (obs.id,)
            ) as cursor:
                row = await cursor.fetchone()
        assert row[0] == 2

    async def test_set_stage_status(self):
        """db_set_stage_status updates the correct column."""
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        await db_insert_observation(obs, ctx.session_id)
        await db_set_stage_status(obs.id, OrchestratorStage.SEQUENCER, "running")
        rows = await db_get_recent_observations(limit=1)
        assert rows[0]["stages"]["sequencer"]["status"] == "running"

    async def test_stamp_stage_sets_done_status_and_timestamp(self):
        """db_stamp_stage records both the timestamp and status='done'."""
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        await db_insert_observation(obs, ctx.session_id)
        await db_stamp_stage(obs.id, OrchestratorStage.SEQUENCER)
        rows = await db_get_recent_observations(limit=1)
        seq = rows[0]["stages"]["sequencer"]
        assert seq["status"] == "done"
        assert seq["done_at"] is not None

    async def test_get_recent_observations_order(self):
        """db_get_recent_observations returns rows newest-first."""
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs1 = self._make_obs(ctx.session_id)
        obs2 = self._make_obs(ctx.session_id)
        await db_insert_observation(obs1, ctx.session_id)
        await db_insert_observation(obs2, ctx.session_id)
        rows = await db_get_recent_observations(limit=2)
        # Both observations are present (order by created_at DESC)
        ids = [r["id"] for r in rows]
        assert obs1.id in ids and obs2.id in ids

    async def test_queried_name_roundtrips(self):
        """queried_name (the name as used, e.g. an alias) is indeed kept
        separately from the canonical name — distinct from target_name."""
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        obs.scheduler_data["queried_name"] = "VegaQueriedNameTest"
        await db_insert_observation(obs, ctx.session_id)

        rows = await db_get_recent_observations(limit=1)
        row = next(r for r in rows if r["id"] == obs.id)
        assert row["queried_name"] == "VegaQueriedNameTest"
        assert row["target_name"] == "Vega"

    async def test_queried_name_none_when_not_set(self):
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)  # doesn't set queried_name
        await db_insert_observation(obs, ctx.session_id)

        rows = await db_get_recent_observations(limit=1)
        row = next(r for r in rows if r["id"] == obs.id)
        assert row["queried_name"] is None

    async def test_priority_roundtrips(self):
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        obs.scheduler_data["priority"] = 2
        await db_insert_observation(obs, ctx.session_id)

        rows = await db_get_recent_observations(limit=1)
        row = next(r for r in rows if r["id"] == obs.id)
        assert row["priority"] == 2

    async def test_priority_none_when_not_set(self):
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)  # doesn't set priority
        await db_insert_observation(obs, ctx.session_id)

        rows = await db_get_recent_observations(limit=1)
        row = next(r for r in rows if r["id"] == obs.id)
        assert row["priority"] is None

    async def test_get_recent_observations_excludes_previous_astronomical_day(self):
        """An observation from two days ago doesn't appear in the current astronomical day's list."""
        ctx = self._make_ctx()
        await db_insert_session(ctx)

        old_obs = self._make_obs(ctx.session_id, created_at=datetime.now(timezone.utc) - timedelta(days=2))
        today_obs = self._make_obs(ctx.session_id)
        await db_insert_observation(old_obs, ctx.session_id)
        await db_insert_observation(today_obs, ctx.session_id)

        rows = await db_get_recent_observations(limit=20)
        ids = [r["id"] for r in rows]
        assert today_obs.id in ids
        assert old_obs.id not in ids

    async def test_count_observations_for_target_zero_when_none(self):
        count = await db_count_observations_for_target("demo", "VegaCountZeroTest")
        assert count == 0

    async def test_count_observations_for_target_counts_matching_pair(self):
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs1 = self._make_obs(ctx.session_id)
        obs2 = self._make_obs(ctx.session_id)
        for obs in (obs1, obs2):
            obs.scheduler_data["target"] = Target(name="VegaCountPairTest", ra=0.0, dec=0.0)
            obs.scheduler_data["program"] = "CountPairProgram"
        await db_insert_observation(obs1, ctx.session_id)
        await db_insert_observation(obs2, ctx.session_id)
        count = await db_count_observations_for_target("CountPairProgram", "VegaCountPairTest")
        assert count == 2

    async def test_count_observations_for_target_scoped_by_program(self):
        """Same star, different program: must not count toward the other program."""
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)  # program="demo"
        await db_insert_observation(obs, ctx.session_id)
        count = await db_count_observations_for_target("another_program", "Vega")
        assert count == 0

    async def test_count_observations_for_target_persists_across_sessions(self):
        """The counter is scoped by astronomical night, not by session_id —
        stopping/restarting a session must not reset it to zero."""
        obs1_ctx = self._make_ctx()
        await db_insert_session(obs1_ctx)
        obs1 = self._make_obs(obs1_ctx.session_id)
        obs1.scheduler_data["target"] = Target(name="VegaCrossSessionTest", ra=0.0, dec=0.0)
        obs1.scheduler_data["program"] = "CrossSessionProgram"
        await db_insert_observation(obs1, obs1_ctx.session_id)

        obs2_ctx = self._make_ctx()  # new session, same night
        await db_insert_session(obs2_ctx)
        obs2 = self._make_obs(obs2_ctx.session_id)
        obs2.scheduler_data["target"] = Target(name="VegaCrossSessionTest", ra=0.0, dec=0.0)
        obs2.scheduler_data["program"] = "CrossSessionProgram"
        await db_insert_observation(obs2, obs2_ctx.session_id)

        count = await db_count_observations_for_target("CrossSessionProgram", "VegaCrossSessionTest")
        assert count == 2

    # test_astronomical_day_start_utc_spans_local_noon moved to test_time_helpers.py
    # (astronomical_day_start_utc now lives in time_helpers.py, shared with init_session.py)

# ═════════════════════════════════════════════════════════════
# GROUP 3 — Targets database (targets.py)
# ═════════════════════════════════════════════════════════════

class TestTargetsDB:
    """CRUD tests for stars and programs (targets.py)."""

    @pytest_asyncio.fixture(autouse=True)
    async def init_db(self):
        await targets_db_init()

    def _star(self, name="Altair", ra=297.69, dec=8.87) -> StarCreate:
        return StarCreate(name=name, ra=ra, dec=dec, magnitude_v=0.76,
                          spectral_type="A7V")

    def _program(self, name="Programme_A") -> ProgramCreate:
        return ProgramCreate(name=name, description="Test programme")

    async def test_targets_db_init_enables_wal_mode(self):
        """WAL: an external reader (e.g. DB Browser for SQLite) must no
        longer block Rosace's concurrent writes."""
        import aiosqlite
        from rosace.targets import TARGETS_DB_PATH

        async with aiosqlite.connect(TARGETS_DB_PATH) as db:
            async with db.execute("PRAGMA journal_mode") as cursor:
                row = await cursor.fetchone()
        assert row[0].lower() == "wal"

    # ── Stars ──────────────────────────────────────────────

    async def test_star_insert_and_get(self):
        sid = await star_insert(self._star("Deneb"))
        star = await star_get(sid)
        assert star is not None
        assert star.name == "Deneb"
        assert star.id == sid

    async def test_star_get_nonexistent(self):
        star = await star_get(999999)
        assert star is None

    async def test_star_get_by_name(self):
        await star_insert(self._star("Fomalhaut"))
        star = await star_get_by_name("Fomalhaut")
        assert star is not None
        assert star.name == "Fomalhaut"

    async def test_star_get_by_name_missing(self):
        star = await star_get_by_name("PasDansLaBase")
        assert star is None

    async def test_star_list(self):
        await star_insert(self._star("Alpha1"))
        await star_insert(self._star("Alpha2", ra=10.0, dec=10.0))
        stars = await star_list()
        names = [s.name for s in stars]
        assert "Alpha1" in names
        assert "Alpha2" in names

    async def test_star_update(self):
        sid = await star_insert(self._star("OldName"))
        updated = StarCreate(name="NewName", ra=100.0, dec=20.0)
        await star_update(sid, updated)
        star = await star_get(sid)
        assert star.name == "NewName"
        assert star.ra == pytest.approx(100.0)

    async def test_star_delete(self):
        sid = await star_insert(self._star("ToDelete"))
        await star_delete(sid)
        assert await star_get(sid) is None

    async def test_star_fields_roundtrip(self):
        """Every optional field survives a round trip through the database."""
        sc = StarCreate(name="Rigel", ra=78.63, dec=-8.20,
                        magnitude_v=0.13, spectral_type="B8Ia",
                        notes="Belle étoile")
        sid = await star_insert(sc)
        star = await star_get(sid)
        assert star.magnitude_v   == pytest.approx(0.13)
        assert star.spectral_type == "B8Ia"
        assert star.notes         == "Belle étoile"

    # ── Aliases ────────────────────────────────────────────────

    async def test_star_get_by_name_falls_back_to_alias(self):
        sid = await star_insert(self._star("AlphaLyrTest1"))
        await star_add_alias(sid, "VegaAliasTest1")
        star = await star_get_by_name("VegaAliasTest1")
        assert star is not None
        assert star.id == sid
        assert star.name == "AlphaLyrTest1"  # canonical name, not the alias

    async def test_star_get_by_name_prefers_canonical_over_alias(self):
        """If the searched term is both a star's canonical name AND another
        star's alias, the canonical name wins (two-level lookup)."""
        sid1 = await star_insert(self._star("PreferCanonicalTestName", ra=1.0, dec=1.0))
        sid2 = await star_insert(self._star("AutreEtoileTest2", ra=10.0, dec=10.0))
        await star_add_alias(sid2, "PreferCanonicalTestName-alias-piege")
        star = await star_get_by_name("PreferCanonicalTestName")
        assert star.id == sid1

    async def test_star_list_aliases(self):
        sid = await star_insert(self._star("AlphaLyrTest3"))
        await star_add_alias(sid, "VegaAliasTest3")
        await star_add_alias(sid, "HD172167Test3")
        aliases = await star_list_aliases(sid)
        assert set(aliases) == {"VegaAliasTest3", "HD172167Test3"}

    async def test_star_remove_alias(self):
        sid = await star_insert(self._star("AlphaLyrTest4"))
        alias_id = await star_add_alias(sid, "VegaAliasTest4")
        await star_remove_alias(alias_id)
        assert await star_list_aliases(sid) == []
        assert await star_get_by_name("VegaAliasTest4") is None

    async def test_delete_star_cleans_aliases(self):
        sid = await star_insert(self._star("AlphaLyrTest5"))
        await star_add_alias(sid, "VegaAliasTest5")
        await star_delete(sid)
        assert await star_get_by_name("VegaAliasTest5") is None

    # ── Programs ───────────────────────────────────────────

    async def test_program_insert_and_get(self):
        pid = await program_insert(self._program("Prog_X"))
        prog = await program_get(pid)
        assert prog is not None
        assert prog.name == "Prog_X"

    async def test_program_get_nonexistent(self):
        prog = await program_get(999999)
        assert prog is None

    async def test_program_get_by_name(self):
        await program_insert(self._program("Prog_ByName"))
        prog = await program_get_by_name("Prog_ByName")
        assert prog is not None
        assert prog.name == "Prog_ByName"

    async def test_program_get_by_name_missing(self):
        assert await program_get_by_name("PasDansLaBase") is None

    async def test_program_list(self):
        await program_insert(self._program("ListA"))
        await program_insert(self._program("ListB"))
        progs = await program_list()
        names = [p.name for p in progs]
        assert "ListA" in names
        assert "ListB" in names

    async def test_program_delete(self):
        pid = await program_insert(self._program("ToDelete"))
        await program_delete(pid)
        assert await program_get(pid) is None

    # ── Program ↔ star links ─────────────────────────────────

    async def test_program_add_and_get_stars(self):
        sid1 = await star_insert(self._star("S1", ra=10.0, dec=10.0))
        sid2 = await star_insert(self._star("S2", ra=20.0, dec=20.0))
        pid  = await program_insert(self._program("P_Lien"))
        await program_add_star(pid, sid1)
        await program_add_star(pid, sid2)
        stars = await program_get_stars(pid)
        assert len(stars) == 2
        assert {s.id for s in stars} == {sid1, sid2}

    async def test_program_add_star_idempotent(self):
        """Adding the same star twice doesn't create a duplicate."""
        sid = await star_insert(self._star("SIdem", ra=30.0, dec=5.0))
        pid = await program_insert(self._program("P_Idem"))
        await program_add_star(pid, sid)
        await program_add_star(pid, sid)
        stars = await program_get_stars(pid)
        assert len(stars) == 1

    async def test_program_add_star_with_exposure_time(self):
        sid = await star_insert(self._star("SExp", ra=35.0, dec=5.0))
        pid = await program_insert(self._program("P_Exp"))
        await program_add_star(pid, sid, exposure_time=90.0)
        stars = await program_get_stars(pid)
        assert isinstance(stars[0], StarInProgram)
        assert stars[0].exposure_time == pytest.approx(90.0)

    async def test_program_add_star_without_exposure_time_is_none(self):
        sid = await star_insert(self._star("SNoExp", ra=36.0, dec=5.0))
        pid = await program_insert(self._program("P_NoExp"))
        await program_add_star(pid, sid)
        stars = await program_get_stars(pid)
        assert stars[0].exposure_time is None

    async def test_program_add_star_upserts_exposure_time(self):
        """A second call on the same program/star pair updates the exposure time."""
        sid = await star_insert(self._star("SUpsert", ra=37.0, dec=5.0))
        pid = await program_insert(self._program("P_Upsert"))
        await program_add_star(pid, sid, exposure_time=60.0)
        await program_add_star(pid, sid, exposure_time=120.0)
        stars = await program_get_stars(pid)
        assert len(stars) == 1
        assert stars[0].exposure_time == pytest.approx(120.0)

    async def test_program_remove_star(self):
        sid = await star_insert(self._star("SRemove", ra=40.0, dec=5.0))
        pid = await program_insert(self._program("P_Remove"))
        await program_add_star(pid, sid)
        await program_remove_star(pid, sid)
        stars = await program_get_stars(pid)
        assert stars == []

    async def test_star_get_programs(self):
        sid = await star_insert(self._star("SMultiProg", ra=50.0, dec=5.0))
        pid1 = await program_insert(self._program("PA"))
        pid2 = await program_insert(self._program("PB"))
        await program_add_star(pid1, sid)
        await program_add_star(pid2, sid)
        progs = await star_get_programs(sid)
        assert {p.id for p in progs} == {pid1, pid2}

    async def test_delete_star_cleans_program_links(self):
        """Deleting a star also removes its program_stars entries."""
        sid = await star_insert(self._star("SClean", ra=60.0, dec=5.0))
        pid = await program_insert(self._program("P_Clean"))
        await program_add_star(pid, sid)
        await star_delete(sid)
        stars = await program_get_stars(pid)
        assert stars == []

    async def test_delete_program_cleans_star_links(self):
        """Deleting a program also removes its program_stars entries."""
        sid = await star_insert(self._star("SPClean", ra=70.0, dec=5.0))
        pid = await program_insert(self._program("PP_Clean"))
        await program_add_star(pid, sid)
        await program_delete(pid)
        progs = await star_get_programs(sid)
        assert progs == []


# ═════════════════════════════════════════════════════════════
# GROUP 4 — Simbad module (mocked queries)
# ═════════════════════════════════════════════════════════════

# Simulated TAP JSON response (Simbad format)
SIMBAD_RESPONSE_VEGA = {
    "metadata": [
        {"name": "main_id"},
        {"name": "ra"},
        {"name": "dec"},
        {"name": "sp_type"},
        {"name": "vmag"},
    ],
    "data": [
        ["* alf Lyr", 279.23473, 38.78369, "A0Va", 0.03],
    ],
}

SIMBAD_RESPONSE_EMPTY = {"metadata": [], "data": []}

SIMBAD_RESPONSE_NO_MAG = {
    "metadata": [
        {"name": "main_id"},
        {"name": "ra"},
        {"name": "dec"},
        {"name": "sp_type"},
        {"name": "vmag"},
    ],
    "data": [
        ["* alf CMa", 101.287, -16.716, "A1Vm", None],
    ],
}


def _mock_httpx_response(payload: dict, status_code: int = 200):
    """Builds a mock httpx response."""
    import json as _json
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


class TestSimbad:
    """Tests for the simbad.py module (mocked network requests)."""

    async def test_simbad_found(self):
        """Returns a correctly populated StarCreate."""
        mock_resp = _mock_httpx_response(SIMBAD_RESPONSE_VEGA)

        async def _fake_get(url, params):
            return mock_resp

        with patch("rosace.simbad.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__  = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await simbad_search("Vega")

        assert result.name == "* alf Lyr"
        assert result.ra   == pytest.approx(279.23473)
        assert result.dec  == pytest.approx(38.78369)
        assert result.spectral_type == "A0Va"
        assert result.magnitude_v == pytest.approx(0.03)

    async def test_simbad_not_found(self):
        """Raises SimbadNotFoundError if data is empty."""
        mock_resp = _mock_httpx_response(SIMBAD_RESPONSE_EMPTY)

        with patch("rosace.simbad.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__  = AsyncMock(return_value=False)
            MockClient.return_value = instance

            with pytest.raises(SimbadNotFoundError):
                await simbad_search("PasUnEtoile")

    async def test_simbad_magnitude_none(self):
        """magnitude_v is None when vmag is NULL in Simbad."""
        mock_resp = _mock_httpx_response(SIMBAD_RESPONSE_NO_MAG)

        with patch("rosace.simbad.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__  = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await simbad_search("Sirius")

        assert result.magnitude_v is None

    async def test_simbad_network_error(self):
        """Raises SimbadQueryError on a network error."""
        import httpx

        with patch("rosace.simbad.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=httpx.RequestError("timeout"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__  = AsyncMock(return_value=False)
            MockClient.return_value = instance

            with pytest.raises(SimbadQueryError):
                await simbad_search("Vega")

    async def test_simbad_http_error(self):
        """Raises SimbadQueryError on a 5xx HTTP error."""
        import httpx

        with patch("rosace.simbad.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 503
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "503", request=MagicMock(), response=mock_resp
            )
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__  = AsyncMock(return_value=False)
            MockClient.return_value = instance

            with pytest.raises(SimbadQueryError):
                await simbad_search("Vega")

    async def test_simbad_sql_injection_escape(self):
        """
        A name containing an apostrophe is correctly escaped
        (no crash while building the ADQL query).
        """
        mock_resp = _mock_httpx_response(SIMBAD_RESPONSE_EMPTY)

        with patch("rosace.simbad.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__  = AsyncMock(return_value=False)
            MockClient.return_value = instance

            with pytest.raises(SimbadNotFoundError):
                await simbad_search("L'Etoile d'Argent")   # apostrophe


# ═════════════════════════════════════════════════════════════
# GROUP 5 — Cross-consistency between models / state machine
# ═════════════════════════════════════════════════════════════

class TestSystemState:
    """
    Checks SystemState's consistency (imported from main)
    without starting the FastAPI application.
    """

    def test_system_state_initial(self):
        from rosace.main import SystemState
        s = SystemState()
        assert s.stage            == OrchestratorStage.IDLE
        assert s.is_running       is False
        assert s.completed        == []
        assert s.pending_post_obs == 0

    def test_system_state_transition(self):
        from rosace.main import SystemState
        s = SystemState()
        s.stage      = OrchestratorStage.SCHEDULER
        s.is_running = True
        assert s.stage == OrchestratorStage.SCHEDULER
        assert s.is_running is True

    def test_obs_stages_order(self):
        """Foreground stages: SCHEDULER then SEQUENCER only."""
        from rosace.main import OBS_STAGES
        assert OBS_STAGES == [
            OrchestratorStage.SCHEDULER,
            OrchestratorStage.SEQUENCER,
        ]

    def test_post_obs_stages_order(self):
        """Background post-obs stages: REDUCTION → SHARING → ARCHIVE."""
        from rosace.main import POST_OBS_STAGES
        assert POST_OBS_STAGES == [
            OrchestratorStage.REDUCTION,
            OrchestratorStage.SHARING,
            OrchestratorStage.ARCHIVE,
        ]


# ═════════════════════════════════════════════════════════════
# GROUP 6 — Orchestrated state machine (session_runner)
# ═════════════════════════════════════════════════════════════

class TestSessionRunner:
    """
    Tests main.py's session_runner() loop with fake plugins
    (monkeypatching rosace.main.plugins) to precisely control the
    sequence of events. A "clean stop" is simulated simply by setting
    state.is_running to False from a fake plugin — no need for a
    dedicated exception, the loop already checks is_running before every stage.
    """

    @pytest.fixture(autouse=True)
    def reset_state(self):
        """Isolates every test: clean state before and after."""
        import rosace.main as main_mod
        main_mod.state.is_running = False
        main_mod.state.session_active = False
        main_mod.state.stage = OrchestratorStage.IDLE
        yield
        main_mod.state.is_running = False
        main_mod.state.session_active = False
        main_mod.state.stage = OrchestratorStage.IDLE

    async def test_clean_stop_calls_close_session(self, monkeypatch):
        """is_running=False during SCHEDULER still triggers CLOSE_SESSION then IDLE."""
        import rosace.main as main_mod

        close_called = []

        class FakeInit:
            async def run(self, ctx):
                Path("/tmp/fake_session_dir").mkdir(parents=True, exist_ok=True)
                ctx.session_dir = "/tmp/fake_session_dir"
                return ctx

        class FakeScheduler:
            async def run(self, obs):
                main_mod.state.is_running = False  # simulates a concurrent /control/stop
                obs.scheduler_data["target"] = Target(name="TestStar", ra=0.0, dec=0.0)
                return obs

        class FakeClose:
            async def run(self, ctx):
                close_called.append(True)
                return ctx

        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.INIT_SESSION, FakeInit())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SCHEDULER, FakeScheduler())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.CLOSE_SESSION, FakeClose())

        main_mod.state.is_running = True
        await main_mod.session_runner()

        assert close_called == [True]
        assert main_mod.state.stage == OrchestratorStage.IDLE

    async def test_yaml_file_recorded_in_db_is_basename_not_full_path(self, monkeypatch):
        """Regression: main.py used to pass create_observation_file()'s full Path
        return value to db_set_yaml_file() instead of obs.yaml_file (already just
        the basename) -- silently stored the full filesystem path in rosace.db."""
        import aiosqlite
        import rosace.main as main_mod
        from rosace.database import DB_PATH

        captured: list[Observation] = []

        class FakeInit:
            async def run(self, ctx):
                Path("/tmp/fake_session_dir").mkdir(parents=True, exist_ok=True)
                ctx.session_dir = "/tmp/fake_session_dir"
                return ctx

        class FakeScheduler:
            async def run(self, obs):
                obs.scheduler_data["target"] = Target(name="YamlFileTestStar", ra=0.0, dec=0.0)
                obs.scheduler_data["queried_name"] = "YamlFileTestStar"
                obs.scheduler_data["program"] = "test_program"
                captured.append(obs)
                main_mod.state.is_running = False
                return obs

        class FakeClose:
            async def run(self, ctx): return ctx

        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.INIT_SESSION, FakeInit())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SCHEDULER, FakeScheduler())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.CLOSE_SESSION, FakeClose())

        main_mod.state.is_running = True
        await main_mod.session_runner()

        obs = captured[0]
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT yaml_file FROM observations WHERE id = ?", (obs.id,)) as cursor:
                row = await cursor.fetchone()

        assert row[0] == obs.yaml_file
        assert "/" not in row[0]

    async def test_scheduler_nothing_to_observe_retries_without_error(self, monkeypatch):
        """target=None is not an error: no db_set_error, just a new attempt."""
        import rosace.main as main_mod

        scheduler_calls = []

        class FakeInit:
            async def run(self, ctx):
                Path("/tmp/fake_session_dir").mkdir(parents=True, exist_ok=True)
                ctx.session_dir = "/tmp/fake_session_dir"
                return ctx

        class FakeScheduler:
            async def run(self, obs):
                scheduler_calls.append(1)
                if len(scheduler_calls) >= 2:
                    main_mod.state.is_running = False
                    return obs  # target stays None -> nothing left to do, stop cleanly
                return obs  # target=None: "nothing to observe" on the first call

        class FakeClose:
            async def run(self, ctx): return ctx

        monkeypatch.setattr(main_mod, "SCHEDULER_RETRY_SECONDS", 0)
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.INIT_SESSION, FakeInit())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SCHEDULER, FakeScheduler())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.CLOSE_SESSION, FakeClose())

        main_mod.state.is_running = True
        await main_mod.session_runner()

        assert len(scheduler_calls) == 2   # SCHEDULER asked again, no error recorded
        assert main_mod.state.completed == []

    async def test_interruptible_sleep_stops_early_when_is_running_false(self, monkeypatch):
        """A stop during the wait ends it well before the full delay (not just right after)."""
        import rosace.main as main_mod

        monkeypatch.setattr(main_mod, "SLEEP_STEP_SECONDS", 0.05)
        main_mod.state.is_running = True

        async def stop_soon():
            await asyncio.sleep(0.12)
            main_mod.state.is_running = False

        start = time.monotonic()
        await asyncio.gather(main_mod._interruptible_sleep(5.0), stop_soon())
        elapsed = time.monotonic() - start

        assert elapsed < 0.5   # well under the requested 5s

    async def test_stop_during_scheduler_retry_wait_is_honored_promptly(self, monkeypatch):
        """A STOP clicked during the 'nothing to observe' wait interrupts the
        session without waiting out the full SCHEDULER_RETRY_SECONDS."""
        import rosace.main as main_mod

        close_called = []

        class FakeInit:
            async def run(self, ctx):
                Path("/tmp/fake_session_dir").mkdir(parents=True, exist_ok=True)
                ctx.session_dir = "/tmp/fake_session_dir"
                return ctx

        class FakeScheduler:
            async def run(self, obs):
                return obs  # target=None on every call: never anything to observe

        class FakeClose:
            async def run(self, ctx):
                close_called.append(True)
                return ctx

        monkeypatch.setattr(main_mod, "SCHEDULER_RETRY_SECONDS", 5.0)
        monkeypatch.setattr(main_mod, "SLEEP_STEP_SECONDS", 0.05)
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.INIT_SESSION, FakeInit())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SCHEDULER, FakeScheduler())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.CLOSE_SESSION, FakeClose())

        main_mod.state.is_running = True

        async def click_stop_soon():
            await asyncio.sleep(0.12)
            main_mod.state.is_running = False  # simulates POST /control/stop

        start = time.monotonic()
        await asyncio.gather(main_mod.session_runner(), click_stop_soon())
        elapsed = time.monotonic() - start

        assert close_called == [True]
        assert elapsed < 1.0   # well under SCHEDULER_RETRY_SECONDS' 5s

    async def test_error_restarts_from_scheduler_not_init(self, monkeypatch):
        """An error in SEQUENCER restarts from SCHEDULER, not from INIT_SESSION."""
        import rosace.main as main_mod

        init_calls = []
        scheduler_calls = []

        class FakeInit:
            async def run(self, ctx):
                init_calls.append(1)
                Path("/tmp/fake_session_dir").mkdir(parents=True, exist_ok=True)
                ctx.session_dir = "/tmp/fake_session_dir"
                return ctx

        class FakeScheduler:
            async def run(self, ctx):
                scheduler_calls.append(1)
                if len(scheduler_calls) >= 2:
                    main_mod.state.is_running = False
                    return ctx
                ctx.scheduler_data["target"] = Target(name="TestStar", ra=0.0, dec=0.0)
                return ctx

        class FakeSequencer:
            async def run(self, ctx):
                raise RuntimeError("boom")

        class FakeClose:
            async def run(self, ctx): return ctx

        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.INIT_SESSION, FakeInit())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SCHEDULER, FakeScheduler())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SEQUENCER, FakeSequencer())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.CLOSE_SESSION, FakeClose())

        main_mod.state.is_running = True
        await main_mod.session_runner()

        assert init_calls == [1]           # INIT_SESSION never restarted
        assert len(scheduler_calls) == 2   # SCHEDULER restarted after the error

    async def test_scheduler_fatal_error_stops_session_without_looping(self, monkeypatch):
        """SchedulerFatalError (invalid config/data) stops the session
        immediately, unlike an ordinary error which loops back and restarts."""
        import rosace.main as main_mod
        from rosace.plugins.schedulers import SchedulerFatalError

        scheduler_calls = []

        class FakeInit:
            async def run(self, ctx):
                Path("/tmp/fake_session_dir_fatal").mkdir(parents=True, exist_ok=True)
                ctx.session_dir = "/tmp/fake_session_dir_fatal"
                return ctx

        class FakeScheduler:
            async def run(self, ctx):
                scheduler_calls.append(1)
                raise SchedulerFatalError("ObservationList.txt introuvable")

        class FakeClose:
            async def run(self, ctx): return ctx

        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.INIT_SESSION, FakeInit())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SCHEDULER, FakeScheduler())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.CLOSE_SESSION, FakeClose())

        main_mod.state.is_running = True
        await main_mod.session_runner()

        assert len(scheduler_calls) == 1   # never restarted
        assert main_mod.state.is_running is False

    async def test_close_session_error_still_reaches_idle(self, monkeypatch):
        """If CLOSE_SESSION raises an exception, the state still goes back to IDLE."""
        import rosace.main as main_mod

        class FakeInit:
            async def run(self, ctx):
                Path("/tmp/fake_session_dir").mkdir(parents=True, exist_ok=True)
                ctx.session_dir = "/tmp/fake_session_dir"
                return ctx

        class FakeScheduler:
            async def run(self, obs):
                main_mod.state.is_running = False
                obs.scheduler_data["target"] = Target(name="TestStar", ra=0.0, dec=0.0)
                return obs

        class FakeClose:
            async def run(self, ctx):
                raise RuntimeError("close failed")

        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.INIT_SESSION, FakeInit())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SCHEDULER, FakeScheduler())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.CLOSE_SESSION, FakeClose())

        main_mod.state.is_running = True
        await main_mod.session_runner()

        assert main_mod.state.stage == OrchestratorStage.IDLE

    async def test_init_session_failure_prevents_loop(self, monkeypatch):
        """A failure in INIT_SESSION prevents the SCHEDULER loop from starting."""
        import rosace.main as main_mod

        scheduler_called = []

        class FakeInit:
            async def run(self, ctx):
                raise RuntimeError("init failed")

        class FakeScheduler:
            async def run(self, ctx):
                scheduler_called.append(1)
                return ctx

        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.INIT_SESSION, FakeInit())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SCHEDULER, FakeScheduler())

        main_mod.state.is_running = True
        await main_mod.session_runner()

        assert scheduler_called == []
        assert main_mod.state.is_running is False
        assert main_mod.state.stage == OrchestratorStage.ERROR
        assert main_mod.state.session_active is False  # even on this early exit

    async def test_session_active_true_during_run_false_after(self, monkeypatch):
        """session_active must be true for the whole duration of the call, and
        become false again on exit — regardless of the exit path."""
        import rosace.main as main_mod

        active_during_run = []

        class FakeInit:
            async def run(self, ctx):
                active_during_run.append(main_mod.state.session_active)
                Path("/tmp/fake_session_dir_active").mkdir(parents=True, exist_ok=True)
                ctx.session_dir = "/tmp/fake_session_dir_active"
                return ctx

        class FakeScheduler:
            async def run(self, ctx):
                main_mod.state.is_running = False  # simulates an immediate /control/stop
                return ctx

        class FakeClose:
            async def run(self, ctx): return ctx

        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.INIT_SESSION, FakeInit())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SCHEDULER, FakeScheduler())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.CLOSE_SESSION, FakeClose())

        assert main_mod.state.session_active is False
        main_mod.state.is_running = True
        await main_mod.session_runner()

        assert active_during_run == [True]
        assert main_mod.state.session_active is False

    async def test_stop_check_reflects_is_running(self, monkeypatch, tmp_path):
        """Every freshly-created Observation gets a stop_check() that reflects
        state.is_running live — used by sub-plugins whose work spans several
        internal steps, to interrupt themselves between two of their own steps."""
        import rosace.main as main_mod

        captured_stop_check = []

        class FakeInit:
            async def run(self, ctx):
                ctx.session_dir = str(tmp_path)
                return ctx

        class FakeScheduler:
            async def run(self, obs):
                captured_stop_check.append(obs.stop_check)
                main_mod.state.is_running = False
                return obs

        class FakeClose:
            async def run(self, ctx): return ctx

        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.INIT_SESSION, FakeInit())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SCHEDULER, FakeScheduler())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.CLOSE_SESSION, FakeClose())

        main_mod.state.is_running = True
        await main_mod.session_runner()

        assert len(captured_stop_check) == 1
        stop_check = captured_stop_check[0]
        assert callable(stop_check)
        assert stop_check() is True  # state.is_running was flipped to False by FakeScheduler

    async def test_live_hardware_is_not_deep_copied_into_post_obs_task(self, monkeypatch, tmp_path):
        """Regression: model_copy(deep=True, update={"hardware": {}}) still tried
        to deep-copy obs.hardware before applying update — copy.deepcopy() on a
        non-copyable object (e.g. a threading.Lock, like in a real INDI adapter)
        raised an exception uncaught in the for...else block, which silently
        killed the whole session_runner() loop (state.is_running stayed stuck
        True forever). This test reproduces the exact ingredient (non-copyable
        hardware) and checks that the session does reach CLOSE_SESSION, and
        that REDUCTION is indeed reached in the background."""
        import threading
        import rosace.main as main_mod

        reduction_calls = []
        close_called = []

        class FakeInit:
            async def run(self, ctx):
                ctx.session_dir = str(tmp_path)
                return ctx

        class FakeScheduler:
            async def run(self, obs):
                obs.scheduler_data["target"] = Target(name="TestStar", ra=0.0, dec=0.0)
                return obs

        class FakeSequencer:
            async def run(self, obs):
                obs.hardware = {"mount": threading.Lock()}  # not deep-copyable
                main_mod.state.is_running = False  # one observation is enough for this test
                return obs

        class FakeReduction:
            async def run(self, obs):
                reduction_calls.append(obs.id)
                return obs

        class FakeClose:
            async def run(self, ctx):
                close_called.append(True)
                return ctx

        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.INIT_SESSION, FakeInit())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SCHEDULER, FakeScheduler())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.SEQUENCER, FakeSequencer())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.REDUCTION, FakeReduction())
        monkeypatch.setitem(main_mod.plugins, OrchestratorStage.CLOSE_SESSION, FakeClose())

        main_mod.state.is_running = True
        await main_mod.session_runner()

        assert len(main_mod.state.completed) == 1
        assert reduction_calls == [main_mod.state.completed[0].id]
        assert close_called == [True]
        assert main_mod.state.is_running is False  # not stuck True


# ═════════════════════════════════════════════════════════════
# GROUP 7 — FastAPI endpoints (TestClient, real lifespan)
# ═════════════════════════════════════════════════════════════

class TestApiEndpoints:
    """Tests for the HTTP layer (never exercised directly by the other groups)."""

    @pytest.fixture(autouse=True)
    def reset_state(self):
        import rosace.main as main_mod
        main_mod.state.is_running = False
        main_mod.state.session_active = False
        main_mod.state.stage = OrchestratorStage.IDLE
        yield
        main_mod.state.is_running = False
        main_mod.state.session_active = False
        main_mod.state.stage = OrchestratorStage.IDLE

    _TEST_ADMIN_USER = "pytest_admin"
    _TEST_ADMIN_PASS = "pytest_password"

    def _ensure_test_admin(self):
        """Provisions a fixed admin account directly against the DB (bypassing
        HTTP/lifespan — the tmp rosace.db is session-scoped, shared across every
        test in this class, so a second call just hits the already-exists case)."""
        import asyncio
        from rosace.auth import auth_db_init, user_create, UserCreate

        async def _ensure():
            await auth_db_init()
            try:
                await user_create(UserCreate(
                    username=self._TEST_ADMIN_USER, password=self._TEST_ADMIN_PASS, is_admin=True,
                ))
            except ValueError:
                pass  # already provisioned by an earlier test

        asyncio.run(_ensure())

    def _client(self):
        """A TestClient already logged in as a fixed test admin — every existing
        test in this class was written before auth existed and expects an
        authenticated client with zero per-test changes; see _anon_client() for
        the (few) tests that specifically want to exercise the unauthenticated path."""
        from fastapi.testclient import TestClient
        from rosace.main import app

        self._ensure_test_admin()
        client = TestClient(app)
        client.post("/auth/login", json={"username": self._TEST_ADMIN_USER, "password": self._TEST_ADMIN_PASS})
        return client

    def _anon_client(self):
        from fastapi.testclient import TestClient
        from rosace.main import app
        return TestClient(app)

    def test_status_endpoint_shape(self):
        with self._client() as client:
            res = client.get("/status")
        assert res.status_code == 200
        data = res.json()
        assert set(data.keys()) == {
            "stage", "is_running", "session_active", "completed_count", "pending_post_obs_count",
        }

    def test_control_start_when_idle(self, monkeypatch):
        """Starts the session; session_runner is mocked so it doesn't loop forever."""
        import rosace.main as main_mod

        async def fake_runner():
            pass

        monkeypatch.setattr(main_mod, "session_runner", fake_runner)

        with self._client() as client:
            res = client.post("/control/start")

        assert res.status_code == 200
        assert "démarrée" in res.json()["message"].lower()

    def test_control_start_when_already_running(self):
        """Doesn't restart an already-running session (no background task added)."""
        import rosace.main as main_mod

        with self._client() as client:
            main_mod.state.session_active = True
            res = client.post("/control/start")

        assert res.status_code == 200
        assert "déjà" in res.json()["message"].lower()

    def test_control_start_refused_while_previous_session_still_winding_down(self):
        """is_running=False (stop requested) isn't enough to allow a new start
        as long as the previous session hasn't finished its cleanup."""
        import rosace.main as main_mod

        with self._client() as client:
            main_mod.state.is_running = False       # stop already requested...
            main_mod.state.session_active = True     # ...but cleanup is still running
            res = client.post("/control/start")

        assert res.status_code == 200
        assert "déjà" in res.json()["message"].lower()

    def test_control_stop_sets_is_running_false(self):
        import rosace.main as main_mod

        with self._client() as client:
            main_mod.state.is_running = True
            res = client.post("/control/stop")

        assert res.status_code == 200
        assert main_mod.state.is_running is False

    def test_get_star_404_when_missing(self):
        with self._client() as client:
            res = client.get("/stars/PasDansLaBase")
        assert res.status_code == 404

    def test_get_star_via_api(self):
        """POST /stars doesn't exist (all creation goes through Simbad) — the
        star is inserted directly into the database, the API is only exercised
        on the read side."""
        from rosace.targets import StarCreate, star_insert

        async def _insert():
            return await star_insert(StarCreate(name="ApiTestStar", ra=100.0, dec=20.0))

        star_id = asyncio.run(_insert())

        with self._client() as client:
            get_res = client.get("/stars/ApiTestStar")

        assert get_res.status_code == 200
        body = get_res.json()
        assert body["id"] == star_id
        assert body["name"] == "ApiTestStar"
        assert body["queried_name"] == "ApiTestStar"

    def test_simbad_import_returns_existing_on_duplicate(self, monkeypatch):
        """A second import of the same name doesn't create a duplicate: returns
        the existing star (200), no error."""
        import rosace.main as main_mod
        from rosace.targets import StarCreate

        async def fake_simbad_search(name):
            return StarCreate(name="DupStar", ra=10.0, dec=10.0)

        monkeypatch.setattr(main_mod, "simbad_search", fake_simbad_search)

        with self._client() as client:
            first = client.post("/simbad/import", params={"name": "DupStar"})
            assert first.status_code == 201
            second = client.post("/simbad/import", params={"name": "DupStar"})

        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]

    def test_simbad_import_registers_search_term_as_alias(self, monkeypatch):
        """If the searched name differs from the canonical name Simbad returns,
        it's registered as an alias — and then becomes directly searchable."""
        import rosace.main as main_mod
        from rosace.targets import StarCreate

        async def fake_simbad_search(name):
            return StarCreate(name="alf Lyr ApiAliasTest", ra=279.23, dec=38.78)

        monkeypatch.setattr(main_mod, "simbad_search", fake_simbad_search)

        with self._client() as client:
            res = client.post("/simbad/import", params={"name": "Vega"})
            assert res.status_code == 201

            by_alias = client.get("/stars/Vega")
            by_canonical = client.get("/stars/alf Lyr ApiAliasTest")

        assert by_alias.status_code == 200
        assert by_canonical.status_code == 200
        assert by_alias.json()["id"] == by_canonical.json()["id"]
        assert by_alias.json()["queried_name"] == "Vega"
        assert by_alias.json()["name"] == "alf Lyr ApiAliasTest"

    def test_simbad_import_deduplicates_across_different_search_terms(self, monkeypatch):
        """Two different names (e.g. 'bet Lyr' / 'Sheliak') that Simbad resolves
        to the same canonical identity must not create two distinct stars."""
        import rosace.main as main_mod
        from rosace.targets import StarCreate

        async def fake_simbad_search(name):
            # Simulates two different search terms for the same physical object.
            return StarCreate(name="DedupCanonicalStar", ra=282.52, dec=33.36)

        monkeypatch.setattr(main_mod, "simbad_search", fake_simbad_search)

        with self._client() as client:
            first = client.post("/simbad/import", params={"name": "DedupSearchTermOne"})
            assert first.status_code == 201

            second = client.post("/simbad/import", params={"name": "DedupSearchTermTwo"})

        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["name"] == "DedupCanonicalStar"

        with self._client() as client:
            all_stars = client.get("/stars").json()
        canonical_matches = [s for s in all_stars if s["name"] == "DedupCanonicalStar"]
        assert len(canonical_matches) == 1  # no duplicate

    def test_simbad_import_reports_alias_added(self, monkeypatch):
        """The response explicitly states whether the searched term was
        registered as a new alias — so the caller doesn't have to go check
        the database by hand."""
        import rosace.main as main_mod
        from rosace.targets import StarCreate

        async def fake_simbad_search(name):
            return StarCreate(name="AliasAddedCanonicalStar", ra=10.0, dec=10.0)

        monkeypatch.setattr(main_mod, "simbad_search", fake_simbad_search)

        with self._client() as client:
            # New star, searched name differs from the canonical name -> alias added.
            created = client.post("/simbad/import", params={"name": "AliasAddedSearchTerm"})
            assert created.json()["alias_added"] is True

            # Same searched name a 2nd time -> already known, no new alias.
            again = client.post("/simbad/import", params={"name": "AliasAddedSearchTerm"})
            assert again.json()["alias_added"] is False

            # Searching directly by the canonical name -> never an alias.
            by_canonical = client.post("/simbad/import", params={"name": "AliasAddedCanonicalStar"})
            assert by_canonical.json()["alias_added"] is False

    def test_import_stars_from_file_mixed_results(self, monkeypatch):
        """One line per star; errors on one line don't prevent the others."""
        import rosace.main as main_mod
        from rosace.targets import StarCreate

        known = {
            "VegaBulkImportTest": StarCreate(name="alf Lyr BulkImportTest", ra=279.23, dec=38.78),
        }

        async def fake_simbad_search(name):
            if name not in known:
                raise SimbadNotFoundError(f"{name} introuvable")
            return known[name]

        monkeypatch.setattr(main_mod, "simbad_search", fake_simbad_search)

        content = "VegaBulkImportTest\nBadStarBulkImportTest\n"
        with self._client() as client:
            res = client.post(
                "/stars/import-list",
                files={"file": ("stars.txt", content, "text/plain")},
            )

        assert res.status_code == 200
        results = {r["name"]: r for r in res.json()}
        assert results["VegaBulkImportTest"]["status"] == "created"
        assert results["VegaBulkImportTest"]["canonical_name"] == "alf Lyr BulkImportTest"
        assert results["BadStarBulkImportTest"]["status"] == "error"
        assert results["BadStarBulkImportTest"]["id"] is None

    def test_import_stars_from_file_existing_star_reported_as_existing(self, monkeypatch):
        import rosace.main as main_mod
        from rosace.targets import StarCreate

        async def fake_simbad_search(name):
            return StarCreate(name="alf Lyr BulkExistingTest", ra=279.23, dec=38.78)

        monkeypatch.setattr(main_mod, "simbad_search", fake_simbad_search)

        with self._client() as client:
            first = client.post(
                "/stars/import-list",
                files={"file": ("stars.txt", "VegaBulkExistingTest\n", "text/plain")},
            )
            second = client.post(
                "/stars/import-list",
                files={"file": ("stars.txt", "VegaBulkExistingTest\n", "text/plain")},
            )

        assert first.json()[0]["status"] == "created"
        assert second.json()[0]["status"] == "existing"
        assert second.json()[0]["id"] == first.json()[0]["id"]

    def test_import_program_stars_from_file(self):
        with self._client() as client:
            client.post("/programs", json={"name": "BulkProgramTest", "description": "test"})

            async def _insert_star():
                from rosace.targets import StarCreate, star_insert
                return await star_insert(StarCreate(name="BulkLinkStarTest", ra=1.0, dec=1.0))

            asyncio.run(_insert_star())

            yaml_content = (
                "BulkProgramTest:\n"
                "  - BulkLinkStarTest\n"
                "  - UnknownStarBulkTest\n"
                "UnknownProgramBulkTest:\n"
                "  - BulkLinkStarTest\n"
            )
            res = client.post(
                "/programs/import-list",
                files={"file": ("programs.yaml", yaml_content, "application/x-yaml")},
            )

        assert res.status_code == 200
        results = {(r["program"], r["star"]): r for r in res.json()}
        assert results[("BulkProgramTest", "BulkLinkStarTest")]["status"] == "linked"
        assert results[("BulkProgramTest", "UnknownStarBulkTest")]["status"] == "star_not_found"
        assert results[("UnknownProgramBulkTest", "BulkLinkStarTest")]["status"] == "program_not_found"

    def test_create_program_rejects_path_traversal_name(self):
        """ProgramCreate.name feeds a file path and a Python import path
        downstream (pipeline_loader.py) — rejected at the API boundary before
        it ever reaches targets.db, with a clear log trail for the operator.
        Asserted via a handler attached directly to the emitting logger, not
        caplog/capsys/capfd: the spectro logger has propagate=0 (conf/logging.cfg,
        deliberately, to avoid duplicate output via root), so its records never
        reach caplog's root-attached handler; and its consoleHandler was bound
        to sys.stdout at import time, before pytest's own capture takes over,
        so capsys/capfd don't see it either."""
        class _Collector(logging.Handler):
            def __init__(self):
                super().__init__()
                self.messages = []

            def emit(self, record):
                self.messages.append(record.getMessage())

        collector = _Collector()
        target_logger = logging.getLogger("spectro.targets")
        target_logger.addHandler(collector)
        try:
            with self._client() as client:
                res = client.post("/programs", json={"name": "../evil", "description": "test"})
        finally:
            target_logger.removeHandler(collector)

        assert res.status_code == 422
        assert any("Program name rejected" in m for m in collector.messages)

    def test_observations_recent_endpoint(self):
        with self._client() as client:
            res = client.get("/observations/recent?limit=5")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_ws_logs_streams_log_records(self, monkeypatch):
        """End-to-end: a spectro.* log record reaches a connected /ws client as log.create."""
        import rosace.log_broadcast as log_broadcast_mod

        monkeypatch.setattr(log_broadcast_mod, "FLUSH_INTERVAL", 0.02)
        # the handler is a module-level singleton — start from a clean slate
        log_broadcast_mod.ws_log_handler._buffer.clear()
        log_broadcast_mod.ws_log_handler._dropped = 0

        with self._client() as client:
            with client.websocket_connect("/ws", subprotocols=["messaging"]) as ws:
                logging.getLogger("spectro.test.ws").info("hello over websocket")
                message = ws.receive_json()

        assert message["msg"] == "log.create"
        texts = [line["text"] for line in message["params"]["lines"]]
        assert any("hello over websocket" in t for t in texts)

    def test_ws_streams_image_new(self):
        """End-to-end: image_broadcast.ws_image_broadcaster.broadcast() reaches a
        connected /ws client as image.new — same channel as log.create."""
        import rosace.image_broadcast as image_broadcast_mod

        with self._client() as client:
            with client.websocket_connect("/ws", subprotocols=["messaging"]) as ws:
                asyncio.run(image_broadcast_mod.ws_image_broadcaster.broadcast("guidage"))
                message = ws.receive_json()

        assert message == {"msg": "image.new", "params": {"panel": "guidage"}}

    def test_get_gui_image_serves_the_panel_file(self, tmp_path, monkeypatch):
        import rosace.main as main_mod

        monkeypatch.setattr(main_mod, "GUI_PREVIEW_DIR", tmp_path)
        (tmp_path / "guidage.fits").write_bytes(b"fake fits bytes")

        with self._client() as client:
            res = client.get("/images/guidage")

        assert res.status_code == 200
        assert res.content == b"fake fits bytes"
        assert res.headers["cache-control"] == "no-store"

    def test_get_gui_image_404_when_no_file_yet(self, tmp_path, monkeypatch):
        import rosace.main as main_mod

        monkeypatch.setattr(main_mod, "GUI_PREVIEW_DIR", tmp_path)

        with self._client() as client:
            res = client.get("/images/guidage")

        assert res.status_code == 404

    def test_get_gui_image_rejects_unsafe_panel_name(self, tmp_path, monkeypatch):
        """A single path segment can't itself traverse directories (no "/" survives
        routing to reach _SAFE_PANEL_NAME) -- this checks the regex rejects any
        other character outside [A-Za-z0-9_], the defense-in-depth posture used
        elsewhere in the app (e.g. ProgramCreate.name)."""
        import rosace.main as main_mod

        monkeypatch.setattr(main_mod, "GUI_PREVIEW_DIR", tmp_path)
        (tmp_path / "foo-bar.fits").write_bytes(b"should never be served")

        with self._client() as client:
            res = client.get("/images/foo-bar")

        assert res.status_code == 404


# ═════════════════════════════════════════════════════════════
# GROUP 8 — Authentication (auth.py)
# ═════════════════════════════════════════════════════════════

class TestAuthRoutes:
    """/auth/login, /auth/logout, /auth/me — via TestApiEndpoints's own client
    helpers (same tmp DB, same fixed test admin)."""

    def _client(self):
        return TestApiEndpoints()._client()

    def _anon_client(self):
        return TestApiEndpoints()._anon_client()

    def test_login_success_sets_cookie_and_returns_user(self):
        with self._anon_client() as client:
            TestApiEndpoints()._ensure_test_admin()
            res = client.post("/auth/login", json={
                "username": TestApiEndpoints._TEST_ADMIN_USER,
                "password": TestApiEndpoints._TEST_ADMIN_PASS,
            })
        assert res.status_code == 200
        assert res.json()["username"] == TestApiEndpoints._TEST_ADMIN_USER
        assert res.json()["is_admin"] is True
        assert "rosace_session" in res.cookies

    def test_login_wrong_password_returns_401(self):
        with self._anon_client() as client:
            TestApiEndpoints()._ensure_test_admin()
            res = client.post("/auth/login", json={
                "username": TestApiEndpoints._TEST_ADMIN_USER, "password": "not the password",
            })
        assert res.status_code == 401

    def test_login_unknown_user_returns_401(self):
        with self._anon_client() as client:
            res = client.post("/auth/login", json={"username": "nobody", "password": "whatever"})
        assert res.status_code == 401

    def test_logout_clears_cookie_and_invalidates_session(self):
        with self._client() as client:
            res = client.post("/auth/logout")
            assert res.status_code == 204
            # the session cookie is gone client-side, so this request carries none —
            # this is the same 401 path as test_protected_route_without_cookie below
            after = client.get("/status")
        assert after.status_code == 401

    def test_me_returns_current_user(self):
        with self._client() as client:
            res = client.get("/auth/me")
        assert res.status_code == 200
        assert res.json()["username"] == TestApiEndpoints._TEST_ADMIN_USER


class TestAuthDependencies:
    """Route protection itself — the "protected"/"admin_router" wiring in main.py."""

    def test_protected_route_without_cookie_returns_401(self):
        with TestApiEndpoints()._anon_client() as client:
            res = client.get("/status")
        assert res.status_code == 401

    def test_ws_without_cookie_closes_connection(self):
        with TestApiEndpoints()._anon_client() as client:
            with pytest.raises(Exception):  # starlette.websockets.WebSocketDisconnect
                with client.websocket_connect("/ws", subprotocols=["messaging"]):
                    pass

    def test_admin_route_rejects_non_admin_user(self):
        import rosace.auth as auth_mod

        async def _make_plain_user():
            try:
                await auth_mod.user_create(auth_mod.UserCreate(
                    username="plain_user", password="whatever_pw", is_admin=False,
                ))
            except ValueError:
                pass  # already provisioned by an earlier run

        asyncio.run(_make_plain_user())

        with TestApiEndpoints()._anon_client() as client:
            client.post("/auth/login", json={"username": "plain_user", "password": "whatever_pw"})
            res = client.get("/auth/users")
        assert res.status_code == 403

    def test_admin_route_accepts_admin_user(self):
        with TestApiEndpoints()._client() as client:
            res = client.get("/auth/users")
        assert res.status_code == 200
        usernames = [u["username"] for u in res.json()]
        assert TestApiEndpoints._TEST_ADMIN_USER in usernames


class TestCreateAdminCLI:
    """`python -m rosace.main --create-admin username` — bootstraps the very
    first admin and exits, never starts the server. Subprocess-based, same
    shape as TestCheckConfigCLI — but unlike --check-config (read-only), this
    one really writes to a database, so each test points ROSACE_CONFIG at its
    own fresh tmp_path rather than touching the real active config."""

    def _write_tmp_config(self, tmp_path: Path) -> Path:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(f"""
base_data_dir: {tmp_path / "data"}
databases_dir: databases
log_dir: log
observations_dir: observations
site_name: TestSite
site_latitude_deg: 45.0
site_longitude_deg: 5.0
site_elevation_m: 950
min_target_altitude_deg: 10
active_hardware: hardware_indi_simu
scheduler_retry_seconds: 60
sequencer_backend: internal
hardware_connect_timeout_seconds: 5
exposure_time_mag0_seconds: 1.0
max_exposure_time_seconds: 300
dark_count: 5
bias_count: 5
bias_exposure_time_seconds: 0.0
dark_library_dir: dark_library
camera_cooldown_timeout_seconds: 300
observation_list_path: {tmp_path / "ObservationList.txt"}
active_scheduler: blank
session_ttl_hours: 336
session_cookie_secure: false
""")
        return config_path

    def _run(self, tmp_path, *extra_args, password="testpassword123"):
        config_path = self._write_tmp_config(tmp_path)
        return subprocess.run(
            [sys.executable, "-m", "rosace.main", "--create-admin", *extra_args],
            capture_output=True, text=True, input=f"{password}\n",
            # getpass.getpass() opens /dev/tty directly when the process has a
            # controlling terminal (e.g. running pytest from an interactive shell),
            # bypassing the piped input= above entirely and blocking on real input.
            # start_new_session detaches this subprocess from that terminal so
            # getpass falls back to stdin, like it already does when pytest itself
            # has no controlling terminal (e.g. CI).
            start_new_session=True,
            env={**os.environ, "ROSACE_CONFIG": str(config_path)},
        )

    def test_missing_username_fails_fast(self, tmp_path):
        result = self._run(tmp_path)  # no username after --create-admin
        assert result.returncode != 0
        assert "[FAIL]" in result.stdout

    def test_creates_first_admin_and_exits_zero(self, tmp_path):
        result = self._run(tmp_path, "night_owl")
        assert result.returncode == 0
        assert "[OK]" in result.stdout
        assert "night_owl" in result.stdout

    def test_refuses_once_an_admin_already_exists(self, tmp_path):
        first = self._run(tmp_path, "first_admin")
        assert first.returncode == 0

        second = self._run(tmp_path, "second_admin")
        assert second.returncode != 0
        assert "[FAIL]" in second.stdout
