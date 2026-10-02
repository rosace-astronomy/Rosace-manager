# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
SQLite access layer (aiosqlite) — sessions, observations, and per-stage statuses.
"""

import logging
from datetime import datetime, timezone

import aiosqlite

from .config import DATABASES_DIR, DB_PATH
from .models import OrchestratorStage, SessionContext, Observation
from .time_helpers import astronomical_day_start_utc

log = logging.getLogger("spectro.database")

# Mapping from stage to its status column name
_STATUS_COL: dict[OrchestratorStage, str] = {
    OrchestratorStage.SCHEDULER: "scheduler_status",
    OrchestratorStage.SEQUENCER: "sequencer_status",
    OrchestratorStage.REDUCTION: "reduction_status",
    OrchestratorStage.SHARING:   "sharing_status",
    OrchestratorStage.ARCHIVE:   "archive_status",
}

# Mapping from stage to its completion timestamp column name
_DONE_COL: dict[OrchestratorStage, str] = {
    OrchestratorStage.SEQUENCER: "sequencer_done",
    OrchestratorStage.REDUCTION: "reduction_done",
    OrchestratorStage.SHARING:   "sharing_done",
    OrchestratorStage.ARCHIVE:   "archive_done",
}


# ─────────────────────────────────────────────────────────────
# Initialisation
# ─────────────────────────────────────────────────────────────

async def db_init() -> None:
    """Creates tables if they do not exist."""
    DATABASES_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        # WAL: readers (e.g. GUI polling /status, /observations/recent) no longer
        # block a concurrent writer — persists in the file, set once is enough.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                started_at  TEXT NOT NULL,
                closed_at   TEXT,
                status      TEXT NOT NULL DEFAULT 'running',
                session_dir TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                id               TEXT PRIMARY KEY,
                session_id       TEXT NOT NULL REFERENCES sessions(id),
                target_name      TEXT NOT NULL,
                queried_name     TEXT,
                program_name   TEXT,
                priority         INTEGER,
                yaml_file        TEXT,
                created_at       TEXT NOT NULL,
                error_message    TEXT,
                scheduler_done   TEXT,
                sequencer_done   TEXT,
                reduction_done   TEXT,
                sharing_done     TEXT,
                archive_done     TEXT,
                scheduler_status TEXT NOT NULL DEFAULT 'pending',
                sequencer_status TEXT NOT NULL DEFAULT 'pending',
                reduction_status TEXT NOT NULL DEFAULT 'pending',
                sharing_status   TEXT NOT NULL DEFAULT 'pending',
                archive_status   TEXT NOT NULL DEFAULT 'pending',
                ended_at             TEXT,
                outcome              TEXT,
                proposed_by          TEXT,
                estimated_duration_s REAL,
                ambient_temp_c       REAL,
                camera_temp_c        REAL,
                focus_fwhm_px        REAL,
                slit_y_px            REAL,
                pier_side            TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pointing_errors (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id  TEXT NOT NULL REFERENCES observations(id),
                target_name     TEXT NOT NULL,
                recorded_at     TEXT NOT NULL,
                mount_ra_deg    REAL NOT NULL,
                mount_dec_deg   REAL NOT NULL,
                solved_ra_deg   REAL NOT NULL,
                solved_dec_deg  REAL NOT NULL,
                pier_side       TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS spectrum_flux (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id       TEXT NOT NULL REFERENCES observations(id),
                target_name          TEXT NOT NULL,
                magnitude_v          REAL,
                image_name           TEXT NOT NULL,
                recorded_at          TEXT NOT NULL,
                exposure_s           REAL,
                method_version       INTEGER NOT NULL,
                center_x             REAL NOT NULL,
                center_y             REAL NOT NULL,
                angle_deg            REAL NOT NULL,
                trace_method         TEXT NOT NULL,
                bands_used           INTEGER NOT NULL,
                trace_sigma_px       REAL NOT NULL,
                sigma_axes_px        REAL,
                background_adu       REAL NOT NULL,
                background_noise_adu REAL NOT NULL,
                total_adu            REAL NOT NULL,
                column_max_adu       REAL NOT NULL,
                column_max_x         INTEGER NOT NULL,
                peak_pixel_adu       REAL NOT NULL,
                peak_pixel_x         INTEGER NOT NULL,
                peak_pixel_y         INTEGER NOT NULL,
                saturated            INTEGER NOT NULL,
                hot_pixels_replaced  INTEGER NOT NULL
            )
        """)
        await db.commit()
    log.info(f"[DB] Database initialised: {DB_PATH}")


