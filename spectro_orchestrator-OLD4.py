"""
Orchestrateur minimal pour observations spectroscopiques
Machine d'état : INIT_SESSION → SCHEDULER → SEQUENCER → REDUCTION → SHARING → ARCHIVE → CLOSE_SESSION
"""

from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable
import asyncio
import logging
import uuid
import yaml
import aiosqlite
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("spectro.orchestrator")

# ─────────────────────────────────────────────────────────────
# Application
# ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Spectro Orchestrator",
    description="Orchestrateur d'observations spectroscopiques",
    version="0.4.0"
)

# ─────────────────────────────────────────────────────────────
# Chemins
# ─────────────────────────────────────────────────────────────

BASE_DATA_DIR = Path("/tmp/rosace")
DB_PATH       = BASE_DATA_DIR / "rosace.db"

# ─────────────────────────────────────────────────────────────
# Modèles de données
# ─────────────────────────────────────────────────────────────

class OrchestratorStage(str, Enum):
    IDLE          = "idle"
    INIT_SESSION  = "init_session"
    SCHEDULER     = "scheduler"
    SEQUENCER     = "sequencer"
    REDUCTION     = "reduction"
    SHARING       = "sharing"
    ARCHIVE       = "archive"
    CLOSE_SESSION = "close_session"
    ERROR         = "error"

class Target(BaseModel):
    name: str
    ra: float = Field(..., ge=0, lt=360, description="Ascension droite en degrés")
    dec: float = Field(..., ge=-90, le=90, description="Déclinaison en degrés")
    magnitude: Optional[float] = None

class ExposureConfig(BaseModel):
    exposure_time: float = Field(..., gt=0, description="Temps d'exposition en secondes")
    num_exposures: int = Field(default=1, ge=1)
    binning: int = Field(default=1, ge=1, le=4)
    grating: str = Field(default="R1200")
    central_wavelength: float = Field(default=6563.0, description="Longueur d'onde centrale en Å")

class ObservationRequest(BaseModel):
    target: Target
    exposure: ExposureConfig
    notes: Optional[str] = None

class Observation(BaseModel):
    id: str
    request: ObservationRequest
    created_at: datetime
    files: list[str] = []

# ─────────────────────────────────────────────────────────────
# Contexte de session
# Transmis de stage en stage ; chaque plugin peut l'enrichir.
# ─────────────────────────────────────────────────────────────

