"""Acces SQLite : schema, connexions par requete, comptes utilisateurs."""
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timezone

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    display_name  TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_cache (
    siren      TEXT PRIMARY KEY,
    status     TEXT NOT NULL,          -- 'site' | 'aucun' | 'inconnu'
    domain     TEXT,
    source     TEXT NOT NULL,          -- 'dns' | 'manuel' | 'manuel_sans'
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS overrides (
    siren TEXT PRIMARY KEY,
    value TEXT NOT NULL,               -- 'sans' : l'associé confirme l'absence de site
    by    TEXT NOT NULL,
    at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hides (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    siren       TEXT NOT NULL,
    nom         TEXT NOT NULL,
    raison      TEXT NOT NULL,
    details     TEXT NOT NULL DEFAULT '',
    snapshot    TEXT NOT NULL DEFAULT '',
    hidden_by   TEXT NOT NULL,
    hidden_at   TEXT NOT NULL,
    restored_at TEXT,
    restored_by TEXT
);

CREATE TABLE IF NOT EXISTS tracked (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    siren             TEXT UNIQUE NOT NULL,
    snapshot          TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'a_contacter',
    note              TEXT NOT NULL DEFAULT '',
    added_by          TEXT NOT NULL,
    added_at          TEXT NOT NULL,
    status_updated_by TEXT,
    status_updated_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path() -> str:
    return os.path.join(current_app.config["DATA_DIR"], "mbdv.sqlite3")


def _connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = _connect()
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app) -> None:
    base = os.path.join(app.config["DATA_DIR"], "mbdv.sqlite3")
    conn = _connect(base)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    _seed_users(app)
    app.teardown_appcontext(close_db)


# --------------------------------------------------------------------------
# Mots de passe (scrypt, dependance stdlib uniquement)
# --------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt.encode("ascii"), n=16384, r=8, p=1, dklen=32
    ).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    candidate = hashlib.scrypt(
        password.encode("utf-8"), salt=salt.encode("ascii"), n=16384, r=8, p=1, dklen=32
    ).hex()
    return secrets.compare_digest(candidate, digest)


def _seed_users(app) -> None:
    """Cree les deux comptes associes au premier demarrage."""
    defaults = [
        (
            os.environ.get("MBDV_ADMIN_USER", "admin"),
            os.environ.get("MBDV_ADMIN_PASSWORD", "MBDV-admin-2026"),
            "Dirigeant",
        ),
        (
            os.environ.get("MBDV_ASSOCIE_USER", "associe"),
            os.environ.get("MBDV_ASSOCIE_PASSWORD", "MBDV-associe-2026"),
            "Associe",
        ),
    ]
    conn = _connect(os.path.join(app.config["DATA_DIR"], "mbdv.sqlite3"))
    try:
        for username, password, display in defaults:
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                continue
            conn.execute(
                "INSERT INTO users (username, display_name, password_hash, created_at)"
                " VALUES (?, ?, ?, ?)",
                (username, display, hash_password(password), now_iso()),
            )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Petits helpers de requetage
# --------------------------------------------------------------------------

def query(sql: str, params=()) -> list:
    return get_db().execute(sql, params).fetchall()


def one(sql: str, params=()):
    return get_db().execute(sql, params).fetchone()


def execute(sql: str, params=()) -> None:
    db = get_db()
    db.execute(sql, params)
    db.commit()