# ─────────────────────────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────────────────────────

async def db_insert_session(ctx: SessionContext) -> None:
    """Inserts a new session."""
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            "INSERT INTO sessions (id, started_at, status, session_dir) VALUES (?, ?, 'running', ?)",
            (ctx.session_id, ctx.started_at.isoformat(), ctx.session_dir)
        )
        await db.commit()
    log.info(f"[DB] Session inserted: {ctx.session_id}")


async def db_close_session(ctx: SessionContext, status: str = "closed") -> None:
    """Updates the session record at shutdown."""
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            "UPDATE sessions SET closed_at = ?, status = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), status, ctx.session_id)
        )
        await db.commit()
    log.info(f"[DB] Session closed: {ctx.session_id} ({status})")


# ─────────────────────────────────────────────────────────────
# Observations
# ─────────────────────────────────────────────────────────────

async def db_insert_observation(obs: Observation, session_id: str) -> None:
    """Inserts a new observation after SCHEDULER completes. proposed_by and
    estimated_duration_s are only set by the multi-program selector (None otherwise)."""
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            """INSERT INTO observations
               (id, session_id, target_name, queried_name, program_name, priority,
                proposed_by, estimated_duration_s, created_at, scheduler_done, scheduler_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done')""",
            (
                obs.id,
                session_id,
                obs.scheduler_data["target"].name,
                obs.scheduler_data.get("queried_name"),
                obs.scheduler_data.get("program"),
                obs.scheduler_data.get("priority"),
                obs.scheduler_data.get("proposed_by"),
                obs.scheduler_data.get("estimated_duration_s"),
                obs.created_at.isoformat(),
                datetime.now(timezone.utc).isoformat(),
            )
        )
        await db.commit()
    log.info(f"[DB] Observation inserted: {obs.id}")


async def db_count_observations_for_target(program_name: str, target_name: str) -> int:
    """Counts how many observations already exist during the current *astronomical
    day* (not scoped to a single session_id) for a given (program, target) pair —
    used by SCHEDULER to know how many times a line from its observation list has
    already been consumed. Scoped by night rather than by session so that stopping
    and restarting the session (e.g. after a pause) doesn't reset the count."""
    since = astronomical_day_start_utc()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            """SELECT COUNT(*) FROM observations
               WHERE created_at >= ? AND program_name = ? AND target_name = ?""",
            (since, program_name, target_name)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0]


async def db_get_successes_for_target(
    program_name: str, target_name: str, now: datetime | None = None
) -> tuple[int, datetime | None]:
    """Number of successful observations (SEQUENCER completed) of a target for
    a program during the current astronomical day, and the start time of the
    most recent one (None if there is none) — the history helper offered to
    the program schedulers. `now` defaults to the current time; a planner
    passes its SchedulingContext.now."""
    since = astronomical_day_start_utc(now)
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            """SELECT COUNT(*), MAX(created_at) FROM observations
               WHERE created_at >= ? AND program_name = ? AND target_name = ?
                 AND sequencer_status = 'done'""",
            (since, program_name, target_name)
        ) as cursor:
            count, last = await cursor.fetchone()
            return count, (datetime.fromisoformat(last) if last else None)


async def db_get_last_failure(
    program_name: str, target_name: str, now: datetime | None = None
) -> datetime | None:
    """Time of the most recent failed observation (SEQUENCER error) of a target
    for a program during the current astronomical day, or None — used by the
    program schedulers' failure backoff. ended_at when known, else created_at
    (rows recorded before ended_at existed)."""
    since = astronomical_day_start_utc(now)
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            """SELECT MAX(COALESCE(ended_at, created_at)) FROM observations
               WHERE created_at >= ? AND program_name = ? AND target_name = ?
                 AND sequencer_status = 'error'""",
            (since, program_name, target_name)
        ) as cursor:
            (last,) = await cursor.fetchone()
            return datetime.fromisoformat(last) if last else None


