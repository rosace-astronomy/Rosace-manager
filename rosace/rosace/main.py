# Copyright (C) 2026 The ROSACE project
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
Spectroscopic orchestrator — main entry point.
State machine: INIT_SESSION → SCHEDULER → SEQUENCER → REDUCTION → SHARING → ARCHIVE → CLOSE_SESSION
"""

# Command-line options (cli.py) must be handled before `from .config import ...`
# below: some of them choose which config.yaml gets loaded.
import sys

if __name__ == "__main__":
    from rosace.cli import run_cli
    run_cli(sys.argv[1:])

import asyncio
import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from contextlib import asynccontextmanager
from importlib import metadata

import yaml
from fastapi import (
    APIRouter, Depends, FastAPI, HTTPException,
    Request, Response, UploadFile, WebSocket, WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config     import GUI_PREVIEW_DIR, SITE_NAME, SITE_LONGITUDE_DEG, SCHEDULER_RETRY_SECONDS, SEQUENCER_BACKEND, SESSION_COOKIE_SECURE, SESSION_TTL_HOURS, IS_DEMO_MODE
from .log_config import configure_logging

configure_logging()

from .log_broadcast import ws_log_handler
from .image_broadcast import ws_image_broadcaster

from .auth import (
    auth_db_init, get_current_user, require_admin,
    UserCreate, UserOut, LoginRequest,
    user_create, user_list, user_delete, _user_row_by_username, _verify_password,
    session_create, session_delete, session_get_user, SESSION_COOKIE_NAME,
)
from .models   import (
    OrchestratorStage, Observation, SessionContext,
)
from .database import (
    db_init, db_insert_session, db_close_session,
    db_insert_observation, db_set_yaml_file,
    db_stamp_stage, db_set_error,
    db_set_stage_status, db_get_recent_observations, db_record_measurements,
)
from .bess_validation_api import router as bess_validation_router
from .demo_seed import seed_demo_data
from .hardware_replay.fake_phd2 import start_fake_phd2
from .control_schedule import next_occurrence
from .errors import SessionFatalError
from .observation_file import create_observation_file, write_observation_file
from .plugins  import plugins
from .plugins.pipeline_loader import get_display_names
from .plugins.hardware_connectivity import test_hardware
from . import step_progress
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
    await auth_db_init()
    fake_phd2 = None
    if IS_DEMO_MODE:
        await seed_demo_data()
        try:
            fake_phd2 = await start_fake_phd2()
        except OSError as e:
            log.error(f"Fake PHD2 not started: {e}")
    ws_log_handler.bind_loop(asyncio.get_running_loop())
    ws_image_broadcaster.bind_loop(asyncio.get_running_loop())
    logging.getLogger("spectro").addHandler(ws_log_handler)
    schedule_task = asyncio.create_task(_control_schedule_loop())
    yield
    schedule_task.cancel()
    if fake_phd2 is not None:
        await fake_phd2.stop()

def _rosace_version() -> str:
    # The one version number is the package's own (pyproject.toml)
    try:
        return metadata.version("rosace")
    except metadata.PackageNotFoundError:
        return "unknown"


app = FastAPI(
    title="Spectro Orchestrator",
    description="Orchestrateur d'observations spectroscopiques",
    version=_rosace_version(),
    lifespan=lifespan,
)

# Any route on "protected" requires a valid session; "admin_router" additionally
# requires is_admin. Default-closed: only /auth/login and the static GUI (mounted
# directly on "app" further below) stay public — a new route must consciously
# choose to be public, rather than the reverse (an allowlist someone could forget
# to update). /ws (WebSocket) is deliberately not part of this: an HTTPException
# raised by Depends() doesn't close a WebSocket handshake cleanly — it's checked
# by hand, inside ws_logs() itself.
protected = APIRouter(dependencies=[Depends(get_current_user)])
admin_router = APIRouter(prefix="/auth/users", dependencies=[Depends(require_admin)])

# ─────────────────────────────────────────────────────────────
# Global system state
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
        # Session start/stop scheduled from the GUI (aware UTC instants, None when
        # nothing is scheduled) -- in memory only, lost on a server restart.
        self.scheduled_start: datetime | None = None
        self.scheduled_stop: datetime | None = None
        # True while the GUI's hardware test runs: no session may start then
        # (both would connect the same devices at the same time).
        self.hardware_test_running: bool = False

state = SystemState()

# ─────────────────────────────────────────────────────────────
# State machine
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


async def _persist_observation(obs: Observation) -> None:
    """Writes the observation's YAML dossier and copies its measurements into
    rosace.db (the columns declared by database.py) — after every stage, on
    success and on error alike. Plugin-agnostic: obs.measurements is a plain
    dict, filled by sub-plugins main.py knows nothing about."""
    write_observation_file(obs)
    await db_record_measurements(obs.id, obs.measurements)


# One worker thread, with its own event loop per observation, runs the post-obs
# stages: a blocking computation in a REDUCTION sub-plugin never delays the
# sequencer, whose loop stays free. A single worker on purpose: observations are
# processed one at a time, in order, so steps that share files across a session
# (export_demetra's session ZIP, appended to by every observation) never run
# concurrently.
_post_obs_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="post-obs")


async def run_post_obs(obs: Observation) -> None:
    """Runs REDUCTION → SHARING → ARCHIVE for a completed observation. Runs in the
    post-obs thread's own event loop (see _launch_post_obs()), never on the main one."""
    for stage in POST_OBS_STAGES:
        try:
            await db_set_stage_status(obs.id, stage, "running")
            obs = await plugins[stage].run(obs)
            await db_stamp_stage(obs.id, stage)  # sets done timestamp + status='done'
            await _persist_observation(obs)
        except Exception as e:
            log.error(f"[{stage.value.upper()}] Background error: {e}")
            await db_set_stage_status(obs.id, stage, "error")
            await db_set_error(obs.id, str(e))
            await _persist_observation(obs)
            break


