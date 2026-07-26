# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language policy

All code artifacts must be in English: variable names, function names, comments, docstrings, log messages, and any text written to files. This applies even though the user communicates with Claude in French. Conversations happen in French; code is written in English.

## Project overview

**Rosace** is an async REST API (FastAPI + Python 3.12) that orchestrates spectroscopic observation sessions end-to-end. It is designed to run autonomously at night, cycling through a state machine from target selection to archiving.

- Python package: `rosace/rosace/`
- Build config: `rosace/pyproject.toml`
- Virtual environment: `rosace-manager/`

## Setup and running

```bash
# Activate the virtual environment
source rosace-manager/bin/activate

# Install in editable mode with dev dependencies (run from repo root)
pip install -e "rosace/[dev]"

# Start the server
cd rosace
uvicorn rosace.main:app --host 0.0.0.0 --port 8000
# or: python -m rosace.main

# Interactive API docs
# http://localhost:8000/docs

# The web GUI is served by the same process, at the same port
# http://localhost:8000/
```

## Running tests

```bash
cd rosace
pytest rosace -v                                       # everything, recursively
pytest rosace/test_rosace_core.py -v
pytest rosace/test_rosace_core.py -v --tb=short
pytest rosace/test_rosace_core.py -v -k "models"       # run one group
pytest rosace/test_rosace_core.py -v -k "TestSimbad"   # run one class
pytest rosace/plugins/test_pipeline_loader.py -v
pytest rosace/plugins -v                                # every stage plugin's tests
pytest rosace/plugins/sequencer_plugins rosace/plugins/reduction_plugins -v  # every sub-plugin's tests
```

Tests patch `BASE_DATA_DIR`, `DB_PATH`, and `TARGETS_DB_PATH` via a session-scoped `tmp_data_dir` fixture in `conftest.py` (`rosace/rosace/conftest.py`, shared by every test file in the tree — always apply this pattern in new test files, no need to redefine it) — to avoid writing to `~/.rosace` or production databases.

### Test layout

`test_rosace_core.py` covers models, config, database, targets, and Simbad. `test_observation_file.py` covers `observation_file.py` (YAML persistence) on its own, next to it. Each plugin and sub-plugin has its own dedicated test file, next to the source it tests (mirroring the pre-existing `plugins/test_pipeline_loader.py` convention):

- Stage plugins: `plugins/test_<stage>.py` (e.g. `plugins/test_scheduler.py`), one per `OrchestratorStage`.
- Sequencer sub-plugins: `plugins/sequencer_plugins/<backend>/test_<name>.py` — one copy per backend (`internal/`, `indi/`), see below.
- Reduction sub-plugins: `plugins/reduction_plugins/test_<name>.py`.

`SequencerPlugin`/`ReductionPlugin` tests mock `pipeline_loader.load_pipelines` — they cover orchestration (order, error propagation, which programme gets loaded), not real sub-plugin behavior, which is covered by the sub-plugin test files themselves.

`test_log_broadcast.py` covers `log_broadcast.py` (WebSocket log streaming) on its own, next to it — same convention as `test_observation_file.py`. The `/ws` route itself is exercised end-to-end in `test_rosace_core.py`'s `TestApiEndpoints` group, via `TestClient.websocket_connect()`.

`plugins/test_hardware_connectivity.py` covers `plugins/hardware_connectivity.py` the same way, next to it.

## Architecture

### State machine (`main.py`)

The global singleton `state` (a `SystemState` instance) drives the orchestration loop:

```
IDLE → INIT_SESSION → [SCHEDULER → SEQUENCER → REDUCTION → SHARING → ARCHIVE] × N → CLOSE_SESSION → IDLE
```

The session runs as a FastAPI background task. `POST /control/start` starts it; `POST /control/stop` sets `state.is_running = False`. No plugin needs to know about this flag: `session_runner()`'s own loop checks `state.is_running` before every stage call, so a stop request is picked up between stages and reaches `CLOSE_SESSION` cleanly — no exception involved.

On any plugin error in the observation loop, the error is recorded in the DB, and the loop restarts from **SCHEDULER** (not from INIT_SESSION).