# Measurement name (Observation.measurements) -> column of the observations
# table. The names describe the measurement, never the sub-plugin that made it.
MEASUREMENT_COLUMNS = {
    "ambient_temp_c": "ambient_temp_c",
    "camera_temp_c": "camera_temp_c",
    "focus_fwhm_px": "focus_fwhm_px",
    "slit_y_px": "slit_y_px",
    "pier_side": "pier_side",
}


async def db_record_measurements(obs_id: str, measurements: dict) -> None:
    """Copies the declared measurements of an observation into its row; names
    not declared in MEASUREMENT_COLUMNS stay in the YAML dossier only."""
    known = {MEASUREMENT_COLUMNS[name]: value for name, value in measurements.items()
             if name in MEASUREMENT_COLUMNS}
    unknown = sorted(set(measurements) - set(MEASUREMENT_COLUMNS))
    if unknown:
        log.debug(f"[DB] Measurements not stored in the observations table: {unknown}")
    if not known:
        return
    assignments = ", ".join(f"{column} = ?" for column in known)
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            f"UPDATE observations SET {assignments} WHERE id = ?",
            (*known.values(), obs_id)
        )
        await db.commit()


async def db_set_yaml_file(obs_id: str, yaml_file: str) -> None:
    """Records the YAML file path for an observation."""
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            "UPDATE observations SET yaml_file = ? WHERE id = ?",
            (yaml_file, obs_id)
        )
        await db.commit()
    log.info(f"[DB] yaml_file recorded for {obs_id}")


async def db_set_stage_status(obs_id: str, stage: OrchestratorStage, status: str) -> None:
    """Sets the status of a stage for a given observation.

    status: 'pending' | 'running' | 'done' | 'error'
    """
    col = _STATUS_COL.get(stage)
    if not col:
        return
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            f"UPDATE observations SET {col} = ? WHERE id = ?",
            (status, obs_id)
        )
        if stage == OrchestratorStage.SEQUENCER and status == "error":
            await db.execute(
                "UPDATE observations SET ended_at = ?, outcome = 'failed' WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), obs_id)
            )
        await db.commit()
    log.debug(f"[DB] {col} = '{status}' for {obs_id}")


async def db_stamp_stage(obs_id: str, stage: OrchestratorStage) -> None:
    """Records the completion timestamp and sets status to 'done' for a stage."""
    done_col   = _DONE_COL.get(stage)
    status_col = _STATUS_COL.get(stage)
    if not done_col:
        return
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            f"UPDATE observations SET {done_col} = ?, {status_col} = 'done' WHERE id = ?",
            (now, obs_id)
        )
        if stage == OrchestratorStage.SEQUENCER:
            # End of the telescope time used by this observation (also set on failure).
            await db.execute(
                "UPDATE observations SET ended_at = ?, outcome = 'success' WHERE id = ?",
                (now, obs_id)
            )
        await db.commit()
    log.info(f"[DB] {done_col} stamped for {obs_id}")


async def db_set_error(obs_id: str, message: str) -> None:
    """Records an error message on an observation."""
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            "UPDATE observations SET error_message = ? WHERE id = ?",
            (message, obs_id)
        )
        await db.commit()
    log.info(f"[DB] Error recorded for {obs_id}")


async def db_get_recent_observations(limit: int | None = None) -> list[dict]:
    """Returns observations from the current astronomical day (local noon to noon), newest first.
    No limit by default: the astronomical-day filter alone already bounds the result set to one night.
    An explicit limit is still accepted (used by tests to isolate their own rows in the shared DB)."""
    since = astronomical_day_start_utc()
    query = """SELECT id, target_name, queried_name, program_name, priority, created_at, error_message,
                      scheduler_done, sequencer_done, reduction_done,
                      sharing_done, archive_done,
                      scheduler_status, sequencer_status, reduction_status,
                      sharing_status, archive_status
               FROM observations
               WHERE created_at >= ?
               ORDER BY created_at DESC"""
    params: list = [since]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
    return [
        {
            "id":            row["id"],
            "target_name":   row["target_name"],
            "queried_name":  row["queried_name"],
            "program_name":  row["program_name"],
            "priority":      row["priority"],
            "created_at":    row["created_at"],
            "error_message": row["error_message"],
            "stages": {
                "scheduler": {
                    "status": row["scheduler_status"] or "pending",
                    "done_at": row["scheduler_done"],
                },
                "sequencer": {
                    "status": row["sequencer_status"] or "pending",
                    "done_at": row["sequencer_done"],
                },
                "reduction": {
                    "status": row["reduction_status"] or "pending",
                    "done_at": row["reduction_done"],
                },
                "sharing": {
                    "status": row["sharing_status"] or "pending",
                    "done_at": row["sharing_done"],
                },
                "archive": {
                    "status": row["archive_status"] or "pending",
                    "done_at": row["archive_done"],
                },
            },
        }
        for row in rows
    ]