class SessionContext(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    started_at: datetime = Field(default_factory=datetime.utcnow)
    current_observation: Optional[Observation] = None
    yaml_file: Optional[str] = None
    # Fichiers produits par le SEQUENCER, consommés par REDUCTION puis SHARING/ARCHIVE
    raw_files: list[str] = []
    reduced_files: list[str] = []

    class Config:
        arbitrary_types_allowed = True

# ─────────────────────────────────────────────────────────────
# Protocol Plugin
# ─────────────────────────────────────────────────────────────

@runtime_checkable
class StagePlugin(Protocol):
    """Interface commune à tous les plugins de stage."""
    async def run(self, ctx: SessionContext) -> SessionContext:
        """
        Exécute la logique du stage.
        Reçoit le contexte courant, retourne le contexte enrichi.
        Toute exception remonte à l'orchestrateur qui gère le fallback.
        """
        ...

# ─────────────────────────────────────────────────────────────
# Plugins stubs
# ─────────────────────────────────────────────────────────────

class InitSessionPlugin:
    """Initialise la session : connexion instruments, vérifications pré-nuit."""
    async def run(self, ctx: SessionContext) -> SessionContext:
        log.info(f"[INIT_SESSION] Démarrage session {ctx.session_id}")
        # TODO : connexion INDI/ASCOM, vérification météo, mise en température CCD…
        await asyncio.sleep(1.0)
        log.info("[INIT_SESSION] Session initialisée")
        return ctx

class SchedulerPlugin:
    """
    Interroge la base SQLite pour obtenir la prochaine observation.
    Bloque jusqu'à ce qu'une observation soit disponible.
    """
    async def run(self, ctx: SessionContext) -> SessionContext:
        log.info("[SCHEDULER] En attente d'une observation…")
        while True:
            obs = await db_fetch_next_observation()
            if obs:
                log.info(f"[SCHEDULER] Observation sélectionnée : {obs.id} ({obs.request.target.name})")
                ctx.current_observation = obs
                ctx.raw_files = []
                ctx.reduced_files = []
                ctx.yaml_file = None
                return ctx
            await asyncio.sleep(2.0)

class SequencerPlugin:
    """Exécute la séquence instrumentale : pointage, config, expositions."""
    async def run(self, ctx: SessionContext) -> SessionContext:
        obs = ctx.current_observation
        if not obs:
            raise RuntimeError("SEQUENCER : aucune observation dans le contexte")

        log.info(f"[SEQUENCER] Début séquence pour {obs.request.target.name}")
        target   = obs.request.target
        exposure = obs.request.exposure

        # 1. Pointage
        log.info(f"[SEQUENCER] Pointage vers RA={target.ra} Dec={target.dec}")
        await asyncio.sleep(2.0)  # TODO : driver télescope

        # 2. Configuration spectrographe
        log.info(f"[SEQUENCER] Configuration réseau {exposure.grating} @ {exposure.central_wavelength} Å")
        await asyncio.sleep(1.0)  # TODO : driver spectrographe

        # 3. Acquisition sur fente (TODO : délégué au plugin Sequencer)
        await asyncio.sleep(1.5)

        # 4. Séquence d'expositions
        for i in range(exposure.num_exposures):
            log.info(f"[SEQUENCER] Exposition {i + 1}/{exposure.num_exposures} ({exposure.exposure_time}s)")
            await asyncio.sleep(exposure.exposure_time * 0.1)  # Simulation accélérée
            filename = f"/data/{obs.id}/spectrum_{i + 1:04d}.fits"
            obs.files.append(filename)
            ctx.raw_files.append(filename)

        log.info(f"[SEQUENCER] {len(ctx.raw_files)} fichier(s) produit(s)")
        return ctx

class ReductionPlugin:
    """Réduit les fichiers bruts produits par le SEQUENCER."""
    async def run(self, ctx: SessionContext) -> SessionContext:
        if not ctx.raw_files:
            raise RuntimeError("REDUCTION : aucun fichier brut dans le contexte")

        log.info(f"[REDUCTION] Réduction de {len(ctx.raw_files)} fichier(s)")
        for raw in ctx.raw_files:
            reduced = raw.replace(".fits", "_reduced.fits")
            await asyncio.sleep(0.5)  # TODO : pipeline de réduction
            ctx.reduced_files.append(reduced)
            log.info(f"[REDUCTION] {raw} → {reduced}")

        return ctx

class SharingPlugin:
    """Diffuse les fichiers réduits (envoi observateur, publication)."""
    async def run(self, ctx: SessionContext) -> SessionContext:
        if not ctx.reduced_files:
            raise RuntimeError("SHARING : aucun fichier réduit dans le contexte")

        log.info(f"[SHARING] Diffusion de {len(ctx.reduced_files)} fichier(s)")
        for f in ctx.reduced_files:
            await asyncio.sleep(0.3)  # TODO : envoi email / API
            log.info(f"[SHARING] Diffusé : {f}")

        return ctx

class ArchivePlugin:
    """Archive les fichiers réduits dans le stockage long terme."""
    async def run(self, ctx: SessionContext) -> SessionContext:
        if not ctx.reduced_files:
            raise RuntimeError("ARCHIVE : aucun fichier réduit dans le contexte")

        log.info(f"[ARCHIVE] Archivage de {len(ctx.reduced_files)} fichier(s)")
        for f in ctx.reduced_files:
            await asyncio.sleep(0.3)  # TODO : transfert vers archive
            log.info(f"[ARCHIVE] Archivé : {f}")

        return ctx

class CloseSessionPlugin:
    """Clôture la session : stationnement télescope, déconnexion instruments."""
    async def run(self, ctx: SessionContext) -> SessionContext:
        log.info(f"[CLOSE_SESSION] Clôture session {ctx.session_id}")
        await asyncio.sleep(1.0)  # TODO : stationner télescope, fermer coupole…
        log.info("[CLOSE_SESSION] Session clôturée")
        return ctx

# ─────────────────────────────────────────────────────────────
# Registre des plugins (point d'extension central)
# ─────────────────────────────────────────────────────────────

plugins: dict[OrchestratorStage, StagePlugin] = {
    OrchestratorStage.INIT_SESSION:  InitSessionPlugin(),
    OrchestratorStage.SCHEDULER:     SchedulerPlugin(),
    OrchestratorStage.SEQUENCER:     SequencerPlugin(),
    OrchestratorStage.REDUCTION:     ReductionPlugin(),
    OrchestratorStage.SHARING:       SharingPlugin(),
    OrchestratorStage.ARCHIVE:       ArchivePlugin(),
    OrchestratorStage.CLOSE_SESSION: CloseSessionPlugin(),
}

# ─────────────────────────────────────────────────────────────
# État global du système
# ─────────────────────────────────────────────────────────────

class SystemState:
    def __init__(self):
        self.stage: OrchestratorStage = OrchestratorStage.IDLE
        self.is_running: bool = False
        self.completed: list[Observation] = []

state = SystemState()

# ─────────────────────────────────────────────────────────────
# Base de données SQLite
# ─────────────────────────────────────────────────────────────

async def db_init() -> None:
    """Crée les tables si elles n'existent pas."""
    BASE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id         TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                closed_at  TEXT,
                status     TEXT NOT NULL DEFAULT 'running'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                id               TEXT PRIMARY KEY,
                session_id       TEXT NOT NULL REFERENCES sessions(id),
                target_name      TEXT NOT NULL,
                yaml_file        TEXT,
                created_at       TEXT NOT NULL,
                error_message    TEXT,
                scheduler_done   TEXT,
                sequencer_done   TEXT,
                reduction_done   TEXT,
                sharing_done     TEXT,
                archive_done     TEXT
            )
        """)
        await db.commit()
    log.info(f"[DB] Base initialisée : {DB_PATH}")

async def db_insert_session(ctx: SessionContext) -> None:
    """Insère une nouvelle session."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (id, started_at, status) VALUES (?, ?, 'running')",
            (ctx.session_id, ctx.started_at.isoformat())
        )
        await db.commit()
    log.info(f"[DB] Session insérée : {ctx.session_id}")

