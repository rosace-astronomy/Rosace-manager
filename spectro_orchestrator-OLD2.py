"""
Orchestrateur minimal pour observations spectroscopiques
Machine d'état : INIT_SESSION → SCHEDULER → SEQUENCER → REDUCTION → SHARING_ARCHIVE → CLOSE_SESSION
"""

from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable
import asyncio
import logging
import uuid

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
    version="0.3.0"
)

# ─────────────────────────────────────────────────────────────
# Modèles de données
# ─────────────────────────────────────────────────────────────

class OrchestratorStage(str, Enum):
    IDLE            = "idle"
    INIT_SESSION    = "init_session"
    SCHEDULER       = "scheduler"
    SEQUENCER       = "sequencer"
    REDUCTION       = "reduction"
    SHARING_ARCHIVE = "sharing_archive"
    CLOSE_SESSION   = "close_session"
    ERROR           = "error"

class TargetType(str, Enum):
    SCIENCE      = "science"
    CALIBRATION  = "calibration"
    FLAT         = "flat"
    DARK         = "dark"

class Target(BaseModel):
    name: str
    ra: float = Field(..., ge=0, lt=360, description="Ascension droite en degrés")
    dec: float = Field(..., ge=-90, le=90, description="Déclinaison en degrés")
    magnitude: Optional[float] = None
    target_type: TargetType = TargetType.SCIENCE

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
    status: str = "pending"
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    files: list[str] = []

# ─────────────────────────────────────────────────────────────
# Contexte de session
# Transmis de stage en stage ; chaque plugin peut l'enrichir.
# ─────────────────────────────────────────────────────────────

