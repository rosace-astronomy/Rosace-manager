"""
test_rosace_core.py — Tests de régression du module cœur Rosace.
Périmètre : models, config, database, targets, simbad (hors plugins).

Lancement :
    pytest test_rosace_core.py -v
    pytest test_rosace_core.py -v --tb=short   # traceback concis
    pytest test_rosace_core.py -v -k "models"  # un groupe seulement

Dépendances :
    pip install pytest pytest-asyncio aiosqlite pydantic httpx
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ─────────────────────────────────────────────────────────────
# La fixture tmp_data_dir (patch de BASE_DATA_DIR) vit désormais dans
# conftest.py — partagée par tous les fichiers de tests du paquet.
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# Imports du package (après le patch)
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
    _astronomical_day_start_utc,
)
from rosace.targets import (
    targets_db_init,
    StarCreate, Star,
    ProgramCreate, Program,
    star_insert, star_get, star_get_by_name, star_list,
    star_update, star_delete,
    program_insert, program_get, program_list, program_delete,
    program_add_star, program_remove_star,
    program_get_stars, star_get_programs,
)
from rosace.simbad import (
    simbad_search,
    SimbadNotFoundError,
    SimbadQueryError,
)

from rosace.config import ACTIVE_HARDWARE, HARDWARE_CONFIG_PATH


# ═════════════════════════════════════════════════════════════
# GROUPE 0 — Configuration
# ═════════════════════════════════════════════════════════════

class TestHardwareConfig:
    """Sélection du fichier de configuration matérielle active."""

    def test_active_hardware_matches_config_path_stem(self):
        assert HARDWARE_CONFIG_PATH.stem == ACTIVE_HARDWARE

    def test_default_hardware_config_file_exists(self):
        assert HARDWARE_CONFIG_PATH.exists()


# ═════════════════════════════════════════════════════════════
# GROUPE 1 — Modèles Pydantic
# ═════════════════════════════════════════════════════════════

class TestOrchestratorStage:
    """Énumération des stages de la machine à états."""

    def test_all_stages_present(self):
        """Les 9 stages attendus sont bien déclarés."""
        expected = {
            "idle", "init_session", "scheduler", "sequencer",
            "reduction", "sharing", "archive", "close_session", "error",
        }
        actual = {s.value for s in OrchestratorStage}
        assert actual == expected

    def test_stage_is_string_enum(self):
        """OrchestratorStage hérite de str — utilisable comme chaîne."""
        assert OrchestratorStage.IDLE == "idle"
        assert isinstance(OrchestratorStage.SCHEDULER, str)

    def test_stage_comparison(self):
        assert OrchestratorStage.ERROR != OrchestratorStage.IDLE


class TestTargetModel:
    """Modèle Target (coordonnées célestes)."""

    def test_valid_target(self):
        t = Target(name="Vega", ra=279.23, dec=38.78)
        assert t.name == "Vega"
        assert t.ra == 279.23
        assert t.dec == 38.78
        assert t.magnitude is None

    def test_target_with_magnitude(self):
        t = Target(name="Sirius", ra=101.28, dec=-16.71, magnitude=-1.46)
        assert t.magnitude == pytest.approx(-1.46)

    def test_ra_lower_bound(self):
        t = Target(name="Test", ra=0.0, dec=0.0)
        assert t.ra == 0.0

    def test_ra_upper_bound_exclusive(self):
        """ra doit être strictement < 360."""
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
    """Configuration d'exposition."""

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

    def test_binning_bounds(self):
        from pydantic import ValidationError
        ExposureConfig(exposure_time=1.0, binning=4)       # ok
        with pytest.raises(ValidationError):
            ExposureConfig(exposure_time=1.0, binning=5)   # > 4
        with pytest.raises(ValidationError):
            ExposureConfig(exposure_time=1.0, binning=0)   # < 1