async def db_close_session(ctx: SessionContext, status: str = "closed") -> None:
    """Met à jour la session à la clôture."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET closed_at = ?, status = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), status, ctx.session_id)
        )
        await db.commit()
    log.info(f"[DB] Session clôturée : {ctx.session_id} ({status})")

async def db_insert_observation(obs: Observation, session_id: str) -> None:
    """Insère une nouvelle observation après le SCHEDULER."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO observations
               (id, session_id, target_name, created_at, scheduler_done)
               VALUES (?, ?, ?, ?, ?)""",
            (
                obs.id,
                session_id,
                obs.request.target.name,
                obs.created_at.isoformat(),
                datetime.utcnow().isoformat(),
            )
        )
        await db.commit()
    log.info(f"[DB] Observation insérée : {obs.id}")

async def db_set_yaml_file(obs_id: str, yaml_file: str) -> None:
    """Enregistre le chemin du fichier YAML de l'observation."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE observations SET yaml_file = ? WHERE id = ?",
            (yaml_file, obs_id)
        )
        await db.commit()

async def db_stamp_stage(obs_id: str, stage: OrchestratorStage) -> None:
    """Enregistre le timestamp de fin d'une étape."""
    col = {
        OrchestratorStage.SEQUENCER: "sequencer_done",
        OrchestratorStage.REDUCTION: "reduction_done",
        OrchestratorStage.SHARING:   "sharing_done",
        OrchestratorStage.ARCHIVE:   "archive_done",
    }.get(stage)
    if not col:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE observations SET {col} = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), obs_id)
        )
        await db.commit()
    log.info(f"[DB] {col} enregistré pour {obs_id}")

async def db_set_error(obs_id: str, message: str) -> None:
    """Enregistre un message d'erreur sur l'observation."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE observations SET error_message = ? WHERE id = ?",
            (message, obs_id)
        )
        await db.commit()

async def db_fetch_next_observation() -> Optional[Observation]:
    """
    Récupère la prochaine observation à exécuter depuis la base SQLite.
    Sélectionne les observations sans scheduler_done, ordonnées par created_at.

    Note : à adapter selon votre logique de priorité.
    """
    # TODO : implémenter avec aiosqlite selon le schéma de la DB externe
    raise NotImplementedError("db_fetch_next_observation() doit être implémentée")

# ─────────────────────────────────────────────────────────────
# Fichier d'observation YAML
# ─────────────────────────────────────────────────────────────

def create_observation_file(ctx: SessionContext) -> Path:
    """
    Crée un fichier YAML pour l'observation courante.
    Nom : <datetime_iso>_<target_name>.yaml
    Emplacement : /tmp/rosace/<session_id>/
    Retourne le chemin du fichier créé.
    """
    obs = ctx.current_observation
    target_name = obs.request.target.name if obs else "target"
    now = datetime.utcnow()

    session_dir = BASE_DATA_DIR / ctx.session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    iso_str = now.strftime("%Y%m%dT%H%M%S")
    safe_target = target_name.replace(" ", "_").replace("/", "-")
    filename = f"{iso_str}_{safe_target}.yaml"

    content = {
        "id": now.isoformat(),
        "target": target_name,
        "created_at": now.isoformat(),
    }

    filepath = session_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        yaml.dump(content, f, allow_unicode=True, default_flow_style=False)

    log.info(f"[ORCHESTRATOR] Fichier d'observation créé : {filepath}")
    return filepath

# ─────────────────────────────────────────────────────────────
# Machine d'état principale
# ─────────────────────────────────────────────────────────────

# Boucle d'observations : stages exécutés en cycle
OBSERVATION_LOOP: list[OrchestratorStage] = [
    OrchestratorStage.SCHEDULER,
    OrchestratorStage.SEQUENCER,
    OrchestratorStage.REDUCTION,
    OrchestratorStage.SHARING,
    OrchestratorStage.ARCHIVE,
]

async def run_stage(stage: OrchestratorStage, ctx: SessionContext) -> SessionContext:
    """Exécute un stage via son plugin et met à jour l'état global."""
    state.stage = stage
    plugin = plugins[stage]
    return await plugin.run(ctx)

