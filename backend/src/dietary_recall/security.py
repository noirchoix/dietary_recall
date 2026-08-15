"""Separate production credential/session store for the research platform.

Credentials are deliberately not stored in the research database. The store
uses salted PBKDF2-HMAC-SHA256 hashes, opaque server-side sessions, absolute and
idle expiry, login throttling and append-only authentication audit events.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import connect_writable


PBKDF2_ITERATIONS = 600_000
MIN_PASSWORD_CHARS = 12
MAX_PASSWORD_BYTES = 1024
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 15 * 60
LOCK_SECONDS = 15 * 60


AUTH_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS auth_credentials (
    email TEXT PRIMARY KEY,
    user_uid TEXT NOT NULL,
    password_salt_b64 TEXT NOT NULL,
    password_hash_b64 TEXT NOT NULL,
    iterations INTEGER NOT NULL CHECK(iterations >= 600000),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled','password_reset_required')),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    first_failed_at INTEGER,
    locked_until INTEGER,
    password_changed_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    session_digest TEXT PRIMARY KEY,
    email TEXT NOT NULL REFERENCES auth_credentials(email) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    idle_expires_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    user_agent_hash TEXT,
    remote_address_hash TEXT,
    revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS auth_sessions_email_idx ON auth_sessions(email,revoked_at,expires_at);
CREATE TABLE IF NOT EXISTS auth_audit_events (
    auth_event_uid TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    email TEXT,
    outcome TEXT NOT NULL,
    remote_address_hash TEXT,
    user_agent_hash TEXT,
    detail TEXT,
    created_at INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS auth_audit_no_update BEFORE UPDATE ON auth_audit_events BEGIN
  SELECT RAISE(ABORT, 'auth_audit_events is append-only');
END;
CREATE TRIGGER IF NOT EXISTS auth_audit_no_delete BEFORE DELETE ON auth_audit_events BEGIN
  SELECT RAISE(ABORT, 'auth_audit_events is append-only');
END;
PRAGMA user_version = 1;
"""


class AuthError(PermissionError):
    pass


class LoginRateLimited(AuthError):
    pass


@dataclass(frozen=True)
class AuthSession:
    email: str
    user_uid: str
    csrf_token: str
    expires_at: int
    idle_expires_at: int
    session_token: str | None = None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _context_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _password_bytes(password: str) -> bytes:
    if len(password) < MIN_PASSWORD_CHARS:
        raise ValueError(f"Password must contain at least {MIN_PASSWORD_CHARS} characters")
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must not exceed {MAX_PASSWORD_BYTES} UTF-8 bytes")
    return encoded


def _derive(password: bytes, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password, salt, iterations, dklen=32)