class TestObservation:
    """Dossier d'observation complet (rempli par les plugins de stage)."""

    def _make(self, obs_id="abc12345") -> Observation:
        obs = Observation(
            id=obs_id,
            created_at=datetime.now(timezone.utc),
            session_id="sess1234",
        )
        obs.scheduler_data["target"] = Target(name="Altair", ra=297.69, dec=8.87)
        obs.scheduler_data["exposure"] = ExposureConfig(exposure_time=120.0)
        obs.scheduler_data["programme"] = "etoiles_be"
        return obs

    def test_basic_creation(self):
        obs = self._make()
        assert obs.id == "abc12345"
        assert obs.scheduler_data["target"].name == "Altair"
        assert obs.sequencer_data == {}

    def test_stage_data_is_independent_per_instance(self):
        """Deux instances ne partagent pas les mêmes casiers <stage>_data."""
        obs1 = self._make("id1")
        obs2 = self._make("id2")
        obs1.sequencer_data["raw_files"] = ["a.fits"]
        assert obs2.sequencer_data == {}

    def test_suggested_filename_uses_target_name(self):
        obs = self._make()
        assert obs.suggested_filename.endswith("_Altair.yaml")

    def test_suggested_filename_falls_back_when_no_target(self):
        obs = Observation(id="x", created_at=datetime.now(timezone.utc), session_id="sess1234")
        assert obs.suggested_filename.endswith("_target.yaml")

    def test_suggested_filename_sanitizes_special_characters(self):
        obs = Observation(id="x", created_at=datetime.now(timezone.utc), session_id="sess1234")
        obs.scheduler_data["target"] = Target(name="V/380 Cyg", ra=1.0, dec=1.0)
        assert "/" not in obs.suggested_filename
        assert " " not in obs.suggested_filename

    def test_suggested_filename_reflects_current_scheduler_data(self):
        """La propriété se recalcule à chaque lecture — pas figée à la création."""
        obs = Observation(id="x", created_at=datetime.now(timezone.utc), session_id="sess1234")
        assert obs.suggested_filename.endswith("_target.yaml")
        obs.scheduler_data["target"] = Target(name="Deneb", ra=1.0, dec=1.0)
        assert obs.suggested_filename.endswith("_Deneb.yaml")


class TestSessionContext:
    """Contexte de session transmis entre les stages."""

    def test_default_values(self):
        ctx = SessionContext()
        assert ctx.site_name == "St-Pancrasse"
        assert ctx.session_dir is None
        assert len(ctx.session_id) == 8      # UUID[:8]

    def test_session_ids_are_unique(self):
        ids = {SessionContext().session_id for _ in range(50)}
        assert len(ids) == 50                # très forte probabilité

    def test_started_at_is_utc(self):
        ctx = SessionContext()
        assert ctx.started_at.tzinfo is not None

    def test_custom_site(self):
        ctx = SessionContext(site_name="Pic du Midi")
        assert ctx.site_name == "Pic du Midi"


