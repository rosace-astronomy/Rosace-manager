# Documentation — Rosace
## Spectroscopic Observation Orchestrator

---

## Overview

**Rosace** is an asynchronous REST API (FastAPI + Python) that drives a spectroscopic observation pipeline end-to-end. It is designed to run autonomously at night: once started, it loops through observations according to a state machine, from target selection to archiving of reduced spectra.

The observation site name is **St-Pancrasse** (configurable in `session_runner`).

---

## Project structure

```
rosace/
├── __init__.py          # Package declaration and presentation docstring
├── config.py            # File paths and logging configuration
├── models.py            # Pydantic models + plugin interface (StagePlugin)
├── database.py          # SQLite access layer — sessions & observations
├── targets.py           # SQLite access layer — stars & programs
├── simbad.py            # HTTP client for the Simbad TAP API (CDS)
├── main.py              # FastAPI entry point, state machine, endpoints
└── plugins/
    ├── __init__.py      # Plugin registry (dict stage → instance)
    ├── init_session.py  # INIT_SESSION stage
    ├── scheduler.py     # SCHEDULER stage
    ├── sequencer.py     # SEQUENCER stage
    ├── reduction.py     # REDUCTION stage
    ├── sharing.py       # SHARING stage
    ├── archive.py       # ARCHIVE stage
    └── close_session.py # CLOSE_SESSION stage
```

---

## Entry point

The server is started with **uvicorn**:

```bash
uvicorn rosace.main:app --host 0.0.0.0 --port 8000
```

Or directly when the module is executed:

```bash
python -m rosace.main
```

The FastAPI application is instantiated in `main.py` as `app`. On startup it does **not** launch a session automatically: `POST /control/start` must be called explicitly.

Interactive Swagger documentation is available at `http://localhost:8000/docs`.

---

## Configuration (`config.py`)

Two central constants control file locations:

| Constant        | Default value           | Purpose                                         |
|-----------------|-------------------------|-------------------------------------------------|
| `BASE_DATA_DIR` | `/tmp/rosace`           | Root directory for all session data             |
| `DB_PATH`       | `/tmp/rosace/rosace.db` | Main SQLite database (sessions + observations)  |

The targets database is defined in `targets.py` as `TARGETS_DB_PATH` (`/tmp/rosace/targets.db`).

Logging is configured at `INFO` level with timestamp and logger name.

---

## The state machine

This is the functional core of Rosace. The current state is held by the global `state` object (a `SystemState` instance) defined in `main.py`.

### Full sequence

```
IDLE
  └─► INIT_SESSION                    (once at startup)
        └─► [foreground loop]
              ├─► SCHEDULER             (select the next target)
              └─► SEQUENCER             (acquire raw frames)
                    └─► back to SCHEDULER (if is_running = True)
        └─► [background tasks, overlapped]
              ├─► REDUCTION             (process raw frames)
              ├─► SHARING               (publish reduced spectra)
              └─► ARCHIVE               (long-term archiving)
        └─► wait for all background tasks to finish
        └─► CLOSE_SESSION              (once at shutdown)
IDLE
```

**Pipeline principle**: as soon as SEQUENCER finishes, the REDUCTION → SHARING → ARCHIVE stages are launched as a background task (`asyncio.create_task`). The main loop immediately returns to SCHEDULER for the next target, without waiting for post-processing to complete. This avoids wasting telescope time.

Before executing CLOSE_SESSION, the orchestrator waits for all background tasks to finish (`asyncio.gather`).

### `OrchestratorStage` enumeration values

| Value           | Purpose                                                      |
|-----------------|--------------------------------------------------------------|
| `IDLE`          | No active session                                            |
| `INIT_SESSION`  | Initialisation: instrument connection, checks                |
| `SCHEDULER`     | Select the next target                                       |
| `SEQUENCER`     | Acquire raw exposures                                        |
| `REDUCTION`     | Raw-frame reduction pipeline                                 |
| `SHARING`       | Publish reduced spectra (email, API…)                        |
| `ARCHIVE`       | Long-term archiving                                          |
| `CLOSE_SESSION` | Shutdown: park telescope, close dome, disconnect instruments |
| `ERROR`         | Non-recoverable critical error                               |

### Error handling in the loop

**Foreground stages (SCHEDULER, SEQUENCER)**: if a plugin raises an exception:
- The error is recorded in the database on the current observation.
- The session context is reset (preserving `session_id` and `started_at`).
- The loop restarts from the beginning, i.e. the **SCHEDULER** stage.

