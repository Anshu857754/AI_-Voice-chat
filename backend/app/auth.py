"""Minimal account system: signup / login with hashed passwords + signed sessions.

* Passwords are hashed with scrypt (stdlib) and a per-user random salt; the
  plain password is never stored or logged.
* Users live in a small SQLite file (``AUTH_DB_PATH``, default ``./data/users.db``).
* A successful login returns a signed JWT (HS256). The signing key is
  ``AUTH_SECRET``; when unset it is derived from ``LIVEKIT_API_SECRET`` so no
  extra variable is required to get started (set your own for production).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import jwt

from app.config.settings import Settings

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8


class AuthError(Exception):
    """Raised with a user-safe message."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self._path = Path(settings.auth_db_path)
        self._ttl = settings.auth_token_ttl_hours * 3600
        secret = settings.auth_secret or hmac.new(
            settings.livekit_api_secret.encode(), b"roxstar-auth", hashlib.sha256
        ).hexdigest()
        self._secret = secret or secrets.token_hex(32)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS users ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,"
                "name TEXT NOT NULL, salt BLOB NOT NULL, pw BLOB NOT NULL, created_at REAL NOT NULL)"
            )

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    # ---- accounts ---------------------------------------------------------
    def signup(self, *, name: str, email: str, password: str) -> User:
        name, email = name.strip(), email.strip().lower()
        if not name or len(name) > 40:
            raise AuthError("Naam 1 se 40 characters ka hona chahiye.")
        if not _EMAIL.match(email):
            raise AuthError("Sahi email daalo.")
        if len(password) < MIN_PASSWORD:
            raise AuthError(f"Password kam se kam {MIN_PASSWORD} characters ka rakho.")
        salt = secrets.token_bytes(16)
        try:
            with closing(self._db()) as db, db:
                cur = db.execute(
                    "INSERT INTO users (email, name, salt, pw, created_at) VALUES (?,?,?,?,?)",
                    (email, name, salt, _hash(password, salt), time.time()),
                )
        except sqlite3.IntegrityError as exc:
            raise AuthError("Is email se account pehle se hai. Login karo.", 409) from exc
        return User(id=int(cur.lastrowid), email=email, name=name)

    def login(self, *, email: str, password: str) -> User:
        with closing(self._db()) as db:
            row = db.execute(
                "SELECT id, email, name, salt, pw FROM users WHERE email = ?",
                (email.strip().lower(),),
            ).fetchone()
        # Same message for unknown email and wrong password (no user probing).
        if row is None or not hmac.compare_digest(_hash(password, row[3]), row[4]):
            raise AuthError("Email ya password galat hai.", 401)
        return User(id=row[0], email=row[1], name=row[2])

    def get(self, user_id: int) -> User | None:
        with closing(self._db()) as db:
            row = db.execute("SELECT id, email, name FROM users WHERE id = ?", (user_id,)).fetchone()
        return User(*row) if row else None

    # ---- sessions ---------------------------------------------------------
    def issue_token(self, user: User) -> str:
        now = int(time.time())
        return jwt.encode(
            {"sub": str(user.id), "iat": now, "exp": now + self._ttl}, self._secret, algorithm="HS256"
        )

    def user_from_token(self, token: str) -> User:
        try:
            claims = jwt.decode(token, self._secret, algorithms=["HS256"])
            user = self.get(int(claims["sub"]))
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            raise AuthError("Session expire ho gaya. Dobara login karo.", 401) from exc
        if user is None:
            raise AuthError("Account nahi mila. Dobara login karo.", 401)
        return user
