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
pytest rosace/test_rosace_core.py -v
pytest rosace/test_rosace_core.py -v --tb=short
pytest rosace/test_rosace_core.py -v -k "models"       # run one group
pytest rosace/test_rosace_core.py -v -k "TestSimbad"   # run one class
pytest rosace/rosace/plugins/test_pipeline_loader.py -v
```

Tests patch `BASE_DATA_DIR`, `DB_PATH`, and `TARGETS_DB_PATH` via a session-scoped fixture in `test_rosace_core.py` — always apply this pattern in new test files to avoid writing to `~/.rosace` or production databases.

## Architecture

### State machine (`main.py`)

The global singleton `state` (a `SystemState` instance) drives the orchestration loop:

```
IDLE → INIT_SESSION → [SCHEDULER → SEQUENCER → REDUCTION → SHARING → ARCHIVE] × N → CLOSE_SESSION → IDLE
```

The session runs as a FastAPI background task. `POST /control/start` starts it; `POST /control/stop` sets `state.is_running = False`, which causes the scheduler to raise `SchedulerStopRequested` and triggers a clean `CLOSE_SESSION`.

On any plugin error in the observation loop, the error is recorded in the DB, and the loop restarts from **SCHEDULER** (not from INIT_SESSION).

### Plugin system (two levels)

**Stage plugins** (`plugins/__init__.py` registry) — one per `OrchestratorStage` enum value. Two interfaces (`models.py`): `SessionStagePlugin` for `INIT_SESSION`/`CLOSE_SESSION` (`run(self, ctx: SessionContext) -> SessionContext`), and `ObservationStagePlugin` for `SCHEDULER` → `ARCHIVE` (`run(self, ctx: ObservationContext) -> ObservationContext`).

**Sub-plugins** — loaded dynamically by `pipeline_loader.py` at runtime from a YAML programme file. The active programme is set by `ACTIVE_PROGRAMME` in `config.py`.

- Sequencer sub-plugins: `plugins/sequencer_plugins/` (`slew`, `focus`, `expose`)
- Reduction sub-plugins: `plugins/reduction_plugins/` (`bias`, `dark`, `flat`, `extract`)
- Programme definitions: `rosace/programmes/*.yaml` — declare which sub-plugins run in order

Note: the `sequencer-plugins/` and `reduction-plugins/` hyphenated directories contain standalone numbered sub-packages (each with their own test file) and are **not** imported by `pipeline_loader.py`. The underscore variants (`sequencer_plugins/`, `reduction_plugins/`) are the importable packages.

To add a new sub-plugin: create the class in the appropriate `*_plugins/` package, register it in `pipeline_loader.py`'s `_build_registry()`, and add its name to the relevant programme YAML.

### Session & observation context (`SessionContext`, `ObservationContext`)

Two separate Pydantic objects (`models.py`), deliberately not merged:

- **`SessionContext`** — passed only to `INIT_SESSION`/`CLOSE_SESSION`. Minimal and stable for the whole night: `session_id`, `started_at`, `site_name`, `session_dir`. Recorded once in the `sessions` table.
- **`ObservationContext`** — passed from `SCHEDULER` through `ARCHIVE`. `main.py`'s `session_runner()` creates a fresh instance for each observation, carrying forward only `session_id` (for traceability — every observation row references its session, never the reverse). Holds `current_observation`, `obs_dir`, `yaml_file`, `next_target_name`, `raw_files`, `reduced_files`. After SCHEDULER runs, the orchestrator creates the `Observation` object and populates these fields before the next stage.

Naming convention: the local variable holding an `ObservationContext` is `obs_ctx`, a `SessionContext` is `session`. Plain `ctx` is reserved for the few genuinely generic spots (e.g. `run_stage()`) that accept either type.

### Two SQLite databases

- **`rosace.db`** (`database.py`) — sessions and observations. Functions: `db_init`, `db_insert_session`, `db_close_session`, `db_insert_observation`, `db_stamp_stage`, `db_set_error`, `db_set_yaml_file`.
- **`targets.db`** (`targets.py`) — stars catalogue and scientific programs (N-N via `program_stars`). CRUD functions follow the pattern `star_*` / `program_*`.

Default path is `~/.rosace/databases/` (`config.py`). Both databases are created automatically on startup.

### Simbad client (`simbad.py`)

Queries CDS TAP via ADQL over HTTPS. Returns `StarCreate`. Raises `SimbadNotFoundError` (404) or `SimbadQueryError` (network/HTTP errors). In tests, mock `rosace.simbad.httpx.AsyncClient` as an async context manager.

### Configuration (`rosace/rosace/conf/`)

All application configuration lives in `conf/` — one predictable place to look, split into separate files by concern rather than crammed into a single monolith:

| File | Purpose |
|---|---|
| `config.yaml` | Core application settings (data paths, site name, active programme) |
| `logging.cfg` | Logging setup (`fileConfig` ini format — see below) |
| `hardware_<variant>.yaml` | Hardware/instrument definitions, one file per variant (e.g. `hardware_simu.yaml` for the INDI simulators, a future `hardware_obs.yaml` for the real observatory). Which variant is active is selected via `active_hardware` in `config.yaml` (resolved to `HARDWARE_CONFIG_PATH` in `config.py`); the file itself is not yet parsed/consumed by any Python code — real hardware integration is still a TODO, this is only a template so far. |

Note: `programmes/*.yaml` (scientific programme definitions) intentionally live outside `conf/` — they're a catalogue of interchangeable observation plans, not app configuration.

### Key configuration (`config.py` loads `conf/config.yaml`)

`config.py` is a thin loader: it reads `conf/config.yaml` at import time and exposes the same constants the rest of the codebase already imports (`from .config import BASE_DATA_DIR, ...`).

| Constant | Source key in `config.yaml` | Default | Purpose |
|---|---|---|---|
| `BASE_DATA_DIR` | `base_data_dir` | `~/.rosace` | Root for all persistent data (databases, log, session directories) |
| `DATABASES_DIR` | `databases_dir` | `~/.rosace/databases` | Subdirectory holding both SQLite databases |
| `DB_PATH` | *(derived)* | `~/.rosace/databases/rosace.db` | Sessions + observations DB |
| `SITE_NAME` | `site_name` | `"St-Pancrasse"` | Used to name session directories |
| `ACTIVE_PROGRAMME` | `active_programme` | `"etoiles_be"` | YAML programme to load at session start (⚠️ misplaced here — this is per-observation, not global deployment config; slated to move out of `config.yaml`, see project memory) |
| `ACTIVE_HARDWARE` | `active_hardware` | `"hardware_simu"` | Name (no extension) of the hardware YAML file to use |
| `HARDWARE_CONFIG_PATH` | *(derived)* | `conf/hardware_simu.yaml` | Full path to the active hardware YAML — resolved but not yet loaded/parsed anywhere |

`TARGETS_DB_PATH` is defined in `targets.py` as `DATABASES_DIR / "targets.db"`.

### Logging (`log_config.py`, `conf/logging.cfg`)

Centralized logging, configured once via `configure_logging()`, called at the very top of `main.py` (right after importing `.config`, before any other internal import) — so that log messages emitted at plugin-instantiation time (which happens at import time) are also captured.

- Config format: classic `fileConfig` ini file (`conf/logging.cfg`), not `dictConfig`.
- Logger hierarchy: everything under the `spectro` logger (`spectro.main`, `spectro.plugin.*`, `spectro.subplugin.*`) propagates to a single `spectro` handler set — no per-module setup needed.
- Two handlers: console (`StreamHandler`, INFO+) and rotating file (`RotatingFileHandler`, DEBUG+, 1 MB × 9 backups) at `~/.rosace/log/rosace.log`.
- `root` logger stays at WARNING to silence FastAPI/uvicorn/httpx noise.

### GUI (`rosace/rosace/gui/`)

The control interface is a single-page application built **exclusively with [X4 (x4js)](https://github.com/rlibre/x4)**. No other UI framework or DOM manipulation library is permitted.

- Entry point: `src/main.ts` — one `RosaceApp` class, all layout via X4 components
- All containers must be `VBox` / `HBox`; all text via `Label`; all interactive elements via `Button` or `Component`
- Build: `npm run build` inside `gui/`, via `x4build` (esbuild-based CLI dedicated to x4js), outputs to `gui/bin/`
- Serve: FastAPI mounts `gui/bin/` as static files directly (`main.py`, end of file) — same process, same port as the API, no separate GUI server
- x4js is a regular npm dependency (`package.json`, `devDependencies.x4js`), installed under `node_modules/x4js`. TypeScript decorators are compiled correctly by `x4build` even from `node_modules`, so no local vendoring is needed.
- After editing `package.json` (e.g. bumping x4js), always run a full `npm install` and confirm `node_modules/` matches `package-lock.json` before rebuilding — a partial/interrupted install here has caused the built app to silently fail to render (blank page, no build error).

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
        ├── <datetime>_<target>.yaml
        └── spectrum_0001.fits
```