async def _launch_post_obs(obs: Observation) -> None:
    """Hands an observation to the post-obs thread and waits for it there, on the
    main loop — state.pending_post_obs is therefore only ever touched from the main
    loop, never from the worker thread."""
    state.pending_post_obs += 1
    try:
        await asyncio.wrap_future(_post_obs_executor.submit(asyncio.run, run_post_obs(obs)))
    except Exception as e:
        log.error(f"[ORCHESTRATOR] Post-obs failed for {obs.id}: {e}")
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
                 → [REDUCTION → SHARING → ARCHIVE] × N (post-obs thread, one
                   observation at a time, overlapping the next observations)
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
    session.stop_check = lambda: not state.is_running

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
                    # The orchestrator creates the empty dossier; SCHEDULER fills it in.
                    obs = Observation(
                        id=str(uuid.uuid4())[:8],
                        created_at=datetime.now(timezone.utc),
                        session_id=session.session_id,
                        obs_dir=str(session_dir),
                        hardware=session.hardware,
                        hardware_config=session.hardware_config,
                        session_data=dict(session.session_data),
                        # No stop_check: a stop request lets the current observation
                        # finish every series it has started (see CLAUDE.md, State machine).
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
                    create_observation_file(obs, session_dir)
                    await db_set_yaml_file(obs.id, obs.yaml_file)
                else:
                    await db_stamp_stage(obs.id, stage)

                await _persist_observation(obs)

            except SessionFatalError as e:
                log.error(f"[{stage.value.upper()}] Fatal error, stopping session: {e}")
                if obs:
                    await db_set_stage_status(obs.id, stage, "error")
                    await db_set_error(obs.id, str(e))
                    await _persist_observation(obs)
                state.is_running = False
                obs = None
                break
            except Exception as e:
                log.error(f"[{stage.value.upper()}] Error: {e} — restarting from SCHEDULER")
                if obs:
                    await db_set_stage_status(obs.id, stage, "error")
                    await db_set_error(obs.id, str(e))
                    await _persist_observation(obs)
                obs = None
                break
        else:
            # SEQUENCER succeeded: record observation and launch post-obs in background
            state.completed.append(obs)
            try:
                # model_copy(deep=True, update=...) deep-copies EVERY field first and
                # only applies `update` afterward — passing update={"hardware": {}}
                # does NOT skip deep-copying the live hardware dict first, it still
                # tries to copy.deepcopy() each connected adapter (sockets/locks
                # inside), which raises. Clearing hardware before the copy (and
                # restoring it right after, in case anything below still reads obs)
                # avoids ever handing a live device connection to copy.deepcopy().
                live_hardware = obs.hardware
                obs.hardware = {}
                post_obs_copy = obs.model_copy(deep=True)
                obs.hardware = live_hardware
            except Exception as e:
                # Must not be allowed to propagate uncaught: this block runs outside
                # the per-stage try/except above, so an unhandled exception here would
                # silently kill the whole session_runner() task — state.is_running
                # would never be reset, leaving the GUI showing a session that looks
                # both running and not running at once. The observation itself already
                # succeeded (SEQUENCER done); only its post-obs record-keeping is lost.
                log.error(f"[ORCHESTRATOR] Failed to prepare post-obs copy for {obs.id}: {e}")
                obs = None
                continue
            task = asyncio.create_task(_launch_post_obs(post_obs_copy))
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

def _iso(instant: datetime | None) -> str | None:
    return instant.isoformat() if instant else None


@protected.get("/status")
async def get_status():
    """Returns the current orchestrator state."""
    return {
        "stage": state.stage,
        "is_running": state.is_running,
        "session_active": state.session_active,
        "completed_count": len(state.completed),
        "pending_post_obs_count": state.pending_post_obs,
        "scheduled_start": _iso(state.scheduled_start),
        "scheduled_stop": _iso(state.scheduled_stop),
        "site_longitude_deg": SITE_LONGITUDE_DEG,   # for the GUI's sidereal clock
        "hardware_test_running": state.hardware_test_running,
        "hardware_test_available": SEQUENCER_BACKEND == "indi",
        "demo_mode": IS_DEMO_MODE,   # the GUI says "DEMO mode" in its title
    }

@protected.get("/observations/completed")
async def list_completed():
    """Returns the last 20 completed observations (in-memory cache)."""
    return {"completed": state.completed[-20:]}


@protected.get("/observations/recent")
async def list_recent_observations(limit: int | None = None):
    """Returns observations from the current astronomical day, with per-stage statuses and
    timestamps from the database. No limit by default — see db_get_recent_observations()."""
    return await db_get_recent_observations(limit)

@protected.get("/observations/steps")
async def get_observation_steps():
    """Live, in-memory sub-plugin progress, per observation and per stage
    ({obs_id: {"sequencer": {...}, "reduction": {...}}}) -- see step_progress.py.
    Ephemeral, lost on restart."""
    return step_progress.get_all()

@protected.get("/subplugins/display-names")
async def get_subplugin_display_names():
    """Static, server-lifetime name -> short label mapping for SEQUENCER/REDUCTION
    sub-plugins, used to label step-progress chips in the GUI (see /observations/steps)."""
    return get_display_names(["sequencer", "reduction"])

@protected.get("/observations/{obs_id}")
async def get_observation(obs_id: str):
    """Retrieves a completed observation by its id."""
    for obs in state.completed:
        if obs.id == obs_id:
            return obs
    raise HTTPException(status_code=404, detail="Observation non trouvée")

# Keeps a reference to each running session task, so it is never garbage-collected
# while it runs (asyncio only keeps weak references to tasks).
_session_tasks: set[asyncio.Task] = set()


def _request_start() -> bool:
    """Starts a new observation session, unless one is still active. Refuses as long
    as a previous session hasn't fully finished stopping (session_active), not just
    while a stop has been requested (is_running) — otherwise a second session could
    start while the first one is still finishing its cleanup. Also refused while
    the hardware test runs."""
    if state.session_active or state.hardware_test_running:
        return False
    state.is_running = True
    task = asyncio.create_task(session_runner())
    _session_tasks.add(task)
    task.add_done_callback(_session_tasks.discard)
    return True


def _request_stop() -> None:
    """Requests a clean session stop (after the current observation)."""
    state.is_running = False
    log.info("[ORCHESTRATOR] Stop requested — CLOSE_SESSION will run at the end of the cycle")


def _describe(instant: datetime) -> str:
    """'21:20 local (19:20 UTC)' — for the log lines of the scheduled start/stop."""
    return f"{instant.astimezone():%Y-%m-%d %H:%M} local ({instant.astimezone(timezone.utc):%H:%M} UTC)"


def _check_control_schedule(now: datetime) -> None:
    """One tick of the scheduled start/stop: acts on whatever is due at `now`. A
    start due while the hardware test runs waits for its end (next ticks)."""
    if state.scheduled_start and state.scheduled_start <= now and not state.hardware_test_running:
        state.scheduled_start = None
        if _request_start():
            log.info("[ORCHESTRATOR] Scheduled start reached — session started")
        else:
            log.warning("[ORCHESTRATOR] Scheduled start reached, but a session is already active — ignored")
    if state.scheduled_stop and state.scheduled_stop <= now:
        state.scheduled_stop = None
        if state.is_running:
            log.info("[ORCHESTRATOR] Scheduled stop reached")
            _request_stop()
        else:
            log.info("[ORCHESTRATOR] Scheduled stop reached, no session running — nothing to do")


CONTROL_SCHEDULE_TICK_SECONDS = 1.0

async def _control_schedule_loop() -> None:
    """Runs for the server's whole lifetime (started in lifespan())."""
    while True:
        try:
            _check_control_schedule(datetime.now(timezone.utc))
        except Exception as e:
            log.error(f"[ORCHESTRATOR] Scheduled start/stop check failed: {e}")
        await asyncio.sleep(CONTROL_SCHEDULE_TICK_SECONDS)


@protected.post("/control/start")
async def start_session():
    """Starts a new observation session now; cancels a scheduled start."""
    if state.scheduled_start:
        state.scheduled_start = None
        log.info("[ORCHESTRATOR] Scheduled start cancelled by a manual start")
    if state.hardware_test_running and not state.session_active:
        return {"message": "Test matériel en cours — démarrage refusé"}
    if not _request_start():
        return {"message": "Session déjà en cours"}
    return {"message": "Session démarrée"}

@protected.post("/control/stop")
async def stop_session():
    """Requests a clean session stop (after the current observation); cancels a scheduled stop."""
    if state.scheduled_stop:
        state.scheduled_stop = None
        log.info("[ORCHESTRATOR] Scheduled stop cancelled by a manual stop")
    _request_stop()
    return {"message": "Arrêt demandé — CLOSE_SESSION sera exécuté en fin de cycle"}


@protected.post("/control/hardware-test")
async def hardware_test():
    """Connects every device of the active hardware profile and PHD2, stays
    connected a few seconds, then disconnects what the test connected — no
    session, no image (plugins/hardware_connectivity.py::test_hardware()).
    Returns {"ok", "connected", "errors"}; 409 in simulation, during a session
    or while another test runs."""
    if SEQUENCER_BACKEND != "indi":
        raise HTTPException(status_code=409, detail="Pas de matériel à tester (simulation)")
    if state.session_active:
        raise HTTPException(status_code=409, detail="Session en cours")
    if state.hardware_test_running:
        raise HTTPException(status_code=409, detail="Test déjà en cours")
    state.hardware_test_running = True
    try:
        log.info("[HW_TEST] Hardware test started")
        return await test_hardware()
    finally:
        state.hardware_test_running = False


class ScheduleRequest(BaseModel):
    time: str                          # "HH:MM", 24-hour, local time


def _resolve(request: ScheduleRequest, after: datetime) -> datetime:
    try:
        return next_occurrence(request.time, after)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@protected.put("/control/schedule/start")
async def schedule_start(request: ScheduleRequest):
    """Schedules (or reschedules) the session start at the next occurrence of a time
    of day, within 24 hours."""
    state.scheduled_start = _resolve(request, datetime.now(timezone.utc))
    log.info(f"[ORCHESTRATOR] Start scheduled at {_describe(state.scheduled_start)}")
    if state.scheduled_stop and state.scheduled_stop < state.scheduled_start:
        log.warning("[ORCHESTRATOR] The scheduled stop comes before the scheduled start")
    return {"scheduled_start": _iso(state.scheduled_start)}


@protected.put("/control/schedule/stop")
async def schedule_stop(request: ScheduleRequest):
    """Schedules (or reschedules) the session stop at the next occurrence of a time
    of day — after the scheduled start, if there is one."""
    now = datetime.now(timezone.utc)
    state.scheduled_stop = _resolve(request, max(now, state.scheduled_start or now))
    log.info(f"[ORCHESTRATOR] Stop scheduled at {_describe(state.scheduled_stop)}")
    return {"scheduled_stop": _iso(state.scheduled_stop)}


@protected.delete("/control/schedule/start")
async def cancel_scheduled_start():
    if state.scheduled_start:
        state.scheduled_start = None
        log.info("[ORCHESTRATOR] Scheduled start cancelled")
    return {"scheduled_start": None}


@protected.delete("/control/schedule/stop")
async def cancel_scheduled_stop():
    if state.scheduled_stop:
        state.scheduled_stop = None
        log.info("[ORCHESTRATOR] Scheduled stop cancelled")
    return {"scheduled_stop": None}


WS_PING_INTERVAL = 30  # seconds — matches the 'ping' heartbeat convention of x4js's setupSocketMessaging()

@app.websocket("/ws")
async def ws_logs(websocket: WebSocket):
    """Streams spectro logger records (INFO+) to connected GUI clients, X4 'messaging' protocol.
    Not on the "protected" router (a Depends()-raised HTTPException doesn't close a WebSocket
    handshake cleanly) — checked here instead, before accept(), same cookie as the REST routes."""
    token = websocket.cookies.get(SESSION_COOKIE_NAME)
    user = await session_get_user(token) if token else None
    if user is None:
        await websocket.close(code=1008)
        return
    await websocket.accept(subprotocol="messaging")
    ws_log_handler.add_client(websocket)
    ws_image_broadcaster.add_client(websocket)
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
        ws_image_broadcaster.remove_client(websocket)


_SAFE_PANEL_NAME = re.compile(r"^[A-Za-z0-9_]+$")


@protected.get("/images/{panel}")
async def get_gui_image(panel: str):
    """Serves a GUI panel's current preview image (<panel>.fits in GUI_PREVIEW_DIR)
    -- a fixed filename per panel, overwritten by each new capture and announced
    over the WebSocket (see image_broadcast.py). panel is validated the same way ProgramCreate.name/pipeline_loader.py's _validate_name() are,
    since it's interpolated straight into a filesystem path."""
    if not _SAFE_PANEL_NAME.match(panel):
        raise HTTPException(status_code=404, detail="Image introuvable")
    path = GUI_PREVIEW_DIR / f"{panel}.fits"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image introuvable")
    return FileResponse(path, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})


