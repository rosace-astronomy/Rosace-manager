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
from typing import Optional

from contextlib import asynccontextmanager

import yaml
from fastapi import FastAPI, BackgroundTasks, HTTPException, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
from .plugins.schedulers import SchedulerFatalError
from .simbad   import simbad_search, SimbadNotFoundError, SimbadQueryError
from .targets  import (
    targets_db_init,
    Star, StarCreate, StarLookup, StarInProgram, Program, ProgramCreate,
    star_insert, star_get, star_get_by_name, star_list, star_update, star_delete,
    star_add_alias, star_list_aliases,
    program_insert, program_get, program_get_by_name, program_list, program_delete,
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
        # True for the entire lifetime of the session_runner() coroutine, from just
        # before it starts until it has fully wound down (INIT_SESSION failure,
        # normal completion through CLOSE_SESSION, or any other exit path) — distinct
        # from is_running, which flips to False as soon as a stop is *requested*,
        # well before the coroutine actually finishes cleaning up. /control/start
        # checks this (not is_running) so a second session can never overlap a first
        # one that's still winding down.
        self.session_active: bool = False
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

    state.session_active stays True for the whole call, on every exit path —
    see SystemState's docstring comment for why this must be distinct from
    state.is_running.
    """
    state.session_active = True
    try:
        await _session_runner_body()
    finally:
        state.session_active = False


async def _session_runner_body():
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

            except SchedulerFatalError as e:
                log.error(f"[SCHEDULER] Fatal error, stopping session: {e}")
                if obs:
                    await db_set_stage_status(obs.id, stage, "error")
                    await db_set_error(obs.id, str(e))
                    write_observation_file(obs)
                state.is_running = False
                obs = None
                break
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
        "session_active": state.session_active,
        "completed_count": len(state.completed),
        "pending_post_obs_count": state.pending_post_obs,
    }

@app.get("/observations/completed")
async def list_completed():
    """Returns the last 20 completed observations (in-memory cache)."""
    return {"completed": state.completed[-20:]}


@app.get("/observations/recent")
async def list_recent_observations(limit: int | None = None):
    """Returns observations from the current astronomical day, with per-stage statuses and
    timestamps from the database. No limit by default — see db_get_recent_observations()."""
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
    """Démarre une nouvelle session d'observation. Refuse tant qu'une session
    précédente n'a pas complètement terminé son arrêt (session_active), pas
    seulement tant qu'un arrêt a été demandé (is_running) — sinon une seconde
    session pourrait démarrer pendant que la première finit son nettoyage."""
    if state.session_active:
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


async def _ensure_alias(star: Star, name: str) -> bool:
    """Enregistre `name` comme alias de `star` s'il n'est ni son nom canonique
    ni un alias déjà connu. Retourne True si un nouvel alias a été ajouté."""
    known_names = {star.name.lower()} | {a.lower() for a in await star_list_aliases(star.id)}
    if name.lower() not in known_names:
        await star_add_alias(star.id, name)
        return True
    return False


async def _import_star_from_simbad(name: str) -> tuple[Star, bool, bool]:
    """
    Ajoute une étoile à la base, identifiée par un nom quelconque — pas besoin
    de connaître son nom canonique Simbad. Recherche d'abord si une étoile
    correspondant à `name` (nom canonique ou alias) existe déjà :
    - si oui, enregistre `name` comme alias si nouveau, renvoie l'étoile
      existante (created=False) plutôt que d'en créer un doublon ;
    - sinon, interroge Simbad pour les données astrométriques. Simbad peut
      résoudre `name` vers une identité canonique déjà présente en base sous
      un tout autre terme de recherche (ex. "Sheliak" et "bet Lyr" sont la
      même étoile) — on revérifie donc par ce nom canonique avant d'insérer :
      si une étoile correspond déjà, même traitement que le cas "existant"
      ci-dessus ; sinon seulement, insère la nouvelle étoile (nom canonique =
      celui retourné par Simbad), enregistre `name` comme alias si différent
      du nom canonique, renvoie la nouvelle étoile (created=True).
    Retourne (star, created, alias_added) — alias_added indique si CET appel
    a enregistré un nouvel alias (utile pour que l'appelant sache, sans avoir
    à aller vérifier la base, si `name` a bien été retenu comme alias).
    Lève SimbadNotFoundError / SimbadQueryError si l'étoile est réellement
    nouvelle et que Simbad ne la reconnaît pas / ne répond pas.
    """
    existing = await star_get_by_name(name)
    if existing:
        alias_added = await _ensure_alias(existing, name)
        return existing, False, alias_added

    star_create = await simbad_search(name)

    existing_by_canonical = await star_get_by_name(star_create.name)
    if existing_by_canonical:
        alias_added = await _ensure_alias(existing_by_canonical, name)
        return existing_by_canonical, False, alias_added

    star_id = await star_insert(star_create)
    alias_added = False
    if name.lower() != star_create.name.lower():
        await star_add_alias(star_id, name)
        alias_added = True

    return await star_get(star_id), True, alias_added


class StarImportResponse(Star):
    alias_added: bool  # True si `name` (le terme recherché) a été enregistré comme alias


@app.post("/simbad/import", response_model=StarImportResponse)
async def simbad_import_endpoint(name: str, response: Response):
    """Ajoute une étoile à la base via Simbad — voir _import_star_from_simbad()."""
    try:
        star, created, alias_added = await _import_star_from_simbad(name)
    except SimbadNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SimbadQueryError as e:
        raise HTTPException(status_code=502, detail=str(e))

    response.status_code = 201 if created else 200
    return StarImportResponse(**star.model_dump(), alias_added=alias_added)


class StarImportResult(BaseModel):
    name: str
    status: str  # "created" | "existing" | "error"
    id: Optional[int] = None
    canonical_name: Optional[str] = None
    alias_added: bool = False
    detail: Optional[str] = None


@app.post("/stars/import-list", response_model=list[StarImportResult])
async def import_stars_from_file(file: UploadFile):
    """
    Importe une liste d'étoiles (un nom par ligne, fichier texte uploadé) —
    chaque ligne suit exactement le même chemin que /simbad/import. Rapport
    ligne par ligne : une erreur sur une ligne n'interrompt pas les suivantes.
    """
    content = (await file.read()).decode("utf-8")
    results: list[StarImportResult] = []

    for line in content.splitlines():
        name = line.strip()
        if not name:
            continue
        try:
            star, created, alias_added = await _import_star_from_simbad(name)
            results.append(StarImportResult(
                name=name, status="created" if created else "existing",
                id=star.id, canonical_name=star.name, alias_added=alias_added,
            ))
        except (SimbadNotFoundError, SimbadQueryError) as e:
            results.append(StarImportResult(name=name, status="error", detail=str(e)))

    return results


class ProgramStarImportResult(BaseModel):
    program: str
    star: str
    status: str  # "linked" | "program_not_found" | "star_not_found"
    detail: Optional[str] = None


@app.post("/programs/import-list", response_model=list[ProgramStarImportResult])
async def import_program_stars_from_file(file: UploadFile):
    """
    Importe des liens programme <-> étoiles depuis un fichier YAML uploadé
    (format : {nom_programme: [nom_etoile, ...], ...}). Les étoiles doivent
    déjà exister en base (nom canonique ou alias) — pas d'appel Simbad ici,
    contrairement à /stars/import-list. Rapport ligne par ligne.
    """
    content = (await file.read()).decode("utf-8")
    data = yaml.safe_load(content) or {}
    results: list[ProgramStarImportResult] = []

    for program_name, star_names in data.items():
        program = await program_get_by_name(program_name)
        for star_name in star_names or []:
            if not program:
                results.append(ProgramStarImportResult(
                    program=program_name, star=star_name, status="program_not_found",
                    detail=f"Programme '{program_name}' introuvable en base",
                ))
                continue

            star = await star_get_by_name(star_name)
            if not star:
                results.append(ProgramStarImportResult(
                    program=program_name, star=star_name, status="star_not_found",
                    detail=f"Étoile '{star_name}' introuvable en base",
                ))
                continue

            await program_add_star(program.id, star.id)
            results.append(ProgramStarImportResult(
                program=program_name, star=star_name, status="linked",
            ))

    return results

# ─────────────────────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rosace.main:app", host="0.0.0.0", port=8000)

# ─────────────────────────────────────────────────────────────
# Endpoints — Étoiles
# ─────────────────────────────────────────────────────────────
# Adressées par nom, jamais par id : l'id SQLite est un détail d'implémentation
# qui ne concerne pas l'utilisateur (voir star_get_by_name : nom canonique
# puis, en repli, table d'alias).

@app.get("/stars", response_model=list[Star])
async def list_stars():
    """Liste toutes les étoiles."""
    return await star_list()

@app.get("/stars/{name}", response_model=StarLookup)
async def get_star(name: str):
    """Recherche une étoile par son nom (canonique ou alias)."""
    star = await star_get_by_name(name)
    if not star:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    return StarLookup(**star.model_dump(), queried_name=name)

@app.put("/stars/{name}", response_model=Star)
async def update_star(name: str, star: StarCreate):
    """Met à jour une étoile, recherchée par son nom (canonique ou alias)."""
    existing = await star_get_by_name(name)
    if not existing:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    await star_update(existing.id, star)
    return await star_get(existing.id)

@app.delete("/stars/{name}", status_code=204)
async def delete_star(name: str):
    """Supprime une étoile, recherchée par son nom (canonique ou alias)."""
    existing = await star_get_by_name(name)
    if not existing:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    await star_delete(existing.id)

@app.get("/stars/{name}/programs", response_model=list[Program])
async def get_star_programs(name: str):
    """Retourne les programmes auxquels appartient une étoile."""
    existing = await star_get_by_name(name)
    if not existing:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    return await star_get_programs(existing.id)

# ─────────────────────────────────────────────────────────────
# Endpoints — Programmes scientifiques
# ─────────────────────────────────────────────────────────────
# Adressées par nom, jamais par id — même principe que pour les étoiles.

@app.post("/programs", response_model=Program, status_code=201)
async def create_program(program: ProgramCreate):
    """Crée un programme scientifique."""
    prog_id = await program_insert(program)
    return await program_get(prog_id)

@app.get("/programs", response_model=list[Program])
async def list_programs():
    """Liste tous les programmes."""
    return await program_list()

@app.get("/programs/{name}", response_model=Program)
async def get_program(name: str):
    """Récupère un programme par son nom."""
    program = await program_get_by_name(name)
    if not program:
        raise HTTPException(status_code=404, detail="Programme non trouvé")
    return program

@app.delete("/programs/{name}", status_code=204)
async def delete_program(name: str):
    """Supprime un programme, recherché par son nom."""
    program = await program_get_by_name(name)
    if not program:
        raise HTTPException(status_code=404, detail="Programme non trouvé")
    await program_delete(program.id)

@app.get("/programs/{name}/stars", response_model=list[StarInProgram])
async def get_program_stars(name: str):
    """Retourne les étoiles d'un programme, avec leur temps de pose éventuel."""
    program = await program_get_by_name(name)
    if not program:
        raise HTTPException(status_code=404, detail="Programme non trouvé")
    return await program_get_stars(program.id)

@app.post("/programs/{name}/stars/{star_name}", status_code=204)
async def add_star_to_program(name: str, star_name: str, exposure_time: Optional[float] = None):
    """Associe une étoile à un programme, avec un temps de pose optionnel
    spécifique à ce couple programme/étoile."""
    program = await program_get_by_name(name)
    if not program:
        raise HTTPException(status_code=404, detail="Programme non trouvé")
    star = await star_get_by_name(star_name)
    if not star:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    await program_add_star(program.id, star.id, exposure_time)

@app.delete("/programs/{name}/stars/{star_name}", status_code=204)
async def remove_star_from_program(name: str, star_name: str):
    """Retire une étoile d'un programme."""
    program = await program_get_by_name(name)
    if not program:
        raise HTTPException(status_code=404, detail="Programme non trouvé")
    star = await star_get_by_name(star_name)
    if not star:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    await program_remove_star(program.id, star.id)

# ─────────────────────────────────────────────────────────────
# Interface web (fichiers statiques du GUI)
# ─────────────────────────────────────────────────────────────
# Monté en dernier : les routes API ci-dessus restent prioritaires.

GUI_DIR = Path(__file__).parent / "gui" / "bin"

app.mount("/", StaticFiles(directory=GUI_DIR, html=True), name="gui")
