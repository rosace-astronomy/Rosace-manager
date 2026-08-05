"""
Authentification par session (cookie) — comptes utilisateurs, hachage des
mots de passe, sessions serveur, dépendances FastAPI pour protéger les routes.

Utilise le même fichier que database.py (rosace.db), mais dans son propre
module : le code sensible à la sécurité (hachage, validation de session,
contrôle d'accès) reste isolé et facile à auditer d'un seul coup d'œil.
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
# Modèles
# ─────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    """Champs fournis pour créer un compte."""
    username: str
    password: str
    is_admin: bool = False


class UserOut(BaseModel):
    """Utilisateur tel que retourné par l'API — jamais le hash du mot de passe."""
    username: str
    is_admin: bool
    created_at: str


class LoginRequest(BaseModel):
    username: str
    password: str


# ─────────────────────────────────────────────────────────────
# Initialisation
# ─────────────────────────────────────────────────────────────

async def auth_db_init() -> None:
    """Crée les tables d'authentification si elles n'existent pas — même
    fichier que database.py (rosace.db). Crée aussi DATABASES_DIR et
    positionne le mode WAL lui-même plutôt que de compter sur db_init() pour
    l'avoir déjà fait : contrairement à database.py::db_init(), qui tourne
    toujours en premier dans lifespan(), auth_db_init() peut aussi être
    appelée seule (CLI --create-admin, sur une installation flambant neuve,
    donc potentiellement avant que db_init() n'ait jamais tourné sur ce fichier)."""
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
# Mots de passe
# ─────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


# ─────────────────────────────────────────────────────────────
# Utilisateurs
# ─────────────────────────────────────────────────────────────

async def user_create(user: UserCreate) -> UserOut:
    """Crée un compte. Lève ValueError si le username existe déjà."""
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
    """Interne — inclut le hash, contrairement à UserOut. Utilisé pour la vérification du login."""
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
    """Retourne l'utilisateur pour un token de session valide (non expiré), sinon None."""
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
# Dépendances FastAPI
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
# Amorçage (CLI --create-admin)
# ─────────────────────────────────────────────────────────────

async def ensure_first_admin(username: str, password: str) -> None:
    """Crée le tout premier compte admin. Refuse si un compte existe déjà —
    au-delà de ce premier amorçage, la création de comptes passe par l'API
    (réservée aux admins), pas par un accès shell à la machine."""
    if await user_count() > 0:
        raise RuntimeError("un compte existe déjà — --create-admin ne peut amorcer qu'un premier compte")
    await user_create(UserCreate(username=username, password=password, is_admin=True))