# ─────────────────────────────────────────────────────────────
# Pointing errors
# ─────────────────────────────────────────────────────────────

async def db_insert_pointing_error(
    observation_id: str,
    target_name: str,
    mount_ra_deg: float,
    mount_dec_deg: float,
    solved_ra_deg: float,
    solved_dec_deg: float,
    pier_side: str | None = None,
) -> None:
    """Records one plate-solve pointing measurement (mount-reported position
    vs. plate-solved position, both J2000) for trending pointing/polar-alignment
    drift over time. Called only when the caller has decided the measurement
    is worth keeping — see plugins/sequencer_plugins/plate_solve_helper.py::
    solve_wcs()'s record_error argument. target_name is
    redundant with observation_id (joinable via the observations table) but
    kept for human readability, same precedent as observations.target_name.
    pier_side is the side the mount reported at that moment ("east": mount
    east of the pier, tube pointing west; "west": the reverse -- see
    hardware/mount.py::Mount.get_pier_side()), or None if unknown; NULL on
    every row recorded before 2026-09-26."""
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            """INSERT INTO pointing_errors
               (observation_id, target_name, recorded_at, mount_ra_deg, mount_dec_deg, solved_ra_deg, solved_dec_deg, pier_side)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                observation_id,
                target_name,
                datetime.now(timezone.utc).isoformat(),
                mount_ra_deg,
                mount_dec_deg,
                solved_ra_deg,
                solved_dec_deg,
                pier_side,
            )
        )
        await db.commit()
    log.info(f"[DB] Pointing error recorded for observation {observation_id} ({target_name})")


# ─────────────────────────────────────────────────────────────
# Spectrum flux
# ─────────────────────────────────────────────────────────────

# Measured values of one spectrum_flux row, filled from the calculate_flux
# REDUCTION sub-plugin's result (plugins/reduction_plugins/spectrum_flux_helpers.py
# ::SpectrumFlux) -- listed here so this module never imports a plugin.
SPECTRUM_FLUX_MEASUREMENT_COLUMNS: tuple[str, ...] = (
    "method_version",
    "center_x",
    "center_y",
    "angle_deg",
    "trace_method",
    "bands_used",
    "trace_sigma_px",
    "sigma_axes_px",
    "background_adu",
    "background_noise_adu",
    "total_adu",
    "column_max_adu",
    "column_max_x",
    "peak_pixel_adu",
    "peak_pixel_x",
    "peak_pixel_y",
    "saturated",
    "hot_pixels_replaced",
)


async def db_insert_spectrum_flux(
    observation_id: str,
    target_name: str,
    magnitude_v: float | None,
    image_name: str,
    exposure_s: float | None,
    measurement: dict,
) -> None:
    """Records the flux measured on one raw science sub-exposure, for trending
    spectrum intensity over time (transparency, exposure tuning). `measurement`
    must hold every key of SPECTRUM_FLUX_MEASUREMENT_COLUMNS (a KeyError names
    the missing one); extra keys are ignored. target_name and magnitude_v are
    redundant with observations/targets.db but kept for readability, same
    precedent as pointing_errors.target_name."""
    values = [measurement[column] for column in SPECTRUM_FLUX_MEASUREMENT_COLUMNS]
    columns = ("observation_id", "target_name", "magnitude_v", "image_name", "recorded_at", "exposure_s") + (
        SPECTRUM_FLUX_MEASUREMENT_COLUMNS
    )
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            f"INSERT INTO spectrum_flux ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            (
                observation_id,
                target_name,
                magnitude_v,
                image_name,
                datetime.now(timezone.utc).isoformat(),
                exposure_s,
                *values,
            ),
        )
        await db.commit()