"""
Session (cookie) authentication — user accounts, password hashing, server
sessions, FastAPI dependencies for protecting routes.

Uses the same file as database.py (rosace.db), but in its own module:
security-sensitive code (hashing, session validation, access control) stays
isolated and easy to audit at a glance.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite
import bcrypt
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from .config import DATABASES_DIR, DB_PATH, SESSION_TTL_HOURS

log = logging.getLogger("spectro.auth")

SESSION_COOKIE_NAME = "rosace_session"


# ─────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    """Fields provided to create an account."""
    username: str
    password: str
    is_admin: bool = False


class UserOut(BaseModel):
    """User as returned by the API — never the password hash."""
    username: str
    is_admin: bool
    created_at: str


class LoginRequest(BaseModel):
    username: str
    password: str


# ─────────────────────────────────────────────────────────────
# Initialization
# ─────────────────────────────────────────────────────────────

async def auth_db_init() -> None:
    """Creates the authentication tables if they don't already exist — same
    file as database.py (rosace.db). Also creates DATABASES_DIR and sets
    WAL mode itself, rather than relying on db_init() having already done
    so: unlike database.py::db_init(), which always runs first in
    lifespan(), auth_db_init() can also be called on its own (CLI
    --create-admin, on a brand-new installation, so potentially before
    db_init() has ever run against this file)."""
    DATABASES_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin      INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL
            )
        """)
        # "auth_sessions", not "sessions" — database.py already owns a "sessions"
        # table in this same file (one row per observation-night session, a
        # completely different concept from a login session); CREATE TABLE IF
        # NOT EXISTS would otherwise silently keep that unrelated table as-is.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        await db.commit()
    log.info(f"[AUTH] Tables d'authentification initialisées : {DB_PATH}")


# ─────────────────────────────────────────────────────────────
# Passwords
# ─────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


# ─────────────────────────────────────────────────────────────
# Users
# ─────────────────────────────────────────────────────────────

async def user_create(user: UserCreate) -> UserOut:
    """Creates an account. Raises ValueError if the username already exists."""
    created_at = datetime.now(timezone.utc).isoformat()
    password_hash = _hash_password(user.password)
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        try:
            await db.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                (user.username, password_hash, int(user.is_admin), created_at)
            )
            await db.commit()
        except aiosqlite.IntegrityError as e:
            raise ValueError(f"le compte '{user.username}' existe déjà") from e
    log.info(f"[AUTH] Compte créé : {user.username} (admin={user.is_admin})")
    return UserOut(username=user.username, is_admin=user.is_admin, created_at=created_at)


async def user_list() -> list[UserOut]:
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT username, is_admin, created_at FROM users ORDER BY username") as cursor:
            rows = await cursor.fetchall()
    return [UserOut(username=r["username"], is_admin=bool(r["is_admin"]), created_at=r["created_at"]) for r in rows]


async def user_count() -> int:
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0]


async def user_delete(username: str) -> None:
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute("DELETE FROM users WHERE username = ?", (username,))
        await db.commit()
    log.info(f"[AUTH] Compte supprimé : {username}")


async def _user_row_by_username(username: str) -> Optional[aiosqlite.Row]:
    """Internal — includes the hash, unlike UserOut. Used for login verification."""
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, username, password_hash, is_admin, created_at FROM users WHERE username = ?",
            (username,)
        ) as cursor:
            return await cursor.fetchone()


# ─────────────────────────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────────────────────────

async def session_create(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=SESSION_TTL_HOURS)
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            "INSERT INTO auth_sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now.isoformat(), expires_at.isoformat())
        )
        await db.commit()
    return token


async def session_delete(token: str) -> None:
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
        await db.commit()


async def session_get_user(token: str) -> Optional[UserOut]:
    """Returns the user for a valid (non-expired) session token, otherwise None."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT u.username, u.is_admin, u.created_at
               FROM auth_sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token = ? AND s.expires_at > ?""",
            (token, now)
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return UserOut(username=row["username"], is_admin=bool(row["is_admin"]), created_at=row["created_at"])


# ─────────────────────────────────────────────────────────────
# FastAPI dependencies
# ─────────────────────────────────────────────────────────────

async def get_current_user(request: Request) -> UserOut:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = await session_get_user(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Non authentifié")
    return user


async def require_admin(user: UserOut = Depends(get_current_user)) -> UserOut:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Réservé aux administrateurs")
    return user


# ─────────────────────────────────────────────────────────────
# Bootstrapping (CLI --create-admin)
# ─────────────────────────────────────────────────────────────

async def ensure_first_admin(username: str, password: str) -> None:
    """Creates the very first admin account. Refuses if an account already
    exists — past this first bootstrap, account creation goes through the
    API (admin-only), not shell access to the machine."""
    if await user_count() > 0:
        raise RuntimeError("un compte existe déjà — --create-admin ne peut amorcer qu'un premier compte")
    await user_create(UserCreate(username=username, password=password, is_admin=True))