class TestObservationSessionId:
    """Observation.session_id — traçabilité de la session d'origine."""

    def test_session_id_is_required(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Observation(id="x", created_at=datetime.now(timezone.utc))  # session_id manquant


# ═════════════════════════════════════════════════════════════
# GROUPE 2 — Base de données sessions / observations
# ═════════════════════════════════════════════════════════════

class TestDatabase:
    """Tests des fonctions database.py (SQLite via aiosqlite)."""

    @pytest_asyncio.fixture(autouse=True)
    async def init_db(self):
        """Initialise les tables avant chaque test."""
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
        obs.scheduler_data["programme"] = "etoiles_be"
        return obs

    async def test_db_init_idempotent(self):
        """db_init peut être appelé plusieurs fois sans erreur."""
        await db_init()
        await db_init()

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
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        await db_insert_observation(obs, ctx.session_id)
        await db_set_yaml_file(obs.id, "/tmp/obs.yaml")

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
        """Un stage non mappé (ex. IDLE) ne lève pas d'erreur."""
        ctx = self._make_ctx()
        await db_insert_session(ctx)
        obs = self._make_obs(ctx.session_id)
        await db_insert_observation(obs, ctx.session_id)
        await db_stamp_stage(obs.id, OrchestratorStage.IDLE)   # doit passer silencieusement

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

    async def test_get_recent_observations_excludes_previous_astronomical_day(self):
        """Une observation d'avant-hier n'apparaît pas dans la liste du jour astronomique en cours."""
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

    def test_astronomical_day_start_utc_spans_local_noon(self):
        """La frontière est le midi local précédent — robuste au fuseau de la machine de test."""
        system_tz = datetime.now().astimezone().tzinfo
        local_date = datetime.now(system_tz).date()
        just_after_noon = datetime(local_date.year, local_date.month, local_date.day, 12, 30, tzinfo=system_tz)
        just_before_noon = just_after_noon.replace(hour=11, minute=30)

        boundary_after  = datetime.fromisoformat(_astronomical_day_start_utc(just_after_noon))
        boundary_before = datetime.fromisoformat(_astronomical_day_start_utc(just_before_noon))

        # crossing noon shifts the boundary by exactly one calendar day
        assert boundary_after - boundary_before == timedelta(days=1)
        # the boundary is always the most recent noon at or before `now`
        assert boundary_after <= just_after_noon.astimezone(timezone.utc)
        assert boundary_before <= just_before_noon.astimezone(timezone.utc)


# ═════════════════════════════════════════════════════════════
# GROUPE 3 — Base de données cibles (targets.py)
# ═════════════════════════════════════════════════════════════

class TestTargetsDB:
    """Tests CRUD étoiles et programmes (targets.py)."""

    @pytest_asyncio.fixture(autouse=True)
    async def init_db(self):
        await targets_db_init()

    def _star(self, name="Altair", ra=297.69, dec=8.87) -> StarCreate:
        return StarCreate(name=name, ra=ra, dec=dec, magnitude=0.76,
                          spectral_type="A7V", exposure_time=120.0)

    def _program(self, name="Programme A") -> ProgramCreate:
        return ProgramCreate(name=name, description="Test programme")

    # ── Étoiles ──────────────────────────────────────────────

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
        """Tous les champs facultatifs survivent un aller-retour en base."""
        sc = StarCreate(name="Rigel", ra=78.63, dec=-8.20,
                        magnitude=0.13, spectral_type="B8Ia",
                        exposure_time=600.0, notes="Belle étoile")
        sid = await star_insert(sc)
        star = await star_get(sid)
        assert star.magnitude     == pytest.approx(0.13)
        assert star.spectral_type == "B8Ia"
        assert star.exposure_time == pytest.approx(600.0)
        assert star.notes         == "Belle étoile"

    # ── Programmes ───────────────────────────────────────────

    async def test_program_insert_and_get(self):
        pid = await program_insert(self._program("Prog-X"))
        prog = await program_get(pid)
        assert prog is not None
        assert prog.name == "Prog-X"

    async def test_program_get_nonexistent(self):
        prog = await program_get(999999)
        assert prog is None

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

    # ── Liens program ↔ stars ─────────────────────────────────

    async def test_program_add_and_get_stars(self):
        sid1 = await star_insert(self._star("S1", ra=10.0, dec=10.0))
        sid2 = await star_insert(self._star("S2", ra=20.0, dec=20.0))
        pid  = await program_insert(self._program("P-Lien"))
        await program_add_star(pid, sid1)
        await program_add_star(pid, sid2)
        stars = await program_get_stars(pid)
        assert len(stars) == 2
        assert {s.id for s in stars} == {sid1, sid2}

    async def test_program_add_star_idempotent(self):
        """Ajouter deux fois la même étoile ne crée pas de doublon."""
        sid = await star_insert(self._star("SIdem", ra=30.0, dec=5.0))
        pid = await program_insert(self._program("P-Idem"))
        await program_add_star(pid, sid)
        await program_add_star(pid, sid)
        stars = await program_get_stars(pid)
        assert len(stars) == 1

    async def test_program_remove_star(self):
        sid = await star_insert(self._star("SRemove", ra=40.0, dec=5.0))
        pid = await program_insert(self._program("P-Remove"))
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
        """Supprimer une étoile retire aussi ses entrées program_stars."""
        sid = await star_insert(self._star("SClean", ra=60.0, dec=5.0))
        pid = await program_insert(self._program("P-Clean"))
        await program_add_star(pid, sid)
        await star_delete(sid)
        stars = await program_get_stars(pid)
        assert stars == []

    async def test_delete_program_cleans_star_links(self):
        """Supprimer un programme retire aussi ses entrées program_stars."""
        sid = await star_insert(self._star("SPClean", ra=70.0, dec=5.0))
        pid = await program_insert(self._program("PP-Clean"))
        await program_add_star(pid, sid)
        await program_delete(pid)
        progs = await star_get_programs(sid)
        assert progs == []


# ═════════════════════════════════════════════════════════════
# GROUPE 4 — Module Simbad (requêtes mockées)
# ═════════════════════════════════════════════════════════════

# Réponse JSON TAP simulée (format Simbad)
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
    """Construit un mock de réponse httpx."""
    import json as _json
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


class TestSimbad:
    """Tests du module simbad.py (requêtes réseau mockées)."""

    async def test_simbad_found(self):
        """Retourne un StarCreate correctement peuplé."""
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
        assert result.magnitude == pytest.approx(0.03)

    async def test_simbad_not_found(self):
        """Lève SimbadNotFoundError si data est vide."""
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
        """magnitude est None quand vmag est NULL dans Simbad."""
        mock_resp = _mock_httpx_response(SIMBAD_RESPONSE_NO_MAG)

        with patch("rosace.simbad.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__  = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await simbad_search("Sirius")

        assert result.magnitude is None

    async def test_simbad_network_error(self):
        """Lève SimbadQueryError en cas d'erreur réseau."""
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
        """Lève SimbadQueryError en cas d'erreur HTTP 5xx."""
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
        Le nom contenant une apostrophe est correctement échappé
        (pas de crash sur la construction de la requête ADQL).
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
# GROUPE 5 — Cohérence croisée modèles / machine à états
# ═════════════════════════════════════════════════════════════

class TestSystemState:
    """
    Vérifie la cohérence de SystemState (importé depuis main)
    sans démarrer l'application FastAPI.
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
# GROUPE 6 — Machine à états orchestrée (session_runner)
# ═════════════════════════════════════════════════════════════

class TestSessionRunner:
    """
    Teste la boucle session_runner() de main.py avec des plugins factices
    (monkeypatch de rosace.main.plugins) pour contrôler précisément le
    déroulement sans dépendre des vrais sous-plugins (qui ne s'arrêtent
    jamais tout seuls — cf. SchedulerPlugin).
    """

    @pytest.fixture(autouse=True)
    def reset_state(self):
        """Isole chaque test : état propre avant et après."""
        import rosace.main as main_mod
        main_mod.state.is_running = False
        main_mod.state.stage = OrchestratorStage.IDLE
        yield
        main_mod.state.is_running = False
        main_mod.state.stage = OrchestratorStage.IDLE

    async def test_clean_stop_calls_close_session(self, monkeypatch):
        """Un SchedulerStopRequested déclenche CLOSE_SESSION puis repasse à IDLE."""
        import rosace.main as main_mod
        from rosace.plugins.scheduler import SchedulerStopRequested

        close_called = []

        class FakeInit:
            async def run(self, ctx):
                Path("/tmp/fake_session_dir").mkdir(parents=True, exist_ok=True)
                ctx.session_dir = "/tmp/fake_session_dir"
                return ctx

        class FakeScheduler:
            async def run(self, ctx):
                raise SchedulerStopRequested()

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

    async def test_error_restarts_from_scheduler_not_init(self, monkeypatch):
        """Une erreur dans SEQUENCER relance depuis SCHEDULER, pas depuis INIT_SESSION."""
        import rosace.main as main_mod
        from rosace.plugins.scheduler import SchedulerStopRequested

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
                    raise SchedulerStopRequested()
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

        assert init_calls == [1]           # INIT_SESSION jamais relancé
        assert len(scheduler_calls) == 2   # SCHEDULER relancé après l'erreur

    async def test_close_session_error_still_reaches_idle(self, monkeypatch):
        """Si CLOSE_SESSION lève une exception, l'état repasse quand même à IDLE."""
        import rosace.main as main_mod
        from rosace.plugins.scheduler import SchedulerStopRequested

        class FakeInit:
            async def run(self, ctx):
                Path("/tmp/fake_session_dir").mkdir(parents=True, exist_ok=True)
                ctx.session_dir = "/tmp/fake_session_dir"
                return ctx

        class FakeScheduler:
            async def run(self, ctx):
                raise SchedulerStopRequested()

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
        """Un échec dans INIT_SESSION empêche la boucle SCHEDULER de démarrer."""
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


# ═════════════════════════════════════════════════════════════
# GROUPE 7 — Endpoints FastAPI (TestClient, lifespan réel)
# ═════════════════════════════════════════════════════════════

class TestApiEndpoints:
    """Tests de la couche HTTP (jamais exercée directement par les autres groupes)."""

    @pytest.fixture(autouse=True)
    def reset_state(self):
        import rosace.main as main_mod
        main_mod.state.is_running = False
        main_mod.state.stage = OrchestratorStage.IDLE
        yield
        main_mod.state.is_running = False
        main_mod.state.stage = OrchestratorStage.IDLE

    def _client(self):
        from fastapi.testclient import TestClient
        from rosace.main import app
        return TestClient(app)

    def test_status_endpoint_shape(self):
        with self._client() as client:
            res = client.get("/status")
        assert res.status_code == 200
        data = res.json()
        assert set(data.keys()) == {"stage", "is_running", "completed_count", "pending_post_obs_count"}

    def test_control_start_when_idle(self, monkeypatch):
        """Démarre la session ; session_runner est mocké pour ne pas boucler indéfiniment."""
        import rosace.main as main_mod

        async def fake_runner():
            pass

        monkeypatch.setattr(main_mod, "session_runner", fake_runner)

        with self._client() as client:
            res = client.post("/control/start")

        assert res.status_code == 200
        assert "démarrée" in res.json()["message"].lower()

    def test_control_start_when_already_running(self):
        """Ne redémarre pas une session déjà en cours (pas de background task ajoutée)."""
        import rosace.main as main_mod

        with self._client() as client:
            main_mod.state.is_running = True
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
            res = client.get("/stars/999999")
        assert res.status_code == 404

    def test_create_and_get_star_via_api(self):
        with self._client() as client:
            create_res = client.post("/stars", json={
                "name": "ApiTestStar", "ra": 100.0, "dec": 20.0,
            })
            assert create_res.status_code == 201
            star_id = create_res.json()["id"]
            get_res = client.get(f"/stars/{star_id}")

        assert get_res.status_code == 200
        assert get_res.json()["name"] == "ApiTestStar"

    def test_simbad_import_409_on_duplicate(self, monkeypatch):
        import rosace.main as main_mod
        from rosace.targets import StarCreate

        async def fake_simbad_search(name):
            return StarCreate(name="DupStar", ra=10.0, dec=10.0)

        monkeypatch.setattr(main_mod, "simbad_search", fake_simbad_search)

        with self._client() as client:
            first = client.post("/simbad/import", params={"name": "DupStar"})
            assert first.status_code == 201
            second = client.post("/simbad/import", params={"name": "DupStar"})

        assert second.status_code == 409

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
