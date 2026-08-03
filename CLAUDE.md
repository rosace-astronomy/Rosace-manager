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

# First run only: bootstrap the first admin account (see Authentication)
python -m rosace.main --create-admin <username>

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
pytest rosace/plugins/sequencer_plugins rosace/plugins/reduction_plugins -v  # every sub-plugin's tests (one directory per sub-plugin)
```

Tests patch `BASE_DATA_DIR`, `DB_PATH`, and `TARGETS_DB_PATH` via a session-scoped `tmp_data_dir` fixture in `conftest.py` (`rosace/rosace/conftest.py`, shared by every test file in the tree — always apply this pattern in new test files, no need to redefine it) — to avoid writing to `~/.rosace` or production databases.

### Test layout

`test_rosace_core.py` covers models, config, database, targets, and Simbad. `test_observation_file.py` covers `observation_file.py` (YAML persistence) on its own, next to it. Each plugin and sub-plugin has its own dedicated test file, next to the source it tests (mirroring the pre-existing `plugins/test_pipeline_loader.py` convention):

- Stage plugins: `plugins/test_<stage>.py` (e.g. `plugins/test_sequencer.py`), one per `OrchestratorStage` — except SCHEDULER, see below.
- SCHEDULER plugins: `plugins/schedulers/<name>/test_scheduler.py` — one copy per scheduler plugin (`scheduler_rosace_2026/`, `blank/`), see below.
- Sequencer sub-plugins: `plugins/sequencer_plugins/<name>/test_<name>.py` (canonical/indi) + `plugins/sequencer_plugins/<name>/test_<name>_internal.py` (demo exception, only where one exists), see below.
- Reduction sub-plugins: `plugins/reduction_plugins/<name>/test_<name>.py`.
- Sharing sub-plugins: `plugins/sharing_plugins/<name>/test_<name>.py`.

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

On any plugin error in the observation loop, the error is recorded in the DB, and the loop restarts from **SCHEDULER** (not from INIT_SESSION) — except `SchedulerFatalError` (see SCHEDULER plugins below), which stops the session instead, since it signals a configuration/data problem that retrying cannot fix.

**Plugins never block waiting on external conditions.** The active SCHEDULER plugin is the one plugin that could, in principle, need to wait (for a target to become observable) — but it must still answer within a few seconds: if nothing is observable right now, it sets `obs.scheduler_data["target"] = None` and returns immediately, rather than sleeping internally. `session_runner()` treats `target is None` as "nothing to do yet" (not an error — no `db_set_error`, no restart-from-SCHEDULER log): it waits `SCHEDULER_RETRY_SECONDS` (`config.py`, in small interruptible steps via `_interruptible_sleep()` so a stop request during the wait still takes effect promptly) and asks SCHEDULER again. This is why no SCHEDULER plugin imports `main.py` or touches `state`: earlier, the default scheduler used to poll `state.is_running` in its own internal wait loop, which required a late/deferred `from .. import main` (a top-level `from ..main import state` fails at import time with a circular-import error, since `main.py` imports the plugin registry before defining `state`). Removing the internal wait removed the need for that coupling entirely.

### Plugin system (three mechanisms)

**Stage plugins** (`plugins/__init__.py` registry) — one per `OrchestratorStage` enum value. Two interfaces (`models.py`): `SessionStagePlugin` for `INIT_SESSION`/`CLOSE_SESSION` (`run(self, ctx: SessionContext) -> SessionContext`), and `ObservationStagePlugin` for `SCHEDULER` → `ARCHIVE` (`run(self, obs: Observation) -> Observation`).

**SCHEDULER plugins** (`plugins/schedulers/` registry) — the SCHEDULER stage itself is pluggable: several interchangeable target-selection algorithms can coexist, each in its own subdirectory with its own class and its own tests, one chosen at startup via `active_scheduler` (`conf/config.yaml`, resolved to `ACTIVE_SCHEDULER` in `config.py`). This is a distinct axis from `SEQUENCER_BACKEND` below — that one is a closed, binary technical choice (simulated vs. real hardware protocol for the *same* sequencing logic); this one is an open-ended set of alternative *scheduling algorithms*, meant to grow as different observers contribute their own. `plugins/schedulers/__init__.py::_build_registry()` is the explicit name→class registry (no filesystem auto-discovery, consistent with `pipeline_loader.py`'s own registry below); `load_scheduler()` instantiates the active one, called once by `plugins/__init__.py` at import time. `SchedulerFatalError` also lives here (`plugins/schedulers/__init__.py`) rather than inside any one scheduler, since any SCHEDULER plugin may raise it.

  Every SCHEDULER plugin must honor the same `scheduler_data` contract (documented in full, in English, in `plugins/schedulers/blank/scheduler.py`'s docstring — the copyable template): populate `target` (a `Target`), `queried_name`, `programme`, `priority`, and `exposure` (an `ExposureConfig`) when a target is selected, or set `target = None` when nothing is observable right now (see the "never block" rule above). This contract is **not** checked at runtime in plugin code — by design, to keep plugins simple — but every scheduler's own test suite must verify it via `conftest.py::assert_scheduler_contract()`, with explicit failure messages naming the offending key.

  Two schedulers exist today: `schedulers/scheduler_rosace_2026/` — the real one, described below — and `schedulers/blank/` — a minimal example (hardcoded Vega/Arcturus/Castor, no `targets.db` access at all) meant purely as a template to duplicate when writing a new scheduler. To add one: duplicate `schedulers/blank/`, implement `run()`, write its tests (checking the contract via `assert_scheduler_contract()`), register the class in `_build_registry()`, add its name to `config.py`'s `_VALID_SCHEDULERS`, and set `active_scheduler` accordingly.

**Sub-plugins** — loaded dynamically by `pipeline_loader.py` at runtime from a YAML programme file, one reload per observation (`obs.scheduler_data["programme"]`, set by the active SCHEDULER plugin — there is no global "active programme" setting). Sub-plugins share the same interface as stage plugins: `execute(self, obs: Observation) -> Observation` (`sub_plugin_base.py`). Each sub-plugin writes its own nested sub-dict inside its parent stage's casier, keyed by its own declared name (e.g. `sequencer_data["slew"] = {"target": ...}`, `reduction_data["bias"] = {"corrected_files": [...]}`) — presence of the key means the sub-plugin completed, no separate `"done"` flag needed. A sub-plugin has access to the whole `obs` object — every casier collected so far, not just its own stage's — and simply reads whichever specific key(s) it needs (e.g. `dark` reads `reduction_data["bias"]`, `flat` reads `reduction_data["dark"]`): this is an implementation choice made by whoever writes that sub-plugin, not a dependency the framework tracks or enforces. If the exact key it looks for isn't there, it raises an explicit error naming what's absent. No fallback chains: the programme YAML is trusted to sequence steps consistently with what each sub-plugin actually reads, not defended against at runtime. Sub-plugins have no config of their own — all configuration lives in `conf/` (see below), consistent with the rest of the app.

  **One directory per sub-plugin**, named after the sub-plugin itself (e.g. `sequencer_plugins/slew/`, `reduction_plugins/bias/`) — chosen deliberately over a purely flat layout so the catalogue of sub-plugins stays discoverable/documentable as it grows (a real concern once many programmes/equipment variants each need their own sub-plugins, most unused by any single observation). Resolved **dynamically via `importlib`** by `pipeline_loader.py` — a sub-plugin declared `"<name>"` in a programme YAML resolves to module `<name>/<name>.py`, class `<Name>Plugin`, by naming convention alone. No central registry file to maintain, and only the sub-plugins actually declared by the programme being loaded are imported — not the whole catalogue. Both the programme name and every step name (sequencer/reduction/sharing) are validated against `^[A-Za-z0-9_]+$` (`pipeline_loader.py::_validate_name()`) before being interpolated into a filesystem path or a Python module path — defense in depth against path traversal, since `ProgramCreate.name` (below) is already validated at the API boundary but this module has no way to know a given caller did that.

- Sequencer sub-plugins: `plugins/sequencer_plugins/<name>/` (`slew`, `focus`, `expose`) — read/write `obs.sequencer_data`, read `obs.scheduler_data` for target/exposure. The only sub-plugin family with a backend axis (`SEQUENCER_BACKEND`, `config.py`), resolved **inside each sub-plugin's own directory** rather than as a separate directory tree:
  - `<name>/<name>.py` — the canonical, no-suffix filename, used whenever there's no backend ambiguity: always for REDUCTION, and for SEQUENCER when `SEQUENCER_BACKEND="indi"`. Currently a **placeholder** (`sleep(1)`, same contract as the demo version) — real INDI/PHD2 integration is a future chantier. The real device-driving classes (see `hardware_indi/` below) already exist and are used by `INIT_SESSION`'s connectivity check, but aren't called from these sub-plugins yet.
  - `<name>/<name>_internal.py` — a **deliberate, narrow exception**, not a first-class backend: used only when `SEQUENCER_BACKEND="internal"` (simulation, démonstrateur niveau 1). Since `internal` exists purely as a demo, **only the sub-plugins actually needed for the demo programme have an `_internal.py` file** — its absence for a given sub-plugin is normal, not a gap. Explicitly rejected: nesting `internal`/`indi` as a subdirectory inside each sub-plugin (`slew/internal/slew.py`) — the user's call: it forces a full duplicate implementation for zero benefit, since `internal` doesn't need first-class-citizen status. A future `ascom` backend, if it ever needs its own duplicate `<name>_ascom.py` per sub-plugin, is the concrete trigger that would justify resolving the backend axis at the device layer instead (see `hardware_indi/` and the hardware-device-abstraction direction, not yet designed) — not decided yet, deliberately deferred.
  - `pipeline_loader.py::_resolve_module_and_class()` does this resolution; `SequencerPlugin.run()` itself stays agnostic of the backend.
- Reduction sub-plugins: `plugins/reduction_plugins/<name>/` (`bias`, `dark`, `flat`, `extract`) — read/write `obs.reduction_data`; `bias` (the first step) reads `obs.sequencer_data["expose"]["raw_files"]` as its initial input, each step after it reads a specific earlier nested key it was written to expect (`dark` reads `reduction_data["bias"]`, `flat` reads `reduction_data["dark"]`, `extract` reads `reduction_data["flat"]`) — a plain implementation choice by whoever wrote that sub-plugin, not a search across candidates. No backend axis at all: these steps process already-acquired files, no hardware dependency — the INDI/ASCOM axis never concerns them, and their module name is never suffixed.
- Sharing sub-plugins: `plugins/sharing_plugins/<name>/` — read `obs.reduction_data["extract"]["reduced_files"]`, write their own nested key in `obs.sharing_data`. Unlike SEQUENCER/REDUCTION, a programme declares exactly **one** (`sharing: <name>`, a singular YAML key, not a list) — `SharingPlugin.run()` (`plugins/sharing.py`) resolves and calls that single sub-plugin via `pipeline_loader.py::load_sharing_plugin()`, instead of iterating a list. A programme that needs to notify several destinations handles that *inside* its one sharing sub-plugin — the mechanism itself deliberately doesn't support a list, to keep it simple. `sharing_plugins/blank/` is the only one that exists today — a placeholder (logs each file, no real email/FTP/API yet, that's a separate future chantier) used by every programme for now, and doubles as the copyable template (its docstring documents the contract in full, in English), same idea as `schedulers/blank/`.
- Programme definitions: `rosace/programmes/*.yaml` — declare which sub-plugins run in order (`sequencer`, `reduction`) plus the single `sharing` sub-plugin. `demo.yaml` is a minimal, backend-agnostic programme (same step names as any other) used as the default for a fresh install and for quick end-to-end verification; `etoiles_be.yaml` and others remain on disk for later use once real target/programme selection exists.

To add a new sub-plugin: create `plugins/<sequencer_plugins|reduction_plugins|sharing_plugins>/<name>/<name>.py` (and `<name>_internal.py` too, only if a demo counterpart is actually needed — SEQUENCER only) with its own `test_<name>.py`, then declare its name in the relevant programme YAML (a list entry for `sequencer`/`reduction`, the single `sharing` value for sharing) — no registry file to edit.

### Session context and the observation dossier (`SessionContext`, `Observation`)

Two separate Pydantic objects (`models.py`), deliberately not merged:

- **`SessionContext`** — passed only to `INIT_SESSION`/`CLOSE_SESSION`. Minimal and stable for the whole night: `session_id`, `started_at`, `site_name`, `session_dir`. Recorded once in the `sessions` table.
- **`Observation`** — passed from `SCHEDULER` through `ARCHIVE`, unwrapped (no `ObservationContext` wrapper — it was removed; the dossier *is* what flows). `main.py`'s `session_runner()` creates a fresh, empty instance for each observation (`id`, `created_at`, `session_id`, `obs_dir` — the only fields the orchestrator itself knows or sets) right before calling the active SCHEDULER plugin, which fills it in.

  The orchestrator never reads or writes business data — each stage plugin (and its sub-plugins) owns one casier, free-form (`dict[str, Any]`), that only it writes to: `scheduler_data`, `sequencer_data`, `reduction_data`, `sharing_data`, `archive_data`. `models.py` only declares that these casiers exist, never what's inside them. The one deliberate exception: `Observation.suggested_filename` (a `@property` on the model itself) reads `self.scheduler_data["target"].name` purely to build a human-readable filename — a conscious, minimal, cosmetic exception, kept on the model (not in `main.py` or a stage plugin) precisely so `main.py` and the plugins never need to know it exists. Computed lazily on every access, not cached — reflects whatever `scheduler_data` currently holds (falls back to `"target"` before SCHEDULER has run).

  `Observation` also carries two top-level fields, not casiers (neither is owned by one stage) — `program_file` (the programme YAML's filename, e.g. `"demo.yaml"`) and `program_steps` (the expected step lists, `{"sequencer": [...], "reduction": [...], "sharing": "blank"}`, the literal names declared in that YAML — `sequencer`/`reduction` are lists, `sharing` is the single value, matching its own singular YAML key) — both set together by `SequencerPlugin.run()` as its very first action, before executing any sub-plugin, so they're already on disk (`main.py` writes `obs` to its YAML file even on the exception path) even if the observation later crashes mid-pipeline. This is what lets a crash be diagnosed later: the nested casier data alone only proves what *succeeded*, not what was *supposed* to happen. Deliberately called `program_steps`, not `pipeline`: "pipeline" is internal plumbing vocabulary (`pipeline_loader.py`, `load_pipelines()`'s ordered lists of sub-plugin instances) — the observation file only ever talks about the *programme*, the scientific concept the observer actually asked for.

  `Observation` is also serialized as-is to the observation's YAML file (`obs.yaml_file`) after **every** stage (`observation_file.py::write_observation_file()`, called from both the foreground loop and `run_post_obs()`) — the file only grows, becoming the complete, self-contained record of the observation by the time ARCHIVE finishes. Useful for crash diagnosis: whatever was last written on disk shows exactly how far the observation got.

Naming convention: the local variable holding an `Observation` is `obs`, a `SessionContext` is `session`. Plain `ctx` is reserved for the few genuinely generic spots (e.g. `run_stage()`) that accept either type.

### Two SQLite databases

- **`rosace.db`** (`database.py`) — sessions and observations. Functions: `db_init`, `db_insert_session`, `db_close_session`, `db_insert_observation`, `db_stamp_stage`, `db_set_error`, `db_set_yaml_file`, `db_get_recent_observations`, `db_count_observations_for_target`. `db_get_recent_observations` only returns observations from the current *astronomical day* (local noon to noon, via `_astronomical_day_start_utc()`) — assumes the server runs in the site's own local timezone, since there is no explicit site timezone setting anywhere in `conf/`. Each observation row also records `programme_name`, `queried_name`, and `priority` (from `obs.scheduler_data["programme"]`/`["queried_name"]`/`["priority"]`) alongside `target_name` — `db_count_observations_for_target(programme_name, target_name)` counts how many observations already exist during the current *astronomical day* (not scoped to a single `session_id` — stopping and restarting the session doesn't reset the count) for a given (programme, target) pair, which is how the default scheduler (`scheduler_rosace_2026`) knows a target has already been "consumed" (see below).
- **`targets.db`** (`targets.py`) — stars catalogue and scientific programs (N-N via `program_stars`, which also carries an optional per-pair `exposure_time`). CRUD functions follow the pattern `star_*` / `program_*`. `ProgramCreate.name` is restricted to `^[A-Za-z0-9_]+$` (a `field_validator`, logging the rejection) — this name later becomes a filesystem path and a Python module path in `pipeline_loader.py` (see above), so anything else is rejected before it ever reaches the database. Stars also have a `star_aliases` table (alternate names, e.g. catalogue cross-references) — `star_get_by_name()` tries the canonical `stars.name` first, falls back to `star_aliases` if nothing matches. Populated exclusively through `main.py`'s REST API (`/stars`, `/programs`, `/simbad/import` — all addressed by name, never by internal id, since the id is not something a user thinks in terms of); of the SCHEDULER plugins, only `scheduler_rosace_2026` reads it (see below), and does so read-only — `blank` never touches it. Two bulk-import endpoints exist for populating the catalogue quickly: `POST /stars/import-list` (uploaded text file, one star name per line, each line goes through the same Simbad-backed logic as `/simbad/import`) and `POST /programs/import-list` (uploaded YAML file, `{programme: [star, ...]}`, links existing stars to existing programmes — no Simbad call, both must already exist). Both accept real file uploads (`UploadFile`, `python-multipart`), not a server-side path, so the GUI or caller can be on a different machine than the server. Both return a per-line report (`status`: `created`/`existing`/`error` or `linked`/`program_not_found`/`star_not_found`) rather than failing the whole batch on the first bad line.

Default path is `~/.rosace/databases/` (`config.py`). Both databases are created automatically on startup.

### Authentication (`auth.py`)

Session-cookie auth — added because the API had none: anyone reaching the port could start/stop a session or edit the catalogue. `users`/`auth_sessions` tables live in `rosace.db` (same file `database.py` uses, own module) — named `auth_sessions`, not `sessions`, specifically to not collide with `database.py`'s own unrelated `sessions` table (one row per observation night). Passwords hashed with `bcrypt`; a session is an opaque `secrets.token_urlsafe(32)` token in an `HttpOnly`/`SameSite=Lax` cookie (`rosace_session`), checked against `auth_sessions` on every request — not a signed JWT, deliberately: no secret-key rotation to manage, and revoking a session is just deleting a row.

Every existing REST route sits behind `main.py`'s `protected` `APIRouter` (`dependencies=[Depends(get_current_user)]`) — default-closed: a new route must be added to `protected` (or `admin_router`) consciously, the opposite of a middleware allowlist someone could forget to update for a route added later. `admin_router` (`prefix="/auth/users"`) additionally requires `is_admin` (`Depends(require_admin)`), for account management (`POST`/`GET`/`DELETE /auth/users...`). `/ws` is on neither router — a `Depends()`-raised `HTTPException` doesn't close a WebSocket handshake cleanly — it's checked by hand in `ws_logs()`, before `accept()`, reading the same cookie directly. `POST /auth/login`/`POST /auth/logout` are the only public API routes, declared directly on `app`.

First admin: `python -m rosace.main --create-admin <username>` (password prompted via `getpass`, never passed as an argument, to keep it out of shell history/`ps`). Refuses once any account already exists (`ensure_first_admin()`) — creating further accounts after that goes through `POST /auth/users` (admin-only), not shell access to the machine. If the only admin account is ever lost, direct `sqlite3`/DB-browser access to `rosace.db`'s `users` table is the deliberate recovery path (no separate "forgot password" flow) — just keep `password_hash` a real bcrypt hash if editing it by hand, a plaintext value there fails login silently.

`SESSION_TTL_HOURS`/`SESSION_COOKIE_SECURE` — see config table below. Session length defaults long (14 days) since the app runs unattended overnight; `secure` defaults `false` since the app typically serves plain HTTP on the local network — `true` would silently stop the browser sending the cookie back over HTTP, including on `localhost`.

### The default scheduler: `scheduler_rosace_2026` (`plugins/schedulers/scheduler_rosace_2026/scheduler.py`)

Reads `OBSERVATION_LIST_PATH` (`config.py`) fresh on every call — a plain-text, read-only CSV plan (`Priority, Program, Star, commentaire`) that this plugin never modifies. Each line is resolved against `targets.db` (programme exists, star exists — by canonical name or alias, see above — and is linked to that programme via `program_stars`); invalid lines are logged and skipped, never fatal. Valid lines are sorted by priority ascending (1 first; file order breaks ties) and walked **individually, one line at a time** — deliberately not collapsed into one group per (programme, canonical star) with a single priority, since the same target can appear on multiple lines at *different* priorities (e.g. priority 2 and priority 4), and another target at an intermediate priority (3) must then be offered between the two occurrences, not after both. For each line, its rank among occurrences of the same (programme, canonical star) seen so far in this pass — two lines naming the same star under different aliases count as the same target — is compared against `db_count_observations_for_target()` (this astronomical day — not scoped to a single session, see above): once that rank has already been fulfilled, the line is skipped. This is how a target gets "consumed" without ever rewriting the plan file, and without a session restart mid-night re-offering already-consumed targets. If every line is either invalid or already fully consumed, `scheduler_data["target"] = None` (see the `target=None` contract above). On success, populates `scheduler_data["target"]` (canonical `Target`), `["queried_name"]` (the name as written in the file — may be an alias), `["programme"]`, `["priority"]`, and `["exposure"]` (`ExposureConfig`, `exposure_time` taken from `program_stars` if set there, otherwise `None` — computing it from magnitude is a future chantier, not yet implemented, so `ExposureConfig.exposure_time` is `Optional`). No real observability check (altitude, night hours…) yet — also future work.

A missing/malformed plan file, or invalid data in `targets.db` (e.g. `program_stars.exposure_time` corrupted to `''` instead of `NULL` by an external tool) raises `SchedulerFatalError` — a dedicated exception, distinct from an ordinary exception. `session_runner()` treats it like an `INIT_SESSION` failure (`state.is_running = False`, session stops) instead of the usual "log and restart from SCHEDULER" retry loop, since retrying can't fix a configuration/data problem — this is the difference between `SchedulerFatalError` (stop) and any other exception from a stage (retry).

### Observation YAML persistence (`observation_file.py`)

Plays the same role for the observation's YAML file that `database.py` plays for SQLite: a dedicated persistence module, kept separate from both the orchestrator (`main.py`) and the pure data model (`models.py`). `create_observation_file(obs, target_dir)` fixes `obs.yaml_file` (using `obs.suggested_filename`) without writing anything; `write_observation_file(obs)` serializes the current full state (`obs.model_dump(mode="json")`) to that path — a no-op until `create_observation_file` has run. `main.py` only ever calls these two functions, the same way it calls `db_insert_session` — it never touches YAML or filesystem details directly.

### INDI hardware drivers (`hardware_indi/`) and connectivity check (`plugins/hardware_connectivity.py`)

`hardware_indi/` (`wrapper/` + `devices/`) holds the real, blocking (non-asyncio) Python classes that talk to an INDI server — one class per device type (`devices/camera.py::IndiCamera`, `devices/mount.py::IndiMount`, `devices/focuser.py::IndiFocuser`, etc.), all built on `wrapper/indi_device.py::IndiDevice` and `wrapper/indi_client.py::IndiClient`. Distant heritage: adapted from the PANOPTES/POCS observatory control software — the POCS-specific config loader and MongoDB-backed database layer that the original `Base` class used to pull in on every instantiation have been stripped out (`wrapper/base.py` is now just a logger), since Rosace has its own config/databases that have nothing to do with POCS's. Devices sharing the same INDI server (`indi_host`/`indi_port`) share a single `IndiClient` instance under the hood (`SingletonIndiClientHolder`), whose setup is not thread-safe.

Each device's class is declared in the active hardware YAML (`HARDWARE_CONFIG_PATH`, e.g. `conf/hardware_indi_simu.yaml`) via `module`/`module_dir`/`class_name` fields and loaded **dynamically** (`importlib`) rather than imported directly — this is what lets the set of devices vary freely per hardware profile (a camera here, an ASI camera there, an extra `pointing_camera` in one profile but not another) without any Python code change. Each device entry also declares a `role` field (`mount`/`camera`/`focuser`/`filter_wheel`) — an explicit string, not inferred from the class — used to pick which `hardware_indi/adapters/` class wraps it (see below); a device whose role has no registered adapter (e.g. a spectroscope) is still connected but simply isn't included in the session's hardware mapping.

`plugins/hardware_connectivity.py::connect_session_hardware()` is called from `INIT_SESSION` (`init_session.py`), **only when `SEQUENCER_BACKEND == "indi"`**: it reads the hardware YAML into a plain dict, dynamically loads and connects every declared `INDI_drivers` device — **for the whole session, connections are left open** — plus checks PHD2 reachability (plain TCP check — no PHD2 client library yet, that's a future chantier), each bounded by `HARDWARE_CONNECT_TIMEOUT_SECONDS` (single attempt, no retry). Because of the shared-`IndiClient` thread-safety concern above, devices are connected **one at a time** (not concurrently); PHD2, an independent plain `asyncio` TCP connection, runs concurrently with the device connects. Each blocking device connect runs in a worker thread (`asyncio.to_thread`) so it never blocks the FastAPI event loop. Any failure (timeout or exception, on any device or PHD2) raises a single aggregated `RuntimeError`, which `INIT_SESSION` lets propagate — `session_runner()` already treats an `INIT_SESSION` exception as a critical, session-cancelling failure, so no extra error handling is needed there. Known simplification: if one device fails partway through, devices already connected before it are left connected rather than explicitly rolled back — acceptable since the session is about to stop anyway, revisit if it becomes a real problem.

### Generic device interfaces (`hardware/`) and INDI adapters (`hardware_indi/adapters/`)

`hardware/` holds backend-agnostic device contracts — one `typing.Protocol` per device role (`hardware/mount.py::Mount`, `camera.py::Camera`, `focuser.py::Focuser`, `filter_wheel.py::FilterWheel`), plus `hardware/errors.py`'s generic exception hierarchy (`DeviceError` → `DeviceConnectionError`/`DeviceTimeoutError`/`DeviceCommandError`). SEQUENCER sub-plugins are meant to depend only on these, never on a specific backend's classes — this is what keeps the door open to a future `hardware_ascom/` (Ascom/Alpaca) without touching sub-plugin code, see the "Next chantiers" direction. Each interface declares only what Rosace actually needs today, not full API coverage — e.g. `Focuser` has no `autofocus()`, since the underlying INDI focuser doesn't expose one either (autofocus is a measure-move-remeasure algorithm that belongs in the focus SEQUENCER sub-plugin, not the device layer); `Camera` covers any camera role (science, guiding — see `hardware_indi_simu.yaml`'s `science_camera`/`guiding_camera` entries) via one shared contract, not a per-role interface. Method names deliberately follow ASCOM's own published device interfaces (naming only, not philosophy — INDI's more open design is still the model followed elsewhere).

`hardware_indi/adapters/` (`mount.py::MountAdapter`, `camera.py::CameraAdapter`, `focuser.py::FocuserAdapter`, `filter_wheel.py::FilterWheelAdapter`) are the concrete INDI implementations: each owns a device instance's whole lifecycle — `connect()`, business calls, `disconnect()` — running every blocking INDI call via `asyncio.to_thread`, so the generic interfaces stay uniformly async regardless of backend. `CameraAdapter.expose()` runs the whole blocking acquisition sequence (`prepare_shoot` → `setExpTimeSec` → `shoot_async` → `synchronize_with_image_reception` → `get_received_image`) as one unit in a single worker thread, since these must happen in strict order on the same device. Adapters translate whatever `IndiDevice`/`pyindi-client` raises into `hardware/errors.py`'s generic hierarchy.

**Wired into SEQUENCER**, minimally — `slew.py`/`expose.py` (backend indi) call `obs.hardware["mount"].slew_to(...)`/`obs.hardware["science_camera"].expose(...)` for real; `focus.py` is deliberately left as a placeholder (no autofocus algorithm exists yet, see `hardware/focuser.py`'s docstring), and `expose.py` writes exactly one FITS file per observation — no `num_exposures` stacking, no exposure-time-from-magnitude — both explicitly deferred until these sub-plugins become real features rather than minimal wiring. `Observation.hardware`/`SessionContext.hardware` (`models.py`) carry the connected adapters, keyed by device name — `Field(exclude=True)`, never persisted to the YAML dossier or the DB, since these hold live connections, not observation data. `InitSessionPlugin` populates `SessionContext.hardware`; `session_runner()` (`main.py`) copies it onto every freshly-created `Observation` (sub-plugins never see `SessionContext`); `CloseSessionPlugin` disconnects each adapter (one failure doesn't block the others). One correctness detail worth remembering: `session_runner()`'s `obs.model_copy(deep=True, update={"hardware": {}})` before handing a completed observation to the background post-obs task deliberately excludes `hardware` from the deep copy — `copy.deepcopy()` on a live device connection is meaningless and likely to break; post-obs stages (REDUCTION/SHARING/ARCHIVE) never touch hardware anyway.

Not yet done: no `hardware_ascom/` (deferred, no Windows environment to validate against yet); no real autofocus algorithm (`focus.py` stays a placeholder).

### Simbad client (`simbad.py`)

Queries CDS TAP via ADQL over HTTPS. Returns `StarCreate`. Raises `SimbadNotFoundError` (404) or `SimbadQueryError` (network/HTTP errors). In tests, mock `rosace.simbad.httpx.AsyncClient` as an async context manager.

### Configuration (`rosace/rosace/conf/`)

All application configuration lives in `conf/` — one predictable place to look, split into separate files by concern rather than crammed into a single monolith:

| File | Purpose |
|---|---|
| `base_data_dir.yaml` | Single field (`base_data_dir`), read before everything else to locate this installation's own active `config.yaml` — see "Which config.yaml is active" below |
| `config.yaml` | Core application settings (data paths, site name, active hardware profile, active scheduler) — the packaged copy is demo defaults; a real deployment's own copy lives under `<base_data_dir>/conf/config.yaml` instead |
| `logging.cfg` | Logging setup (`fileConfig` ini format — see below) |
| `hardware_<variant>.yaml` | Hardware/instrument definitions, one file per variant (e.g. `hardware_indi_simu.yaml` for the INDI simulators, `hardware_indi_72ED.yaml` for a real telescope). Which variant is active is selected via `active_hardware` in `config.yaml` (resolved to `HARDWARE_CONFIG_PATH` in `config.py`). Declares, under `INDI_drivers`, one entry per device (`role`, `module`/`module_dir`/`class_name` for dynamic loading, plus device-specific fields incl. `indi_client: {indi_host, indi_port}`), and a `PHD2_config` section (`host`/`port`/...) — parsed by `plugins/hardware_connectivity.py` when `sequencer_backend=indi`. |

Note: `programmes/*.yaml` (scientific programme definitions) intentionally live outside `conf/` — they're a catalogue of interchangeable observation plans, not app configuration.

### Which config.yaml is active (multi-instrument support)

The packaged `conf/config.yaml` is a **demo default**, meant to work out of the box — but a real deployment's own settings (site name, active hardware, observation list...) shouldn't have to live inside the git-tracked code tree, and someone running more than one instrument (e.g. a main telescope plus a demo scope) needs more than one active config. `config.py::_resolve_config_path()` picks the active file in priority order, evaluated once at import time:

1. **`ROSACE_CONFIG` env var**, if set — an explicit path (any location, `~` expanded), highest priority. `python -m rosace.main <path>` sets this from a positional CLI argument before any other import runs (see `main.py`'s top-of-file CLI handling, required to run before `from .config import ...` triggers `config.py`'s own loading); a `uvicorn rosace.main:app` launch needs the env var set directly instead (`ROSACE_CONFIG=<path> uvicorn ...`). Meant for one-off overrides — a second instrument, a test config — not the everyday case.
2. **`<base_data_dir>/conf/config.yaml`**, if it exists — where `base_data_dir` comes from `conf/base_data_dir.yaml` (a single-field file, `base_data_dir: ~/.rosace` by default). This is a real installation's own config, found automatically on every launch with no argument needed — the day-to-day case once you've configured your own instrument. `conf/base_data_dir.yaml` itself always lives in the package's own `conf/`: it has to be found before `base_data_dir` is even known, so it can't live under `base_data_dir` itself.
3. **The packaged `conf/config.yaml`** — used only if neither of the above applies (fresh install, nothing configured yet).

`python -m rosace.main --check-config [path]` validates a config without starting the server: with no argument, it validates whatever step 1–3 above would resolve to; with a path, it validates that file specifically (via the same `ROSACE_CONFIG` mechanism). Prints `[OK]`/`[FAIL]` and exits 0/1 — never reaches `uvicorn.run()` either way, so it's safe to run against a candidate config without risking a real session starting.

`python -m rosace.main --help` (or `-h`) prints this same CLI's usage — the positional `config_path` argument, `--check-config`, and `--create-admin` — and exits, without starting the server either.

`python -m rosace.main --create-admin <username>` bootstraps the first admin account (see Authentication above) and exits — also never starts the server.

### Key configuration (`config.py` loads `conf/config.yaml`)

`config.py` is a thin loader: it resolves which `config.yaml` is active (see above), reads it at import time, and exposes the same constants the rest of the codebase already imports (`from .config import BASE_DATA_DIR, ...`).

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
| `OBSERVATION_LIST_PATH` | `observation_list_path` | `~/.rosace/ObservationList.txt` | Text file (CSV: `Priority, Program, Star, commentaire`) the `scheduler_rosace_2026` plugin reads to pick the next target — see below. Any location, not necessarily under `BASE_DATA_DIR`. |
| `ACTIVE_SCHEDULER` | `active_scheduler` | `"scheduler_rosace_2026"` | Name (registry in `plugins/schedulers/__init__.py`) of the active SCHEDULER plugin: `"scheduler_rosace_2026"` (real target selection) or `"blank"` (minimal hardcoded example) |
| `SEQUENCER_BACKEND` | `sequencer_backend` | `"internal"` | Which SEQUENCER sub-plugin family to load: `"internal"` (simulation, no external dependency) or `"indi"` (real INDI/PHD2 protocol, placeholder for now) |
| `HARDWARE_CONNECT_TIMEOUT_SECONDS` | `hardware_connect_timeout_seconds` | `5` | `INIT_SESSION` (`sequencer_backend=indi` only): max time to wait for each INDI device / PHD2 to answer |
| `SESSION_TTL_HOURS` | `session_ttl_hours` | `336` (14 days) | How long a login session (cookie) stays valid — see Authentication above |
| `SESSION_COOKIE_SECURE` | `session_cookie_secure` | `false` | Whether the session cookie requires HTTPS (the `Secure` attribute) — see Authentication above |

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
- Login: `auth.ts::LoginView` is shown before the dashboard by `AppShell` (`main.ts`) — the actual `setMainView()` target (x4js doesn't support calling `setMainView()` a second time to swap the root view — its `mounted` guard is declared but never actually set to `true` anywhere in `core_application.ts`, confirmed by reading the source, not assumed), which instead swaps its own content between `LoginView` and a freshly-built `MainView` via `setContent()`. `fetchStatus()`/`fetchObs()` stop rescheduling themselves and fire a local `"auth.logout"` global event on a 401 (session expired) rather than polling forever unauthenticated in the background; `AppShell` listens for that same event (also fired by the "Déconnexion" item in `AppHeader`'s menu) to show the login screen again. `MainView` is deliberately rebuilt from scratch on every `showMain()` rather than cached — a cached instance's polling loop, once stopped by a 401, would never resume on the next login.
- Two non-obvious x4js behaviors hit while building the login form, worth knowing before writing another one: `Button._on_click()` always calls `preventDefault()`/`stopPropagation()`, whether or not a `click` prop was given — a `<button>` inside a `Form` therefore never triggers the browser's native submit-on-click, so wire the button's own `click` prop directly rather than relying on the form's `submit` event for anything a click should do. And `Form` extends `Box`, not `VBox` — `%box` (`shared.scss`) only sets `position:relative`, no `display:flex` — so a `Form`'s own stylesheet needs explicit `display:flex;flex-direction:column` (or `row`) for `gap`/child stacking to work at all; it isn't inherited the way it would be from `VBox`/`HBox`.

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