**Plugins never block waiting on external conditions.** `SchedulerPlugin` is the one plugin that could, in principle, need to wait (for a target to become observable) — but it must still answer within a few seconds: if nothing is observable right now, it sets `obs.scheduler_data["target"] = None` and returns immediately, rather than sleeping internally. `session_runner()` treats `target is None` as "nothing to do yet" (not an error — no `db_set_error`, no restart-from-SCHEDULER log): it waits `SCHEDULER_RETRY_SECONDS` (`config.py`, in small interruptible steps via `_interruptible_sleep()` so a stop request during the wait still takes effect promptly) and asks SCHEDULER again. This is why no plugin — including `SchedulerPlugin` — imports `main.py` or touches `state`: earlier, `SchedulerPlugin` used to poll `state.is_running` in its own internal wait loop, which required a late/deferred `from .. import main` (a top-level `from ..main import state` fails at import time with a circular-import error, since `main.py` imports the plugin registry before defining `state`). Removing the internal wait removed the need for that coupling entirely.

### Plugin system (two levels)

**Stage plugins** (`plugins/__init__.py` registry) — one per `OrchestratorStage` enum value. Two interfaces (`models.py`): `SessionStagePlugin` for `INIT_SESSION`/`CLOSE_SESSION` (`run(self, ctx: SessionContext) -> SessionContext`), and `ObservationStagePlugin` for `SCHEDULER` → `ARCHIVE` (`run(self, obs: Observation) -> Observation`).

**Sub-plugins** — loaded dynamically by `pipeline_loader.py` at runtime from a YAML programme file, one reload per observation (`obs.scheduler_data["programme"]`, set by `SchedulerPlugin` — there is no global "active programme" setting). Sub-plugins share the same interface as stage plugins: `execute(self, obs: Observation) -> Observation` (`sub_plugin_base.py`). Each sub-plugin reads/writes its parent stage's casier on `obs` (see below), and may read a previous stage's casier as input (e.g. `extract` reads `obs.sequencer_data["raw_files"]`). Sub-plugins have no config of their own — all configuration lives in `conf/` (see below), consistent with the rest of the app.

- Sequencer sub-plugins: `plugins/sequencer_plugins/` (`slew`, `focus`, `expose`) — read/write `obs.sequencer_data`, read `obs.scheduler_data` for target/exposure. **Two parallel implementations**, chosen by `SEQUENCER_BACKEND` (`config.py`):
  - `sequencer_plugins/internal/` — simulation interne, aucune dépendance externe (démonstrateur niveau 1, "internal" backend). Fonctionnellement stable — c'est celle qui tourne aujourd'hui par défaut.
  - `sequencer_plugins/indi/` — protocole INDI/PHD2 réel ("indi" backend, démonstrateurs niveaux 2/3). **Placeholder pour l'instant** : même contrat exact (mêmes clés posées dans `sequencer_data`) que `internal/`, mais action minimale (`sleep(1)`) — à implémenter lors du prochain chantier. Les vraies classes de pilotage device (voir `indi_hardware/` ci-dessous) existent déjà et sont utilisées par le check de connectivité d'`INIT_SESSION`, mais ne sont pas encore appelées depuis ces sous-plugins SEQUENCER.
  - `pipeline_loader.py::_build_registry()` fait le dispatch selon `SEQUENCER_BACKEND` ; `SequencerPlugin.run()` lui-même reste agnostique du backend.
- Reduction sub-plugins: `plugins/reduction_plugins/` (`bias`, `dark`, `flat`, `extract`) — read/write `obs.reduction_data`, read `obs.sequencer_data["raw_files"]` as their initial input. Pas de split par backend : ces étapes traitent des fichiers déjà acquis, sans dépendance matérielle — l'axe INDI/ASCOM ne les concerne pas.
- Programme definitions: `rosace/programmes/*.yaml` — declare which sub-plugins run in order. `demo.yaml` is a minimal, backend-agnostic programme (same step names as any other) used as the default for a fresh install and for quick end-to-end verification; `etoiles_be.yaml` and others remain on disk for later use once real target/programme selection exists.

Note: the `sequencer-plugins/` and `reduction-plugins/` hyphenated directories contain standalone numbered sub-packages (each with their own test file) and are **not** imported by `pipeline_loader.py`. The underscore variants (`sequencer_plugins/`, `reduction_plugins/`) are the importable packages.

To add a new sub-plugin: create the class in the appropriate `*_plugins/` package, register it in `pipeline_loader.py`'s `_build_registry()`, and add its name to the relevant programme YAML.

### Session context and the observation dossier (`SessionContext`, `Observation`)

Two separate Pydantic objects (`models.py`), deliberately not merged:

