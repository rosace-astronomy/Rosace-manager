"""
SQLite access layer (aiosqlite) — sessions, observations, and per-stage statuses.
"""

import logging
from datetime import datetime, timezone

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
    async with aiosqlite.connect(DB_PATH) as db:
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
        await db.commit()
    log.info(f"[DB] Database initialised: {DB_PATH}")


# ─────────────────────────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────────────────────────

async def db_insert_session(ctx: SessionContext) -> None:
    """Inserts a new session."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (id, started_at, status, session_dir) VALUES (?, ?, 'running', ?)",
            (ctx.session_id, ctx.started_at.isoformat(), ctx.session_dir)
        )
        await db.commit()
    log.info(f"[DB] Session inserted: {ctx.session_id}")


async def db_close_session(ctx: SessionContext, status: str = "closed") -> None:
    """Updates the session record at shutdown."""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO observations
               (id, session_id, target_name, created_at, scheduler_done, scheduler_status)
               VALUES (?, ?, ?, ?, ?, 'done')""",
            (
                obs.id,
                session_id,
                obs.scheduler_data["target"].name,
                obs.created_at.isoformat(),
                datetime.now(timezone.utc).isoformat(),
            )
        )
        await db.commit()
    log.info(f"[DB] Observation inserted: {obs.id}")


async def db_set_yaml_file(obs_id: str, yaml_file: str) -> None:
    """Records the YAML file path for an observation."""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE observations SET {done_col} = ?, {status_col} = 'done' WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), obs_id)
        )
        await db.commit()
    log.info(f"[DB] {done_col} stamped for {obs_id}")


async def db_set_error(obs_id: str, message: str) -> None:
    """Records an error message on an observation."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE observations SET error_message = ? WHERE id = ?",
            (message, obs_id)
        )
        await db.commit()
    log.info(f"[DB] Error recorded for {obs_id}")


async def db_get_recent_observations(limit: int = 20) -> list[dict]:
    """Returns the most recent observations with per-stage statuses and timestamps."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, target_name, created_at, error_message,
                      scheduler_done, sequencer_done, reduction_done,
                      sharing_done, archive_done,
                      scheduler_status, sequencer_status, reduction_status,
                      sharing_status, archive_status
               FROM observations
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
    return [
        {
            "id":            row["id"],
            "target_name":   row["target_name"],
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