async def session_runner():
    """
    Boucle principale de la session.
    INIT_SESSION → [SCHEDULER → SEQUENCER → REDUCTION → SHARING → ARCHIVE] × N → CLOSE_SESSION
    """
    await db_init()
    ctx = SessionContext()

    # ── Initialisation (une seule fois) ──────────────────────
    try:
        ctx = await run_stage(OrchestratorStage.INIT_SESSION, ctx)
        await db_insert_session(ctx)
    except Exception as e:
        log.error(f"[INIT_SESSION] Échec critique, session annulée : {e}")
        state.stage = OrchestratorStage.ERROR
        state.is_running = False
        return

    # ── Boucle d'observations ─────────────────────────────────
    while state.is_running:
        for stage in OBSERVATION_LOOP:
            if not state.is_running:
                break
            try:
                ctx = await run_stage(stage, ctx)

                # ── Actions post-stage orchestrateur ─────────
                if stage == OrchestratorStage.SCHEDULER:
                    # Insertion en base + création fichier YAML
                    await db_insert_observation(ctx.current_observation, ctx.session_id)
                    yaml_path = create_observation_file(ctx)
                    ctx.yaml_file = str(yaml_path)
                    await db_set_yaml_file(ctx.current_observation.id, ctx.yaml_file)
                else:
                    # Timestamp de fin d'étape
                    await db_stamp_stage(ctx.current_observation.id, stage)

            except Exception as e:
                log.error(f"[{stage.value.upper()}] Erreur : {e} — retour au SCHEDULER")
                if ctx.current_observation:
                    await db_set_error(ctx.current_observation.id, str(e))
                    state.completed.append(ctx.current_observation)
                ctx = SessionContext(session_id=ctx.session_id, started_at=ctx.started_at)
                break  # sort du for → retour au SCHEDULER
        else:
            # Boucle for terminée sans break : observation réussie
            if ctx.current_observation:
                state.completed.append(ctx.current_observation)

    # ── Clôture (une seule fois) ──────────────────────────────
    try:
        ctx = await run_stage(OrchestratorStage.CLOSE_SESSION, ctx)
        await db_close_session(ctx, status="closed")
    except Exception as e:
        log.error(f"[CLOSE_SESSION] Erreur lors de la clôture : {e}")
        await db_close_session(ctx, status="error")
    finally:
        state.stage = OrchestratorStage.IDLE

# ─────────────────────────────────────────────────────────────
# Endpoints API
# ─────────────────────────────────────────────────────────────

@app.get("/status")
async def get_status():
    """Retourne l'état courant de l'orchestrateur."""
    return {
        "stage": state.stage,
        "is_running": state.is_running,
        "completed_count": len(state.completed),
    }

@app.get("/observations/completed")
async def list_completed():
    """Retourne les 20 dernières observations complétées (cache mémoire)."""
    return {"completed": state.completed[-20:]}

@app.get("/observations/{obs_id}")
async def get_observation(obs_id: str):
    """Récupère une observation complétée par son identifiant."""
    for obs in state.completed:
        if obs.id == obs_id:
            return obs
    raise HTTPException(status_code=404, detail="Observation non trouvée")

@app.post("/control/start")
async def start_session(background_tasks: BackgroundTasks):
    """Démarre une nouvelle session d'observation."""
    if state.is_running:
        return {"message": "Session déjà en cours"}
    state.is_running = True
    background_tasks.add_task(session_runner)
    return {"message": "Session démarrée"}

@app.post("/control/stop")
async def stop_session():
    """Demande l'arrêt propre de la session (après l'observation en cours)."""
    state.is_running = False
    return {"message": "Arrêt demandé — CLOSE_SESSION sera exécuté en fin de cycle"}

# ─────────────────────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