class CredentialStore:
    """SQLite-backed credentials and revocable opaque sessions."""

    def __init__(self, path: str | Path, *, initialize: bool = False):
        self.path = Path(path).resolve()
        if initialize:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as con:
                con.executescript(AUTH_SCHEMA)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        with self.connect() as con:
            if int(con.execute("PRAGMA user_version").fetchone()[0]) != 1:
                raise ValueError("Unsupported credential-store schema")
        # A fixed-cost dummy record prevents a missing email from taking a fast path.
        self._dummy_salt = hashlib.sha256(b"dietary-recall-auth-dummy-salt").digest()[:16]
        self._dummy_hash = _derive(b"not-a-user-password", self._dummy_salt, PBKDF2_ITERATIONS)

    def connect(self) -> sqlite3.Connection:
        con = connect_writable(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        return con

    @classmethod
    def initialize(cls, path: str | Path) -> "CredentialStore":
        target = Path(path).resolve()
        if target.exists():
            raise FileExistsError(target)
        return cls(target, initialize=True)

    @staticmethod
    def _audit(
        con: sqlite3.Connection,
        action: str,
        email: str | None,
        outcome: str,
        remote_address: str | None,
        user_agent: str | None,
        detail: str | None = None,
        now: int | None = None,
    ) -> None:
        con.execute(
            "INSERT INTO auth_audit_events(auth_event_uid,action,email,outcome,remote_address_hash,user_agent_hash,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (f"auth_{uuid.uuid4().hex}", action, email, outcome, _context_hash(remote_address), _context_hash(user_agent), detail, now or int(time.time())),
        )

    def set_password(self, email: str, user_uid: str, password: str, *, require_reset: bool = False) -> dict[str, Any]:
        normalized = email.strip().casefold()
        if "@" not in normalized or not user_uid:
            raise ValueError("A valid email and research user UID are required")
        password_bytes = _password_bytes(password)
        salt = secrets.token_bytes(16)
        derived = _derive(password_bytes, salt, PBKDF2_ITERATIONS)
        now = int(time.time())
        status = "password_reset_required" if require_reset else "active"
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "INSERT INTO auth_credentials(email,user_uid,password_salt_b64,password_hash_b64,iterations,status,password_changed_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(email) DO UPDATE SET user_uid=excluded.user_uid,password_salt_b64=excluded.password_salt_b64,password_hash_b64=excluded.password_hash_b64,iterations=excluded.iterations,status=excluded.status,failed_attempts=0,first_failed_at=NULL,locked_until=NULL,password_changed_at=excluded.password_changed_at,updated_at=excluded.updated_at",
                (normalized, user_uid, _b64(salt), _b64(derived), PBKDF2_ITERATIONS, status, now, now, now),
            )
            con.execute("UPDATE auth_sessions SET revoked_at=? WHERE email=? AND revoked_at IS NULL", (now, normalized))
            self._audit(con, "set_password", normalized, "success", None, None, "all earlier sessions revoked", now)
            con.commit()
        return {"email": normalized, "user_uid": user_uid, "status": status, "iterations": PBKDF2_ITERATIONS, "sessions_revoked": True}

    def disable(self, email: str) -> None:
        normalized = email.strip().casefold()
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("UPDATE auth_credentials SET status='disabled',updated_at=? WHERE email=?", (now, normalized)).rowcount != 1:
                raise KeyError(normalized)
            con.execute("UPDATE auth_sessions SET revoked_at=? WHERE email=? AND revoked_at IS NULL", (now, normalized))
            self._audit(con, "disable_credential", normalized, "success", None, None, None, now)
            con.commit()

    def login(
        self,
        email: str,
        password: str,
        *,
        remote_address: str | None = None,
        user_agent: str | None = None,
        absolute_seconds: int = 8 * 60 * 60,
        idle_seconds: int = 30 * 60,
    ) -> AuthSession:
        normalized = email.strip().casefold()
        candidate = password.encode("utf-8")[:MAX_PASSWORD_BYTES + 1]
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM auth_credentials WHERE email=?", (normalized,)).fetchone()
            if row is None:
                hmac.compare_digest(_derive(candidate, self._dummy_salt, PBKDF2_ITERATIONS), self._dummy_hash)
                self._audit(con, "login", normalized or None, "denied", remote_address, user_agent, "invalid credentials", now)
                con.commit()
                raise AuthError("Invalid email or password")
            if row["locked_until"] and int(row["locked_until"]) > now:
                self._audit(con, "login", normalized, "rate_limited", remote_address, user_agent, "credential temporarily locked", now)
                con.commit()
                raise LoginRateLimited("Too many failed attempts; try again later")
            expected = _unb64(row["password_hash_b64"])
            actual = _derive(candidate, _unb64(row["password_salt_b64"]), int(row["iterations"]))
            valid = hmac.compare_digest(expected, actual) and row["status"] == "active"
            if not valid:
                first = int(row["first_failed_at"] or now)
                count = int(row["failed_attempts"] or 0)
                if first < now - FAILURE_WINDOW_SECONDS:
                    first, count = now, 0
                count += 1
                locked_until = now + LOCK_SECONDS if count >= MAX_FAILURES else None
                con.execute("UPDATE auth_credentials SET failed_attempts=?,first_failed_at=?,locked_until=?,updated_at=? WHERE email=?", (count, first, locked_until, now, normalized))
                self._audit(con, "login", normalized, "denied", remote_address, user_agent, "invalid credentials", now)
                con.commit()
                raise AuthError("Invalid email or password")
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            expires = now + max(300, int(absolute_seconds))
            idle_expires = min(expires, now + max(60, int(idle_seconds)))
            con.execute("UPDATE auth_credentials SET failed_attempts=0,first_failed_at=NULL,locked_until=NULL,updated_at=? WHERE email=?", (now, normalized))
            con.execute("DELETE FROM auth_sessions WHERE expires_at<?", (now - 24 * 60 * 60,))
            con.execute(
                "INSERT INTO auth_sessions(session_digest,email,csrf_token,created_at,expires_at,idle_expires_at,last_seen_at,user_agent_hash,remote_address_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (_token_digest(token), normalized, csrf, now, expires, idle_expires, now, _context_hash(user_agent), _context_hash(remote_address)),
            )
            self._audit(con, "login", normalized, "success", remote_address, user_agent, None, now)
            con.commit()
            return AuthSession(normalized, row["user_uid"], csrf, expires, idle_expires, token)

    def authenticate(
        self,
        token: str | None,
        *,
        user_agent: str | None = None,
        idle_seconds: int = 30 * 60,
    ) -> AuthSession:
        if not token:
            raise AuthError("Authentication required")
        now = int(time.time())
        digest = _token_digest(token)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT s.*,c.user_uid,c.status credential_status FROM auth_sessions s JOIN auth_credentials c USING(email) WHERE s.session_digest=?",
                (digest,),
            ).fetchone()
            valid = bool(
                row
                and row["revoked_at"] is None
                and row["credential_status"] == "active"
                and int(row["expires_at"]) > now
                and int(row["idle_expires_at"]) > now
            )
            if valid and row["user_agent_hash"] and not hmac.compare_digest(row["user_agent_hash"], _context_hash(user_agent) or ""):
                valid = False
            if not valid:
                if row and row["revoked_at"] is None:
                    con.execute("UPDATE auth_sessions SET revoked_at=? WHERE session_digest=?", (now, digest))
                con.commit()
                raise AuthError("Session is invalid or expired")
            idle_expires = min(int(row["expires_at"]), now + max(60, int(idle_seconds)))
            con.execute("UPDATE auth_sessions SET last_seen_at=?,idle_expires_at=? WHERE session_digest=?", (now, idle_expires, digest))
            con.commit()
            return AuthSession(row["email"], row["user_uid"], row["csrf_token"], int(row["expires_at"]), idle_expires)

    def verify_csrf(self, session: AuthSession, supplied: str | None) -> None:
        if not supplied or not hmac.compare_digest(session.csrf_token, supplied):
            raise AuthError("CSRF validation failed")

    def logout(self, token: str | None, *, remote_address: str | None = None, user_agent: str | None = None) -> None:
        if not token:
            return
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT email FROM auth_sessions WHERE session_digest=?", (_token_digest(token),)).fetchone()
            con.execute("UPDATE auth_sessions SET revoked_at=? WHERE session_digest=? AND revoked_at IS NULL", (now, _token_digest(token)))
            self._audit(con, "logout", row["email"] if row else None, "success", remote_address, user_agent, None, now)
            con.commit()

    def revoke_sessions(self, email: str) -> int:
        normalized = email.strip().casefold()
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            count = con.execute("UPDATE auth_sessions SET revoked_at=? WHERE email=? AND revoked_at IS NULL", (now, normalized)).rowcount
            self._audit(con, "revoke_sessions", normalized, "success", None, None, f"revoked={count}", now)
            con.commit()
            return int(count)

    def status(self) -> dict[str, Any]:
        now = int(time.time())
        with self.connect() as con:
            return {
                "credential_store": str(self.path),
                "active_credentials": con.execute("SELECT COUNT(*) FROM auth_credentials WHERE status='active'").fetchone()[0],
                "active_sessions": con.execute("SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL AND expires_at>? AND idle_expires_at>?", (now, now)).fetchone()[0],
                "password_algorithm": "pbkdf2_hmac_sha256",
                "password_iterations": PBKDF2_ITERATIONS,
                "plaintext_passwords_stored": False,
            }
