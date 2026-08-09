"""
SQLite access layer (aiosqlite) — sessions, observations, and per-stage statuses.
"""

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from .config import DATABASES_DIR, DB_PATH
from .models import OrchestratorStage, SessionContext, Observation

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
                archive_status   TEXT NOT NULL DEFAULT 'pending'
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
                solved_dec_deg  REAL NOT NULL
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
    """Inserts a new observation after SCHEDULER completes."""
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            """INSERT INTO observations
               (id, session_id, target_name, queried_name, program_name, priority, created_at, scheduler_done, scheduler_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'done')""",
            (
                obs.id,
                session_id,
                obs.scheduler_data["target"].name,
                obs.scheduler_data.get("queried_name"),
                obs.scheduler_data.get("program"),
                obs.scheduler_data.get("priority"),
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
    since = _astronomical_day_start_utc()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            """SELECT COUNT(*) FROM observations
               WHERE created_at >= ? AND program_name = ? AND target_name = ?""",
            (since, program_name, target_name)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0]


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
        await db.commit()
    log.debug(f"[DB] {col} = '{status}' for {obs_id}")


async def db_stamp_stage(obs_id: str, stage: OrchestratorStage) -> None:
    """Records the completion timestamp and sets status to 'done' for a stage."""
    done_col   = _DONE_COL.get(stage)
    status_col = _STATUS_COL.get(stage)
    if not done_col:
        return
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            f"UPDATE observations SET {done_col} = ?, {status_col} = 'done' WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), obs_id)
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


def _astronomical_day_start_utc(now: datetime | None = None) -> str:
    """
    Start of the current astronomical day (local noon at or before `now`), as a UTC
    timestamp string in the same format as the created_at column, for a direct
    lexicographic comparison. Assumes the server runs in the site's own local
    timezone — there is no explicit site timezone setting in conf/config.yaml.
    """
    local_now = (now or datetime.now(timezone.utc)).astimezone()
    local_noon = local_now.replace(hour=12, minute=0, second=0, microsecond=0)
    if local_now < local_noon:
        local_noon -= timedelta(days=1)
    return local_noon.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


async def db_get_recent_observations(limit: int | None = None) -> list[dict]:
    """Returns observations from the current astronomical day (local noon to noon), newest first.
    No limit by default: the astronomical-day filter alone already bounds the result set to one night.
    An explicit limit is still accepted (used by tests to isolate their own rows in the shared DB)."""
    since = _astronomical_day_start_utc()
    query = """SELECT id, target_name, queried_name, priority, created_at, error_message,
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
) -> None:
    """Records one plate-solve pointing measurement (mount-reported position
    vs. plate-solved position, both J2000) for trending pointing/polar-alignment
    drift over time. Called only when the caller has decided the measurement
    is worth keeping — see plugins/sequencer_plugins/plate_solve_helper.py::
    measure_pointing_error()'s record_error argument. target_name is
    redundant with observation_id (joinable via the observations table) but
    kept for human readability, same precedent as observations.target_name."""
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            """INSERT INTO pointing_errors
               (observation_id, target_name, recorded_at, mount_ra_deg, mount_dec_deg, solved_ra_deg, solved_dec_deg)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                observation_id,
                target_name,
                datetime.now(timezone.utc).isoformat(),
                mount_ra_deg,
                mount_dec_deg,
                solved_ra_deg,
                solved_dec_deg,
            )
        )
        await db.commit()
    log.info(f"[DB] Pointing error recorded for observation {observation_id} ({target_name})")