@protected.get("/images/{panel}/overlay")
async def get_gui_image_overlay(panel: str):
    """The overlay kept next to a panel's preview image (<panel>.json in
    GUI_PREVIEW_DIR, written by the sub-plugin that published it), so a reloaded
    page finds it again -- null when the panel has none."""
    if not _SAFE_PANEL_NAME.match(panel):
        raise HTTPException(status_code=404, detail="Image introuvable")
    path = GUI_PREVIEW_DIR / f"{panel}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


# ─────────────────────────────────────────────────────────────
# Endpoints — Simbad search
# ─────────────────────────────────────────────────────────────

@protected.get("/simbad/search", response_model=StarCreate)
async def simbad_search_endpoint(name: str):
    """
    Queries Simbad and returns the star's information, without inserting it
    into the database. Useful for previewing before insertion.
    """
    try:
        return await simbad_search(name)
    except SimbadNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SimbadQueryError as e:
        raise HTTPException(status_code=502, detail=str(e))


async def _ensure_alias(star: Star, name: str) -> bool:
    """Registers `name` as an alias of `star` unless it's already its canonical
    name or a known alias. Returns True if a new alias was added."""
    known_names = {star.name.lower()} | {a.lower() for a in await star_list_aliases(star.id)}
    if name.lower() not in known_names:
        await star_add_alias(star.id, name)
        return True
    return False