class SessionContext(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    started_at: datetime = Field(default_factory=datetime.utcnow)
    current_observation: Optional[Observation] = None
    # Fichiers produits par le SEQUENCER, consommés par REDUCTION et SHARING_ARCHIVE
    raw_files: list[str] = []
    reduced_files: list[str] = []

    class Config:
        arbitrary_types_allowed = True

# ─────────────────────────────────────────────────────────────
# Protocol Plugin
# Chaque étape expose un plugin qui implémente cette interface.
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
# Plugins stubs (à remplacer par les implémentations réelles)
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
                return ctx
            await asyncio.sleep(2.0)

class SequencerPlugin:
    """Exécute la séquence instrumentale : pointage, config, expositions."""
    async def run(self, ctx: SessionContext) -> SessionContext:
        obs = ctx.current_observation
        if not obs:
            raise RuntimeError("SEQUENCER : aucune observation dans le contexte")

        log.info(f"[SEQUENCER] Début séquence pour {obs.request.target.name}")
        obs.status = "running"
        obs.started_at = datetime.utcnow()
        await db_update_observation(obs)

        target   = obs.request.target
        exposure = obs.request.exposure

        # 1. Pointage
        log.info(f"[SEQUENCER] Pointage vers RA={target.ra} Dec={target.dec}")
        await asyncio.sleep(2.0)  # TODO : driver télescope

        # 2. Configuration spectrographe
        log.info(f"[SEQUENCER] Configuration réseau {exposure.grating} @ {exposure.central_wavelength} Å")
        await asyncio.sleep(1.0)  # TODO : driver spectrographe

        # 3. Acquisition sur fente (science uniquement)
        if target.target_type == TargetType.SCIENCE:
            log.info("[SEQUENCER] Acquisition sur fente")
            await asyncio.sleep(1.5)  # TODO : guidage / centrage

        # 4. Séquence d'expositions
        for i in range(exposure.num_exposures):
            log.info(f"[SEQUENCER] Exposition {i + 1}/{exposure.num_exposures} ({exposure.exposure_time}s)")
            await asyncio.sleep(exposure.exposure_time * 0.1)  # Simulation accélérée
            filename = f"/data/{obs.id}/spectrum_{i + 1:04d}.fits"
            obs.files.append(filename)
            ctx.raw_files.append(filename)

        obs.status = "sequenced"
        await db_update_observation(obs)
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

class SharingArchivePlugin:
    """Archive et diffuse les fichiers réduits."""
    async def run(self, ctx: SessionContext) -> SessionContext:
        if not ctx.reduced_files:
            raise RuntimeError("SHARING_ARCHIVE : aucun fichier réduit dans le contexte")

        obs = ctx.current_observation
        log.info(f"[SHARING_ARCHIVE] Archivage de {len(ctx.reduced_files)} fichier(s)")
        for f in ctx.reduced_files:
            await asyncio.sleep(0.3)  # TODO : transfert vers archive / base de données
            log.info(f"[SHARING_ARCHIVE] Archivé : {f}")

        if obs:
            obs.status = "completed"
            obs.completed_at = datetime.utcnow()
            await db_update_observation(obs)

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
    OrchestratorStage.INIT_SESSION:    InitSessionPlugin(),
    OrchestratorStage.SCHEDULER:       SchedulerPlugin(),
    OrchestratorStage.SEQUENCER:       SequencerPlugin(),
    OrchestratorStage.REDUCTION:       ReductionPlugin(),
    OrchestratorStage.SHARING_ARCHIVE: SharingArchivePlugin(),
    OrchestratorStage.CLOSE_SESSION:   CloseSessionPlugin(),
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
# Interface SQLite — stubs à implémenter
# ─────────────────────────────────────────────────────────────

async def db_fetch_next_observation() -> Optional[Observation]:
    """
    Récupère la prochaine observation depuis la base SQLite.
    Requête attendue :
        SELECT * FROM observations
        WHERE status = 'pending'
        ORDER BY priority DESC, created_at ASC
        LIMIT 1
    """
    # TODO : implémenter avec aiosqlite
    raise NotImplementedError("db_fetch_next_observation() doit être implémentée")

async def db_update_observation(obs: Observation) -> None:
    """
    Met à jour le statut d'une observation dans la base SQLite.
    Requête attendue :
        UPDATE observations SET status=?, started_at=?, completed_at=?, files=?
        WHERE id=?
    """
    # TODO : implémenter avec aiosqlite
    raise NotImplementedError("db_update_observation() doit être implémentée")

# ─────────────────────────────────────────────────────────────
# Machine d'état principale
# ─────────────────────────────────────────────────────────────

# Boucle d'observations : stages exécutés en cycle
OBSERVATION_LOOP: list[OrchestratorStage] = [
    OrchestratorStage.SCHEDULER,
    OrchestratorStage.SEQUENCER,
    OrchestratorStage.REDUCTION,
    OrchestratorStage.SHARING_ARCHIVE,
]

async def run_stage(stage: OrchestratorStage, ctx: SessionContext) -> SessionContext:
    """Exécute un stage via son plugin et met à jour l'état global."""
    state.stage = stage
    plugin = plugins[stage]
    return await plugin.run(ctx)

async def session_runner():
    """
    Boucle principale de la session.
    INIT_SESSION → [SCHEDULER → SEQUENCER → REDUCTION → SHARING_ARCHIVE] × N → CLOSE_SESSION
    """
    ctx = SessionContext()

    # ── Initialisation (une seule fois) ──────────────────────
    try:
        ctx = await run_stage(OrchestratorStage.INIT_SESSION, ctx)
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
            except Exception as e:
                log.error(f"[{stage.value.upper()}] Erreur : {e} — retour au SCHEDULER")
                # Marquer l'observation courante en erreur si applicable
                if ctx.current_observation:
                    ctx.current_observation.status = f"error: {e}"
                    ctx.current_observation.completed_at = datetime.utcnow()
                    try:
                        await db_update_observation(ctx.current_observation)
                        state.completed.append(ctx.current_observation)
                    except Exception:
                        pass
                # Réinitialiser le contexte et reprendre depuis le SCHEDULER
                ctx = SessionContext(session_id=ctx.session_id, started_at=ctx.started_at)
                break  # sort du for, relance le while → retour au SCHEDULER
        else:
            # Boucle for terminée sans break : observation réussie
            if ctx.current_observation:
                state.completed.append(ctx.current_observation)

    # ── Clôture (une seule fois) ──────────────────────────────
    try:
        ctx = await run_stage(OrchestratorStage.CLOSE_SESSION, ctx)
    except Exception as e:
        log.error(f"[CLOSE_SESSION] Erreur lors de la clôture : {e}")
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
    """Retourne les 20 dernières observations complétées."""
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