**Background stages (REDUCTION, SHARING, ARCHIVE)**: errors are caught locally inside `run_post_obs`. If a background stage fails:
- The error is recorded in the database on the affected observation.
- The remaining stages of that background task are abandoned.
- The main loop is **not** interrupted.

Special case: `SchedulerStopRequested` (raised by the scheduler when `is_running` becomes `False`) triggers a clean exit to `CLOSE_SESSION` after waiting for any in-progress background tasks.

---

## Session context (`SessionContext`)

`SessionContext` is the shared object passed from stage to stage. Each plugin receives it as input, enriches it, and returns it. It is the working memory of the session.

| Field                 | Type          | Description                                         |
|-----------------------|---------------|-----------------------------------------------------|
| `session_id`          | `str`         | Short identifier (8-character UUID)                 |
| `started_at`          | `datetime`    | UTC start timestamp                                 |
| `site_name`           | `str`         | Site name (default: "St-Pancrasse")                 |
| `current_observation` | `Observation` | Current observation (set after SCHEDULER)           |
| `yaml_file`           | `str`         | Path to the current observation's YAML file         |
| `obs_dir`             | `str`         | Directory for the current observation               |
| `next_target_name`    | `str`         | Target name returned by SCHEDULER                   |
| `raw_files`           | `list[str]`   | Raw files produced by SEQUENCER                     |
| `reduced_files`       | `list[str]`   | Reduced files produced by REDUCTION                 |

---

## Plugins

Each stage is handled by a dedicated plugin. All implement the `StagePlugin` interface (Python protocol): a single method `async def run(self, ctx: SessionContext) -> SessionContext`.

The registry in `plugins/__init__.py` maps each `OrchestratorStage` value to its corresponding plugin instance.

### `InitSessionPlugin`
Prepares the observation night: instrument connection (INDI/ASCOM), weather check, CCD cooling. Currently simulated (`asyncio.sleep`).

### `SchedulerPlugin`
Determines the next target to observe. Stores the name in `ctx.next_target_name` and resets `raw_files`, `reduced_files`, `yaml_file`. Currently always returns `"Vega"` — intended to query the targets database. Raises `SchedulerStopRequested` if `is_running` is `False`.

### `SequencerPlugin`
Performs the spectroscopic acquisition. Reads the exposure configuration from `ctx.current_observation.request.exposure`, simulates the acquisition, and appends the raw FITS filename to `ctx.raw_files`. Requires `ctx.obs_dir` to be set.

### `ReductionPlugin`
Processes the raw files listed in `ctx.raw_files`. For each raw file, produces a `*_reduced.fits` file and appends it to `ctx.reduced_files`. The actual reduction pipeline is yet to be implemented.

### `SharingPlugin`
Distributes reduced files (`ctx.reduced_files`) to the observer or a publication platform (email, BeSS API…). Currently simulated.

### `ArchivePlugin`
Transfers reduced files to long-term storage. Currently simulated.

### `CloseSessionPlugin`
Closes the observation session: telescope parking, dome closure, instrument disconnection. Currently simulated.

---

## On-disk layout

For each session, the orchestrator creates a directory tree under `BASE_DATA_DIR`:

```
/tmp/rosace/
├── rosace.db                              # Main SQLite database
├── targets.db                             # Targets SQLite database
└── 2026-05-09_St-Pancrasse_a3f7c1b2/     # Session directory
    │   (format: date_site_session_id)
    └── 20260509T214532_Vega/              # Observation directory
        │   (format: datetime_target)
        ├── 20260509T214532_Vega.yaml      # Observation YAML record
        └── spectrum_0001.fits             # Raw file produced by SEQUENCER
```

---

## Databases

### `rosace.db` — Sessions & observations

Managed by `database.py`. Two tables:

**`sessions`** — one row per observation night  
`id`, `started_at`, `closed_at`, `status` (`running` | `closed` | `error`)

**`observations`** — one row per observed target  
`id`, `session_id`, `target_name`, `yaml_file`, `created_at`, `error_message`, plus one timestamp per stage (`scheduler_done`, `sequencer_done`, `reduction_done`, `sharing_done`, `archive_done`)

The `db_stamp_stage` function automatically records the completion time of each stage on the current observation.

### `targets.db` — Targets & programs

Managed by `targets.py`. Three tables:

**`stars`** — target star catalogue  
`id`, `name`, `ra`, `dec`, `magnitude`, `spectral_type`, `exposure_time`, `notes`

**`programs`** — scientific programs  
`id`, `name`, `description`

**`program_stars`** — N-N join table between stars and programs  
`program_id`, `star_id`

---

## Simbad client (`simbad.py`)

The module queries the CDS Strasbourg TAP service via an ADQL query. It returns a `StarCreate` object with `main_id`, `ra`, `dec`, `sp_type`, and V magnitude.

Two specific exceptions are defined:
- `SimbadNotFoundError`: the star does not exist in Simbad.
- `SimbadQueryError`: network or HTTP error.

---

## REST API — Endpoint reference

### Session control

| Method | Route            | Description                                                                    |
|--------|------------------|--------------------------------------------------------------------------------|
| `GET`  | `/status`        | Current state: active stage, completed obs. count, background task count       |
| `POST` | `/control/start` | Start the session in the background                                            |
| `POST` | `/control/stop`  | Request a clean stop after the current observation finishes                    |

The `/status` response includes the `pending_post_obs_count` field: the number of REDUCTION/SHARING/ARCHIVE tasks currently running in the background.

### Observations (in-memory cache)

| Method | Route                     | Description                               |
|--------|---------------------------|-------------------------------------------|
| `GET`  | `/observations/completed` | Last 20 completed observations            |
| `GET`  | `/observations/{obs_id}`  | Detail of an observation by its id        |

### Simbad integration

| Method | Route            | Description                                          |
|--------|------------------|------------------------------------------------------|
| `GET`  | `/simbad/search` | Search without insertion (`?name=Vega`)              |
| `POST` | `/simbad/import` | Search + insert into database (409 if duplicate)     |

### Stars

| Method   | Route                    | Description                    |
|----------|--------------------------|--------------------------------|
| `POST`   | `/stars`                 | Create a star                  |
| `GET`    | `/stars`                 | List all stars                 |
| `GET`    | `/stars/{id}`            | Detail of a star               |
| `PUT`    | `/stars/{id}`            | Update a star                  |
| `DELETE` | `/stars/{id}`            | Delete a star                  |
| `GET`    | `/stars/{id}/programs`   | Programs associated with a star|

### Scientific programs

| Method   | Route                              | Description                        |
|----------|------------------------------------|------------------------------------|
| `POST`   | `/programs`                        | Create a program                   |
| `GET`    | `/programs`                        | List all programs                  |
| `GET`    | `/programs/{id}`                   | Detail of a program                |
| `DELETE` | `/programs/{id}`                   | Delete a program                   |
| `GET`    | `/programs/{id}/stars`             | Stars belonging to a program       |
| `POST`   | `/programs/{id}/stars/{star_id}`   | Add a star to a program            |
| `DELETE` | `/programs/{id}/stars/{star_id}`   | Remove a star from a program       |

---

## Key data models

### `Target`
Target coordinates: `name`, `ra` (0–360°), `dec` (-90–90°), `magnitude` (optional).

### `ExposureConfig`
Acquisition parameters: `exposure_time` (s), `num_exposures`, `binning` (1–4), `grating` (default `R1200`), `central_wavelength` (default 6563 Å — Hα line).

### `Observation`
Groups `id`, `request` (Target + ExposureConfig), `created_at`, `files` (list of produced files).

### `StarCreate` / `Star`
Extension of Target with `spectral_type`, star-specific `exposure_time`, and `notes`. `Star` adds the SQLite `id`.

---

## Extension points (built-in TODOs)

The application is structured so that each plugin can be replaced independently. Known work items identified in the code:

- **`InitSessionPlugin`**: real equipment connection (INDI, ASCOM), weather check, CCD cooling.
- **`SchedulerPlugin`**: query `targets.db` to select the optimal target (altitude, program priority, exposure time).
- **`SequencerPlugin`**: integrate a real acquisition driver (actual FITS files, calibration frame handling).
- **`ReductionPlugin`**: spectral reduction pipeline (bias, flat, extraction, wavelength calibration).
- **`SharingPlugin`**: email delivery or upload to BeSS / another public repository.
- **`ArchivePlugin`**: transfer to a NAS or cloud storage.
- **`CloseSessionPlugin`**: telescope parking and dome closure commands.

To implement a new plugin, create a class with `async def run(self, ctx: SessionContext) -> SessionContext` and register it in `plugins/__init__.py`.
