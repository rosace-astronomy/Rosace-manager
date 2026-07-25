"""
Orchestrateur spectroscopique — point d'entrée principal.
Machine d'état : INIT_SESSION → SCHEDULER → SEQUENCER → REDUCTION → SHARING → ARCHIVE → CLOSE_SESSION
"""

#─────────────────────────────────────────────────────────────
# Quelques TODO...
# Mettre en contener (docker) ?
# Mettre en place les plugins pour chaque étape d'observation et de
# réduction de données
# Faire une version 0.0.1, qui permette de faire une démonstration 
# Faire un chargement dynamique des plugis (sequencer et réduction)
# Créer un fichier YAML pour une démo
# Faire une séquence d'observations à partir d'une liste d'étoiles en fichier texte
#─────────────────────────────────────────────────────────────

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .config     import SITE_NAME, SCHEDULER_RETRY_SECONDS
from .log_config import configure_logging

configure_logging()

from .log_broadcast import ws_log_handler

from .models   import (
    OrchestratorStage, Observation, SessionContext,
)
from .database import (
    db_init, db_insert_session, db_close_session,
    db_insert_observation, db_set_yaml_file,
    db_stamp_stage, db_set_error,
    db_set_stage_status, db_get_recent_observations,
)
from .observation_file import create_observation_file, write_observation_file
from .plugins  import plugins
from .simbad   import simbad_search, SimbadNotFoundError, SimbadQueryError
from .targets  import (
    targets_db_init,
    Star, StarCreate, Program, ProgramCreate,
    star_insert, star_get, star_get_by_name, star_list, star_update, star_delete,
    program_insert, program_get, program_list, program_delete,
    program_add_star, program_remove_star, program_get_stars, star_get_programs,
)

log = logging.getLogger("spectro.main")

# ─────────────────────────────────────────────────────────────
# Application FastAPI
# ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    await db_init()
    await targets_db_init()
    ws_log_handler.bind_loop(asyncio.get_running_loop())
    logging.getLogger("spectro").addHandler(ws_log_handler)
    yield

app = FastAPI(
    title="Spectro Orchestrator",
    description="Orchestrateur d'observations spectroscopiques",
    version="0.5.0",
    lifespan=lifespan,
)

# ─────────────────────────────────────────────────────────────
# État global du système
# ─────────────────────────────────────────────────────────────

class SystemState:
    def __init__(self):
        self.stage: OrchestratorStage = OrchestratorStage.IDLE
        self.is_running: bool = False
        self.completed: list[Observation] = []
        self.pending_post_obs: int = 0

state = SystemState()

# ─────────────────────────────────────────────────────────────
# Machine à états
# ─────────────────────────────────────────────────────────────

OBS_STAGES: list[OrchestratorStage] = [
    OrchestratorStage.SCHEDULER,
    OrchestratorStage.SEQUENCER,
]

POST_OBS_STAGES: list[OrchestratorStage] = [
    OrchestratorStage.REDUCTION,
    OrchestratorStage.SHARING,
    OrchestratorStage.ARCHIVE,
]

async def run_stage(
    stage: OrchestratorStage, ctx: SessionContext | Observation
) -> SessionContext | Observation:
    """Executes a stage via its plugin and updates the global state."""
    state.stage = stage
    return await plugins[stage].run(ctx)


async def run_post_obs(obs: Observation) -> None:
    """Runs REDUCTION → SHARING → ARCHIVE in the background for a completed observation."""
    state.pending_post_obs += 1
    try:
        for stage in POST_OBS_STAGES:
            try:
                await db_set_stage_status(obs.id, stage, "running")
                obs = await plugins[stage].run(obs)
                await db_stamp_stage(obs.id, stage)  # sets done timestamp + status='done'
                write_observation_file(obs)
            except Exception as e:
                log.error(f"[{stage.value.upper()}] Background error: {e}")
                await db_set_stage_status(obs.id, stage, "error")
                await db_set_error(obs.id, str(e))
                write_observation_file(obs)
                break
    finally:
        state.pending_post_obs -= 1


SLEEP_STEP_SECONDS = 1.0  # polling granularity for _interruptible_sleep()

async def _interruptible_sleep(seconds: float) -> None:
    """Sleeps in small steps, re-checking state.is_running — a stop request takes effect
    promptly (within one SLEEP_STEP_SECONDS step) instead of only after the full delay."""
    elapsed = 0.0
    while elapsed < seconds and state.is_running:
        await asyncio.sleep(min(SLEEP_STEP_SECONDS, seconds - elapsed))
        elapsed += SLEEP_STEP_SECONDS