async def _import_star_from_simbad(name: str) -> tuple[Star, bool, bool]:
    """
    Adds a star to the database, identified by any name — no need to know its
    canonical Simbad name. First looks up whether a star matching `name`
    (canonical name or alias) already exists:
    - if so, registers `name` as an alias if it's new, returns the existing
      star (created=False) rather than creating a duplicate;
    - otherwise, queries Simbad for the astrometric data. Simbad can resolve
      `name` to a canonical identity already present in the database under a
      completely different search term (e.g. "Sheliak" and "bet Lyr" are the
      same star) — so we re-check by that canonical name before inserting:
      if a star already matches, same handling as the "existing" case above;
      only otherwise does it insert the new star (canonical name = the one
      returned by Simbad), registering `name` as an alias if different from
      the canonical name, and returns the new star (created=True).
    Returns (star, created, alias_added) — alias_added indicates whether THIS
    call registered a new alias (useful so the caller knows, without having
    to go check the database, whether `name` was indeed kept as an alias).
    Raises SimbadNotFoundError / SimbadQueryError if the star is genuinely
    new and Simbad doesn't recognize it / doesn't respond.
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
    alias_added: bool  # True if `name` (the searched term) was registered as an alias


@protected.post("/simbad/import", response_model=StarImportResponse)
async def simbad_import_endpoint(name: str, response: Response):
    """Adds a star to the database via Simbad — see _import_star_from_simbad()."""
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


@protected.post("/stars/import-list", response_model=list[StarImportResult])
async def import_stars_from_file(file: UploadFile):
    """
    Imports a list of stars (one name per line, uploaded text file) — each
    line follows exactly the same path as /simbad/import. Per-line report: an
    error on one line doesn't interrupt the following ones.
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


