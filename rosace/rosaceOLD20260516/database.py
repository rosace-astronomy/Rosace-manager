"""
Couche d'accès à la base SQLite (aiosqlite).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from .config import BASE_DATA_DIR, DB_PATH
from .models import OrchestratorStage, SessionContext, Observation

log = logging.getLogger("spectro.database")


# ─────────────────────────────────────────────────────────────
# Initialisation
# ─────────────────────────────────────────────────────────────

async def db_init() -> None:
    """Crée les tables si elles n'existent pas."""
    BASE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id         TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                closed_at  TEXT,
                status     TEXT NOT NULL DEFAULT 'running'
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
                archive_done     TEXT
            )
        """)
        await db.commit()
    log.info(f"[DB] Base initialisée : {DB_PATH}")


# ─────────────────────────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────────────────────────

async def db_insert_session(ctx: SessionContext) -> None:
    """Insère une nouvelle session."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (id, started_at, status) VALUES (?, ?, 'running')",
            (ctx.session_id, ctx.started_at.isoformat())
        )
        await db.commit()
    log.info(f"[DB] Session insérée : {ctx.session_id}")


async def db_close_session(ctx: SessionContext, status: str = "closed") -> None:
    """Met à jour la session à la clôture."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET closed_at = ?, status = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), status, ctx.session_id)
        )
        await db.commit()
    log.info(f"[DB] Session clôturée : {ctx.session_id} ({status})")


# ─────────────────────────────────────────────────────────────
# Observations
# ─────────────────────────────────────────────────────────────

async def db_insert_observation(obs: Observation, session_id: str) -> None:
    """Insère une nouvelle observation après le SCHEDULER."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO observations
               (id, session_id, target_name, created_at, scheduler_done)
               VALUES (?, ?, ?, ?, ?)""",
            (
                obs.id,
                session_id,
                obs.request.target.name,
                obs.created_at.isoformat(),
                datetime.now(timezone.utc).isoformat(),
            )
        )
        await db.commit()
    log.info(f"[DB] Observation insérée : {obs.id}")


async def db_set_yaml_file(obs_id: str, yaml_file: str) -> None:
    """Enregistre le chemin du fichier YAML de l'observation."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE observations SET yaml_file = ? WHERE id = ?",
            (yaml_file, obs_id)
        )
        await db.commit()
    log.info(f"[DB] yaml_file enregistré pour {obs_id}")


async def db_stamp_stage(obs_id: str, stage: OrchestratorStage) -> None:
    """Enregistre le timestamp de fin d'une étape."""
    col = {
        OrchestratorStage.SEQUENCER: "sequencer_done",
        OrchestratorStage.REDUCTION: "reduction_done",
        OrchestratorStage.SHARING:   "sharing_done",
        OrchestratorStage.ARCHIVE:   "archive_done",
    }.get(stage)
    if not col:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE observations SET {col} = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), obs_id)
        )
        await db.commit()
    log.info(f"[DB] {col} enregistré pour {obs_id}")


async def db_set_error(obs_id: str, message: str) -> None:
    """Enregistre un message d'erreur sur l'observation."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE observations SET error_message = ? WHERE id = ?",
            (message, obs_id)
        )
        await db.commit()
    log.info(f"[DB] Erreur enregistrée pour {obs_id}")