async def session_runner():
    """
    Main session loop.
    INIT_SESSION → [SCHEDULER → SEQUENCER] × N (foreground)
                 → [REDUCTION → SHARING → ARCHIVE] × N (background, overlapped)
                 → CLOSE_SESSION
    """
    await db_init()
    await targets_db_init()
    session = SessionContext(site_name=SITE_NAME)

    try:
        session = await run_stage(OrchestratorStage.INIT_SESSION, session)
        session_dir = Path(session.session_dir)
        await db_insert_session(session)
    except Exception as e:
        log.error(f"[INIT_SESSION] Critical failure, session cancelled: {e}")
        state.stage = OrchestratorStage.ERROR
        state.is_running = False
        return

    post_obs_tasks: list[asyncio.Task] = []
    obs: Observation | None = None

    while state.is_running:
        for stage in OBS_STAGES:
            if not state.is_running:
                break
            try:
                if stage == OrchestratorStage.SCHEDULER:
                    # L'orchestrateur crée le dossier vide ; SCHEDULER le remplit.
                    obs = Observation(
                        id=str(uuid.uuid4())[:8],
                        created_at=datetime.now(timezone.utc),
                        session_id=session.session_id,
                        obs_dir=str(session_dir),
                    )
                else:
                    await db_set_stage_status(obs.id, stage, "running")

                obs = await run_stage(stage, obs)

                if stage == OrchestratorStage.SCHEDULER and obs.scheduler_data.get("target") is None:
                    log.info(f"[SCHEDULER] Nothing to observe — retrying in {SCHEDULER_RETRY_SECONDS}s")
                    obs = None
                    await _interruptible_sleep(SCHEDULER_RETRY_SECONDS)
                    break

                if stage == OrchestratorStage.SCHEDULER:
                    await db_insert_observation(obs, session.session_id)
                    yaml_path = create_observation_file(obs, session_dir)
                    await db_set_yaml_file(obs.id, str(yaml_path))
                else:
                    await db_stamp_stage(obs.id, stage)

                write_observation_file(obs)

            except Exception as e:
                log.error(f"[{stage.value.upper()}] Error: {e} — restarting from SCHEDULER")
                if obs:
                    await db_set_stage_status(obs.id, stage, "error")
                    await db_set_error(obs.id, str(e))
                    write_observation_file(obs)
                obs = None
                break
        else:
            # SEQUENCER succeeded: record observation and launch post-obs in background
            state.completed.append(obs)
            task = asyncio.create_task(run_post_obs(obs.model_copy(deep=True)))
            post_obs_tasks.append(task)
            obs = None

    # Wait for all background post-obs tasks before closing the session
    if post_obs_tasks:
        log.info(f"[ORCHESTRATOR] Waiting for {len(post_obs_tasks)} background post-obs task(s)...")
        await asyncio.gather(*post_obs_tasks, return_exceptions=True)

    try:
        session = await run_stage(OrchestratorStage.CLOSE_SESSION, session)
        await db_close_session(session, status="closed")
    except Exception as e:
        log.error(f"[CLOSE_SESSION] Error: {e}")
        await db_close_session(session, status="error")
    finally:
        state.stage = OrchestratorStage.IDLE

# ─────────────────────────────────────────────────────────────
# Endpoints API
# ─────────────────────────────────────────────────────────────

@app.get("/status")
async def get_status():
    """Returns the current orchestrator state."""
    return {
        "stage": state.stage,
        "is_running": state.is_running,
        "completed_count": len(state.completed),
        "pending_post_obs_count": state.pending_post_obs,
    }

@app.get("/observations/completed")
async def list_completed():
    """Returns the last 20 completed observations (in-memory cache)."""
    return {"completed": state.completed[-20:]}


@app.get("/observations/recent")
async def list_recent_observations(limit: int = 20):
    """Returns recent observations with per-stage statuses and timestamps from the database."""
    return await db_get_recent_observations(limit)

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
    log.info("[ORCHESTRATOR] Arrêt demandé — CLOSE_SESSION sera exécuté en fin de cycle")
    return {"message": "Arrêt demandé — CLOSE_SESSION sera exécuté en fin de cycle"}


WS_PING_INTERVAL = 30  # seconds — matches the 'ping' heartbeat convention of x4js's setupSocketMessaging()