- **`SessionContext`** — passed only to `INIT_SESSION`/`CLOSE_SESSION`. Minimal and stable for the whole night: `session_id`, `started_at`, `site_name`, `session_dir`. Recorded once in the `sessions` table.
- **`Observation`** — passed from `SCHEDULER` through `ARCHIVE`, unwrapped (no `ObservationContext` wrapper — it was removed; the dossier *is* what flows). `main.py`'s `session_runner()` creates a fresh, empty instance for each observation (`id`, `created_at`, `session_id`, `obs_dir` — the only fields the orchestrator itself knows or sets) right before calling `SchedulerPlugin`, which fills it in.

  The orchestrator never reads or writes business data — each stage plugin (and its sub-plugins) owns one casier, free-form (`dict[str, Any]`), that only it writes to: `scheduler_data`, `sequencer_data`, `reduction_data`, `sharing_data`, `archive_data`. `models.py` only declares that these casiers exist, never what's inside them. The one deliberate exception: `Observation.suggested_filename` (a `@property` on the model itself) reads `self.scheduler_data["target"].name` purely to build a human-readable filename — a conscious, minimal, cosmetic exception, kept on the model (not in `main.py` or a stage plugin) precisely so `main.py` and the plugins never need to know it exists. Computed lazily on every access, not cached — reflects whatever `scheduler_data` currently holds (falls back to `"target"` before `SchedulerPlugin` has run).

  `Observation` is also serialized as-is to the observation's YAML file (`obs.yaml_file`) after **every** stage (`observation_file.py::write_observation_file()`, called from both the foreground loop and `run_post_obs()`) — the file only grows, becoming the complete, self-contained record of the observation by the time ARCHIVE finishes. Useful for crash diagnosis: whatever was last written on disk shows exactly how far the observation got.

Naming convention: the local variable holding an `Observation` is `obs`, a `SessionContext` is `session`. Plain `ctx` is reserved for the few genuinely generic spots (e.g. `run_stage()`) that accept either type.

### Two SQLite databases

- **`rosace.db`** (`database.py`) — sessions and observations. Functions: `db_init`, `db_insert_session`, `db_close_session`, `db_insert_observation`, `db_stamp_stage`, `db_set_error`, `db_set_yaml_file`, `db_get_recent_observations`. The latter only returns observations from the current *astronomical day* (local noon to noon, via `_astronomical_day_start_utc()`) — assumes the server runs in the site's own local timezone, since there is no explicit site timezone setting anywhere in `conf/`.
- **`targets.db`** (`targets.py`) — stars catalogue and scientific programs (N-N via `program_stars`). CRUD functions follow the pattern `star_*` / `program_*`.

Default path is `~/.rosace/databases/` (`config.py`). Both databases are created automatically on startup.

### Observation YAML persistence (`observation_file.py`)

Plays the same role for the observation's YAML file that `database.py` plays for SQLite: a dedicated persistence module, kept separate from both the orchestrator (`main.py`) and the pure data model (`models.py`). `create_observation_file(obs, target_dir)` fixes `obs.yaml_file` (using `obs.suggested_filename`) without writing anything; `write_observation_file(obs)` serializes the current full state (`obs.model_dump(mode="json")`) to that path — a no-op until `create_observation_file` has run. `main.py` only ever calls these two functions, the same way it calls `db_insert_session` — it never touches YAML or filesystem details directly.

### INDI hardware drivers (`indi_hardware/`) and connectivity check (`plugins/hardware_connectivity.py`)

`indi_hardware/` (`wrapper/` + `devices/`) holds the real, blocking (non-asyncio) Python classes that talk to an INDI server — one class per device type (`devices/camera.py::IndiCamera`, `devices/mount.py::IndiMount`, `devices/focuser.py::IndiFocuser`, etc.), all built on `wrapper/indi_device.py::IndiDevice` and `wrapper/indi_client.py::IndiClient`. Distant heritage: adapted from the PANOPTES/POCS observatory control software — the POCS-specific config loader and MongoDB-backed database layer that the original `Base` class used to pull in on every instantiation have been stripped out (`wrapper/base.py` is now just a logger), since Rosace has its own config/databases that have nothing to do with POCS's. Devices sharing the same INDI server (`indi_host`/`indi_port`) share a single `IndiClient` instance under the hood (`SingletonIndiClientHolder`), whose setup is not thread-safe.

