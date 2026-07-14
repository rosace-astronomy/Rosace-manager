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

Tests patch `BASE_DATA_DIR`, `DB_PATH`, and `TARGETS_DB_PATH` via a session-scoped fixture in `test_rosace_core.py` — always apply this pattern in new test files to avoid writing to `/tmp/rosace` or production databases.

## Architecture

### State machine (`main.py`)

The global singleton `state` (a `SystemState` instance) drives the orchestration loop:

```
IDLE → INIT_SESSION → [SCHEDULER → SEQUENCER → REDUCTION → SHARING → ARCHIVE] × N → CLOSE_SESSION → IDLE
```

The session runs as a FastAPI background task. `POST /control/start` starts it; `POST /control/stop` sets `state.is_running = False`, which causes the scheduler to raise `SchedulerStopRequested` and triggers a clean `CLOSE_SESSION`.

On any plugin error in the observation loop, the error is recorded in the DB, and the loop restarts from **SCHEDULER** (not from INIT_SESSION).

### Plugin system (two levels)

**Stage plugins** (`plugins/__init__.py` registry) — one per `OrchestratorStage` enum value, each implementing `async def run(self, ctx: SessionContext) -> SessionContext`.

**Sub-plugins** — loaded dynamically by `pipeline_loader.py` at runtime from a YAML programme file. The active programme is set by `ACTIVE_PROGRAMME` in `config.py`.

- Sequencer sub-plugins: `plugins/sequencer_plugins/` (`slew`, `focus`, `expose`)
- Reduction sub-plugins: `plugins/reduction_plugins/` (`bias`, `dark`, `flat`, `extract`)
- Programme definitions: `rosace/programmes/*.yaml` — declare which sub-plugins run in order

Note: the `sequencer-plugins/` and `reduction-plugins/` hyphenated directories contain standalone numbered sub-packages (each with their own test file) and are **not** imported by `pipeline_loader.py`. The underscore variants (`sequencer_plugins/`, `reduction_plugins/`) are the importable packages.

To add a new sub-plugin: create the class in the appropriate `*_plugins/` package, register it in `pipeline_loader.py`'s `_build_registry()`, and add its name to the relevant programme YAML.

### Session context (`SessionContext`)

The `SessionContext` Pydantic object is the shared work memory passed between all stages. After SCHEDULER runs, the orchestrator (`main.py`) creates the `Observation` object and populates `ctx.current_observation`, `ctx.obs_dir`, and `ctx.yaml_file` before the next stage.

### Two SQLite databases

- **`rosace.db`** (`database.py`) — sessions and observations. Functions: `db_init`, `db_insert_session`, `db_close_session`, `db_insert_observation`, `db_stamp_stage`, `db_set_error`, `db_set_yaml_file`.
- **`targets.db`** (`targets.py`) — stars catalogue and scientific programs (N-N via `program_stars`). CRUD functions follow the pattern `star_*` / `program_*`.

Default path is `/tmp/rosace/` (`config.py`). Both databases are created automatically on startup.

### Simbad client (`simbad.py`)

Queries CDS TAP via ADQL over HTTPS. Returns `StarCreate`. Raises `SimbadNotFoundError` (404) or `SimbadQueryError` (network/HTTP errors). In tests, mock `rosace.simbad.httpx.AsyncClient` as an async context manager.

### Key configuration (`config.py`)

| Constant | Default | Purpose |
|---|---|---|
| `BASE_DATA_DIR` | `/tmp/rosace` | Root for all session data |
| `DB_PATH` | `/tmp/rosace/rosace.db` | Sessions + observations DB |
| `ACTIVE_PROGRAMME` | `"etoiles_be"` | YAML programme to load at session start |

`TARGETS_DB_PATH` is defined in `targets.py` as `/tmp/rosace/targets.db`.

### GUI (`rosace/rosace/gui/`)

The control interface is a single-page application built **exclusively with [X4 (x4js)](https://github.com/rlibre/x4)**. No other UI framework or DOM manipulation library is permitted.

- Entry point: `src/main.ts` — one `RosaceApp` class, all layout via X4 components
- All containers must be `VBox` / `HBox`; all text via `Label`; all interactive elements via `Button` or `Component`
- Build: `npm run build` inside `gui/` — pure esbuild CLI, no extra tooling, outputs to `gui/bin/`
- Serve: FastAPI mounts `gui/bin/` as static files directly (`main.py`, end of file) — same process, same port as the API, no separate GUI server
- x4js source lives at `gui/src/x4js/` (copied from the upstream repo, **not** an npm dependency). esbuild compiles it as project code, so TypeScript decorators work natively with `experimentalDecorators: true` in `tsconfig.json`.

### On-disk layout

```
/tmp/rosace/
├── rosace.db
├── targets.db
└── 2026-07-05_St-Pancrasse_<session_id>/
    └── <datetime>_<target>/
        ├── <datetime>_<target>.yaml
        └── spectrum_0001.fits
```