@app.websocket("/ws")
async def ws_logs(websocket: WebSocket):
    """Streams spectro logger records (INFO+) to connected GUI clients, X4 'messaging' protocol."""
    await websocket.accept(subprotocol="messaging")
    ws_log_handler.add_client(websocket)
    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=WS_PING_INTERVAL)
            except asyncio.TimeoutError:
                await websocket.send_text("ping")
    except WebSocketDisconnect:
        pass
    finally:
        ws_log_handler.remove_client(websocket)


# ─────────────────────────────────────────────────────────────
# Endpoints — Recherche Simbad
# ─────────────────────────────────────────────────────────────

@app.get("/simbad/search", response_model=StarCreate)
async def simbad_search_endpoint(name: str):
    """
    Interroge Simbad et retourne les informations de l'étoile,
    sans l'insérer en base. Utile pour prévisualiser avant insertion.
    """
    try:
        return await simbad_search(name)
    except SimbadNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SimbadQueryError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/simbad/import", response_model=Star, status_code=201)
async def simbad_import_endpoint(name: str):
    """
    Interroge Simbad et insère l'étoile en base.
    Retourne une erreur 409 si l'étoile existe déjà.
    """
    try:
        star_create = await simbad_search(name)
    except SimbadNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SimbadQueryError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Vérification doublon (par nom principal retourné par Simbad)
    existing = await star_get_by_name(star_create.name)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"L'étoile '{star_create.name}' existe déjà en base (id={existing.id})"
        )

    star_id = await star_insert(star_create)
    return await star_get(star_id)

# ─────────────────────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rosace.main:app", host="0.0.0.0", port=8000)

# ─────────────────────────────────────────────────────────────
# Endpoints — Étoiles
# ─────────────────────────────────────────────────────────────

@app.post("/stars", response_model=Star, status_code=201)
async def create_star(star: StarCreate):
    """Ajoute une étoile à la base de cibles."""
    star_id = await star_insert(star)
    return await star_get(star_id)

@app.get("/stars", response_model=list[Star])
async def list_stars():
    """Liste toutes les étoiles."""
    return await star_list()

@app.get("/stars/{star_id}", response_model=Star)
async def get_star(star_id: int):
    """Récupère une étoile par son id."""
    star = await star_get(star_id)
    if not star:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    return star

@app.put("/stars/{star_id}", response_model=Star)
async def update_star(star_id: int, star: StarCreate):
    """Met à jour une étoile."""
    await star_update(star_id, star)
    return await star_get(star_id)

@app.delete("/stars/{star_id}", status_code=204)
async def delete_star(star_id: int):
    """Supprime une étoile."""
    await star_delete(star_id)

@app.get("/stars/{star_id}/programs", response_model=list[Program])
async def get_star_programs(star_id: int):
    """Retourne les programmes auxquels appartient une étoile."""
    return await star_get_programs(star_id)

# ─────────────────────────────────────────────────────────────
# Endpoints — Programmes scientifiques
# ─────────────────────────────────────────────────────────────

@app.post("/programs", response_model=Program, status_code=201)
async def create_program(program: ProgramCreate):
    """Crée un programme scientifique."""
    prog_id = await program_insert(program)
    return await program_get(prog_id)

@app.get("/programs", response_model=list[Program])
async def list_programs():
    """Liste tous les programmes."""
    return await program_list()

@app.get("/programs/{program_id}", response_model=Program)
async def get_program(program_id: int):
    """Récupère un programme par son id."""
    program = await program_get(program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Programme non trouvé")
    return program

@app.delete("/programs/{program_id}", status_code=204)
async def delete_program(program_id: int):
    """Supprime un programme."""
    await program_delete(program_id)

@app.get("/programs/{program_id}/stars", response_model=list[Star])
async def get_program_stars(program_id: int):
    """Retourne les étoiles d'un programme."""
    return await program_get_stars(program_id)

@app.post("/programs/{program_id}/stars/{star_id}", status_code=204)
async def add_star_to_program(program_id: int, star_id: int):
    """Associe une étoile à un programme."""
    await program_add_star(program_id, star_id)

@app.delete("/programs/{program_id}/stars/{star_id}", status_code=204)
async def remove_star_from_program(program_id: int, star_id: int):
    """Retire une étoile d'un programme."""
    await program_remove_star(program_id, star_id)

# ─────────────────────────────────────────────────────────────
# Interface web (fichiers statiques du GUI)
# ─────────────────────────────────────────────────────────────
# Monté en dernier : les routes API ci-dessus restent prioritaires.

GUI_DIR = Path(__file__).parent / "gui" / "bin"

app.mount("/", StaticFiles(directory=GUI_DIR, html=True), name="gui")