Each device's class is declared in the active hardware YAML (`HARDWARE_CONFIG_PATH`, e.g. `conf/hardware_indi_simu.yaml`) via `module`/`module_dir`/`class_name` fields and loaded **dynamically** (`importlib`) rather than imported directly — this is what lets the set of devices vary freely per hardware profile (a camera here, an ASI camera there, an extra `pointing_camera` in one profile but not another) without any Python code change.

`plugins/hardware_connectivity.py::check_hardware_connectivity()` is called from `INIT_SESSION` (`init_session.py`), **only when `SEQUENCER_BACKEND == "indi"`**: it reads the hardware YAML into a plain dict, dynamically loads and connects every declared `INDI_drivers` device plus PHD2 (plain TCP check — no PHD2 client library yet, that's a future chantier), each bounded by `HARDWARE_CONNECT_TIMEOUT_SECONDS` (single attempt, no retry). Because of the shared-`IndiClient` thread-safety concern above, devices are checked **one at a time** (not concurrently); PHD2, an independent plain `asyncio` TCP connection, runs concurrently with the device checks. Each blocking device connect runs in a worker thread (`asyncio.to_thread`) so it never blocks the FastAPI event loop. A successful device check disconnects again right away — this only verifies reachability, it doesn't keep connections open for the rest of the session (SEQUENCER's own connection lifecycle, once `sequencer_plugins/indi/` stops being a placeholder, is a separate concern). Any failure (timeout or exception, on any device or PHD2) raises a single aggregated `RuntimeError`, which `INIT_SESSION` lets propagate — `session_runner()` already treats an `INIT_SESSION` exception as a critical, session-cancelling failure, so no extra error handling is needed there.

### Simbad client (`simbad.py`)

Queries CDS TAP via ADQL over HTTPS. Returns `StarCreate`. Raises `SimbadNotFoundError` (404) or `SimbadQueryError` (network/HTTP errors). In tests, mock `rosace.simbad.httpx.AsyncClient` as an async context manager.

### Configuration (`rosace/rosace/conf/`)

All application configuration lives in `conf/` — one predictable place to look, split into separate files by concern rather than crammed into a single monolith:

| File | Purpose |
|---|---|
| `config.yaml` | Core application settings (data paths, site name, active programme) |
| `logging.cfg` | Logging setup (`fileConfig` ini format — see below) |
| `hardware_<variant>.yaml` | Hardware/instrument definitions, one file per variant (e.g. `hardware_indi_simu.yaml` for the INDI simulators, `hardware_indi_72ED.yaml` for a real telescope). Which variant is active is selected via `active_hardware` in `config.yaml` (resolved to `HARDWARE_CONFIG_PATH` in `config.py`). Declares, under `INDI_drivers`, one entry per device (`module`/`module_dir`/`class_name` for dynamic loading, plus device-specific fields incl. `indi_client: {indi_host, indi_port}`), and a `PHD2_config` section (`host`/`port`/...) — parsed by `plugins/hardware_connectivity.py` when `sequencer_backend=indi`. |

Note: `programmes/*.yaml` (scientific programme definitions) intentionally live outside `conf/` — they're a catalogue of interchangeable observation plans, not app configuration.

### Key configuration (`config.py` loads `conf/config.yaml`)

`config.py` is a thin loader: it reads `conf/config.yaml` at import time and exposes the same constants the rest of the codebase already imports (`from .config import BASE_DATA_DIR, ...`).

| Constant | Source key in `config.yaml` | Default | Purpose |
|---|---|---|---|
| `BASE_DATA_DIR` | `base_data_dir` | `~/.rosace` | Root for all persistent data (databases, log, session directories) |
| `DATABASES_DIR` | `databases_dir` | `~/.rosace/databases` | Subdirectory holding both SQLite databases |
| `DB_PATH` | *(derived)* | `~/.rosace/databases/rosace.db` | Sessions + observations DB |
| `LOG_DIR` | `log_dir` | `~/.rosace/log` | Subdirectory holding the rotating log file (`log_config.py`) |
| `OBSERVATIONS_DIR` | `observations_dir` | `~/.rosace/observations` | Subdirectory holding one folder per observation night (`INIT_SESSION`) |
| `SITE_NAME` | `site_name` | `"St-Pancrasse"` | Used to name session directories |
| `ACTIVE_HARDWARE` | `active_hardware` | `"hardware_indi_simu"` | Name (no extension) of the hardware YAML file to use |
| `HARDWARE_CONFIG_PATH` | *(derived)* | `conf/hardware_indi_simu.yaml` | Full path to the active hardware YAML, parsed by `plugins/hardware_connectivity.py` (`sequencer_backend=indi` only) |
| `SCHEDULER_RETRY_SECONDS` | `scheduler_retry_seconds` | `60` | Delay before `main.py` asks SCHEDULER again after it reports nothing to observe (`target=None`) |
| `SEQUENCER_BACKEND` | `sequencer_backend` | `"internal"` | Which SEQUENCER sub-plugin family to load: `"internal"` (simulation, no external dependency) or `"indi"` (real INDI/PHD2 protocol, placeholder for now) |
| `HARDWARE_CONNECT_TIMEOUT_SECONDS` | `hardware_connect_timeout_seconds` | `5` | `INIT_SESSION` (`sequencer_backend=indi` only): max time to wait for each INDI device / PHD2 to answer |

`TARGETS_DB_PATH` is defined in `targets.py` as `DATABASES_DIR / "targets.db"`.

### Logging (`log_config.py`, `conf/logging.cfg`)

Centralized logging, configured once via `configure_logging()`, called at the very top of `main.py` (right after importing `.config`, before any other internal import) — so that log messages emitted at plugin-instantiation time (which happens at import time) are also captured.

- Config format: classic `fileConfig` ini file (`conf/logging.cfg`), not `dictConfig`.
- Logger hierarchy: everything under the `spectro` logger (`spectro.main`, `spectro.plugin.*`, `spectro.subplugin.*`) propagates to a single `spectro` handler set — no per-module setup needed.
- Two handlers configured via `logging.cfg`: console (`StreamHandler`, INFO+) and rotating file (`RotatingFileHandler`, DEBUG+, 1 MB × 9 backups) at `~/.rosace/log/rosace.log`.
- `root` logger stays at WARNING to silence FastAPI/uvicorn/httpx noise.
- A third handler, `WebSocketLogHandler` (`log_broadcast.py`), is attached to the `spectro` logger **programmatically** (not via `logging.cfg` — it needs a live asyncio loop and the set of connected GUI clients), in `main.py`'s `lifespan` startup. It buffers INFO+ records and flushes them in periodic batches (every `FLUSH_INTERVAL`, capped at `MAX_BUFFERED` with a dropped-count warning) over the `/ws` route to the GUI's Journal panel — see GUI section below.

### GUI (`rosace/rosace/gui/`)

The control interface is a single-page application built **exclusively with [X4 (x4js)](https://github.com/rlibre/x4)**. No other UI framework or DOM manipulation library is permitted.

- Entry point: `src/main.ts` — one `RosaceApp` class, all layout via X4 components
- All containers must be `VBox` / `HBox`; all text via `Label`; all interactive elements via `Button` or `Component`
- Build: `npm run build` inside `gui/`, via `x4build` (esbuild-based CLI dedicated to x4js), outputs to `gui/bin/`
- Serve: FastAPI mounts `gui/bin/` as static files directly (`main.py`, end of file) — same process, same port as the API, no separate GUI server
- x4js is a regular npm dependency (`package.json`, `devDependencies.x4js`), installed under `node_modules/x4js`. TypeScript decorators are compiled correctly by `x4build` even from `node_modules`, so no local vendoring is needed.
- After editing `package.json` (e.g. bumping x4js), always run a full `npm install` and confirm `node_modules/` matches `package-lock.json` before rebuilding — a partial/interrupted install here has caused the built app to silently fail to render (blank page, no build error).
- `bin/main.js` has no version hash in its filename — after `npm run build`, a hard browser refresh (cache bypass) may be needed to actually load the new bundle; a stale cached bundle fails silently (no console error, feature just isn't there).
- Live log streaming: `RosaceApp` opens the `/ws` WebSocket via X4's built-in `Application.setupSocketMessaging()` (no custom WebSocket code — see `connectWebSocket()` in `main.ts`), subprotocol `"messaging"`. Backend batches are delivered as global events `{msg: "log.create", params: {lines: [{level, text}, ...]}}`; `MainView` picks them up via `this.onGlobalEvent(...)` and appends them to the existing Journal panel (`logMany()`). `setupSocketMessaging()` has no built-in reconnect, so `main.ts` owns retry-with-visible-`Notification` on disconnect — never silent.

### On-disk layout

```
~/.rosace/
├── databases/
│   ├── rosace.db
│   └── targets.db
├── log/
│   └── rosace.log
└── observations/
    └── <date>_<site_name>/
        ├── <datetime>_<target>.yaml   # full Observation dossier, rewritten after every stage
        └── spectrum_0001.fits
```