@protected.post("/programs/import-list", response_model=list[ProgramStarImportResult])
async def import_program_stars_from_file(file: UploadFile):
    """
    Imports program <-> star links from an uploaded YAML file (format:
    {program_name: [star_name, ...], ...}). Stars must already exist in the
    database (canonical name or alias) — no Simbad call here, unlike
    /stars/import-list. Per-line report.
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
# Entry point
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rosace.main:app", host="0.0.0.0", port=8000)

# ─────────────────────────────────────────────────────────────
# Endpoints — Stars
# ─────────────────────────────────────────────────────────────
# Addressed by name, never by id: the SQLite id is an implementation detail
# that doesn't concern the user (see star_get_by_name: canonical name, then
# falling back to the alias table).

@protected.get("/stars", response_model=list[Star])
async def list_stars():
    """Lists all stars."""
    return await star_list()

@protected.get("/stars/{name}", response_model=StarLookup)
async def get_star(name: str):
    """Looks up a star by its name (canonical or alias)."""
    star = await star_get_by_name(name)
    if not star:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    return StarLookup(**star.model_dump(), queried_name=name)

@protected.put("/stars/{name}", response_model=Star)
async def update_star(name: str, star: StarCreate):
    """Updates a star, looked up by its name (canonical or alias)."""
    existing = await star_get_by_name(name)
    if not existing:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    await star_update(existing.id, star)
    return await star_get(existing.id)

@protected.delete("/stars/{name}", status_code=204)
async def delete_star(name: str):
    """Deletes a star, looked up by its name (canonical or alias)."""
    existing = await star_get_by_name(name)
    if not existing:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    await star_delete(existing.id)

@protected.get("/stars/{name}/programs", response_model=list[Program])
async def get_star_programs(name: str):
    """Returns the programs a star belongs to."""
    existing = await star_get_by_name(name)
    if not existing:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    return await star_get_programs(existing.id)

# ─────────────────────────────────────────────────────────────
# Endpoints — Scientific programs
# ─────────────────────────────────────────────────────────────
# Addressed by name, never by id — same principle as for stars.

@protected.post("/programs", response_model=Program, status_code=201)
async def create_program(program: ProgramCreate):
    """Creates a scientific program."""
    prog_id = await program_insert(program)
    return await program_get(prog_id)

@protected.get("/programs", response_model=list[Program])
async def list_programs():
    """Lists all programs."""
    return await program_list()

@protected.get("/programs/{name}", response_model=Program)
async def get_program(name: str):
    """Retrieves a program by its name."""
    program = await program_get_by_name(name)
    if not program:
        raise HTTPException(status_code=404, detail="Programme non trouvé")
    return program

@protected.delete("/programs/{name}", status_code=204)
async def delete_program(name: str):
    """Deletes a program, looked up by its name."""
    program = await program_get_by_name(name)
    if not program:
        raise HTTPException(status_code=404, detail="Programme non trouvé")
    await program_delete(program.id)

@protected.get("/programs/{name}/stars", response_model=list[StarInProgram])
async def get_program_stars(name: str):
    """Returns a program's stars, with their optional exposure time."""
    program = await program_get_by_name(name)
    if not program:
        raise HTTPException(status_code=404, detail="Programme non trouvé")
    return await program_get_stars(program.id)

@protected.post("/programs/{name}/stars/{star_name}", status_code=204)
async def add_star_to_program(name: str, star_name: str, exposure_time: Optional[float] = None):
    """Links a star to a program, with an optional exposure time specific to
    this program/star pair."""
    program = await program_get_by_name(name)
    if not program:
        raise HTTPException(status_code=404, detail="Programme non trouvé")
    star = await star_get_by_name(star_name)
    if not star:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    await program_add_star(program.id, star.id, exposure_time)

@protected.delete("/programs/{name}/stars/{star_name}", status_code=204)
async def remove_star_from_program(name: str, star_name: str):
    """Removes a star from a program."""
    program = await program_get_by_name(name)
    if not program:
        raise HTTPException(status_code=404, detail="Programme non trouvé")
    star = await star_get_by_name(star_name)
    if not star:
        raise HTTPException(status_code=404, detail="Étoile non trouvée")
    await program_remove_star(program.id, star.id)

# ─────────────────────────────────────────────────────────────
# Endpoints — Authentication
# ─────────────────────────────────────────────────────────────
# Deliberately on "app" directly, not "protected": you need to be able to log
# in before having a session. This is the only public API route.

@app.post("/auth/login", response_model=UserOut)
async def login(credentials: LoginRequest, response: Response):
    """Verifies the credentials and sets the session cookie."""
    row = await _user_row_by_username(credentials.username)
    if row is None or not _verify_password(credentials.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    token = await session_create(row["id"])
    response.set_cookie(
        SESSION_COOKIE_NAME, token,
        httponly=True, samesite="lax", secure=SESSION_COOKIE_SECURE,
        max_age=SESSION_TTL_HOURS * 3600,
    )
    return UserOut(username=row["username"], is_admin=bool(row["is_admin"]), created_at=row["created_at"])

@app.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response):
    """Invalidates the session server-side and clears the cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        await session_delete(token)
    response.delete_cookie(SESSION_COOKIE_NAME)

@protected.get("/auth/me", response_model=UserOut)
async def get_me(user: UserOut = Depends(get_current_user)):
    """Currently logged-in user — used by the GUI at startup to know whether
    to show the login screen or the application."""
    return user

# ─────────────────────────────────────────────────────────────
# Endpoints — Account management (admin only)
# ─────────────────────────────────────────────────────────────

@admin_router.post("", response_model=UserOut, status_code=201)
async def create_user(user: UserCreate):
    """Creates an account. Admin-only — no self-registration."""
    try:
        return await user_create(user)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@admin_router.get("", response_model=list[UserOut])
async def list_users():
    return await user_list()

@admin_router.delete("/{username}", status_code=204)
async def delete_user(username: str, current: UserOut = Depends(require_admin)):
    """Deletes an account — refuses to delete yourself or the last remaining
    admin account (that would cut off all admin access)."""
    if username == current.username:
        raise HTTPException(status_code=400, detail="impossible de se supprimer soi-même")
    admins = [u for u in await user_list() if u.is_admin]
    if len(admins) == 1 and admins[0].username == username:
        raise HTTPException(status_code=400, detail="impossible de supprimer le dernier compte admin")
    await user_delete(username)

app.include_router(protected)
app.include_router(admin_router)
app.include_router(bess_validation_router)

# ─────────────────────────────────────────────────────────────
# Web interface (GUI static files)
# ─────────────────────────────────────────────────────────────
# Mounted last: the API routes above stay in priority.

GUI_DIR = Path(__file__).parent / "gui" / "bin"

app.mount("/", StaticFiles(directory=GUI_DIR, html=True), name="gui")
