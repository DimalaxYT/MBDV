"""Acces SQLite : schema, connexions par requete, comptes utilisateurs."""
import hashlib
import json
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
    created_at    TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'associe'   -- 'admin' (dirigeant) | 'associe'
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

CREATE TABLE IF NOT EXISTS benefices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    siret         TEXT NOT NULL DEFAULT '',   -- facultatif : SIRET (14) ou SIREN (9)
    siren         TEXT,                       -- 9 premiers chiffres, pour le rattachement
    libelle       TEXT NOT NULL DEFAULT '',   -- nom de l'entreprise si elle est connue
    montant_cents INTEGER NOT NULL,
    encaisse_le   TEXT NOT NULL,              -- AAAA-MM-JJTHH:MM (heure de Paris)
    encaisse_par  TEXT NOT NULL,
    created_at    TEXT NOT NULL
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
    status_updated_at TEXT,
    montant_cents     INTEGER NOT NULL DEFAULT 0,  -- benefice encaisse (en centimes)
    signe_le          TEXT,                         -- date du paiement (AAAA-MM-JJ)
    -- Suivi d'equipe : qui travaille l'entreprise et ou en est l'appel.
    pris_par          TEXT NOT NULL DEFAULT '',     -- identifiant du collegue qui la travaille
    pris_le           TEXT,                         -- depuis quand (ISO)
    appele_le         TEXT,                         -- dernier appel (AAAA-MM-JJTHH:MM)
    appele_par        TEXT,                         -- qui a passe le dernier appel
    appels            INTEGER NOT NULL DEFAULT 0,   -- nombre d'appels passes
    relance_le        TEXT,                         -- prochain appel a passer (AAAA-MM-JJ)
    -- Livrables : ce qu'on a produit pour cette entreprise.
    url_vitrine       TEXT NOT NULL DEFAULT '',     -- URL de la vitrine (lien a ouvrir)
    dossier_site      TEXT NOT NULL DEFAULT '',     -- dossier du site (chemin ou lien)
    zip_lien          TEXT NOT NULL DEFAULT '',     -- archive .zip deposee ailleurs (lien)
    zip_nom           TEXT NOT NULL DEFAULT '',     -- nom du .zip televerse (fichier local)
    livrables_maj_le  TEXT                          -- derniere mise a jour des livrables (ISO)
);
"""

# Mots de passe livres dans le README : on alerte tant qu'ils n'ont pas ete changes.
DEFAULT_PASSWORDS = {"MBDV-admin-2026", "MBDV-associe-2026"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path() -> str:
    return os.path.join(current_app.config["DATA_DIR"], "mbdv.sqlite3")


def _connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # Plusieurs threads waitress ecrivent dans le meme fichier : on attend le
    # verrou plutot que de renvoyer "database is locked".
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = _connect()
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _migration_benefices(conn) -> None:
    """Table `benefices` : source unique des encaissements.

    Les montants deja enregistres sur les entreprises suivies y sont reportes une
    seule fois, pour ne rien perdre. Le portefeuille ne fait plus que les afficher ;
    c'est le panel staff qui les saisit.
    """
    conn.executescript("""CREATE TABLE IF NOT EXISTS benefices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    siret         TEXT NOT NULL DEFAULT '',   -- facultatif : SIRET (14) ou SIREN (9)
    siren         TEXT,                       -- 9 premiers chiffres, pour le rattachement
    libelle       TEXT NOT NULL DEFAULT '',   -- nom de l'entreprise si elle est connue
    montant_cents INTEGER NOT NULL,
    encaisse_le   TEXT NOT NULL,              -- AAAA-MM-JJTHH:MM (heure de Paris)
    encaisse_par  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

""")
    if conn.execute("SELECT 1 FROM benefices LIMIT 1").fetchone():
        return
    lignes = conn.execute(
        "SELECT siren, snapshot, montant_cents, signe_le, added_by, added_at"
        " FROM tracked WHERE montant_cents > 0").fetchall()
    for ligne in lignes:
        try:
            nom = (json.loads(ligne["snapshot"] or "{}") or {}).get("nom") or ""
        except (ValueError, TypeError):
            nom = ""
        quand = (ligne["signe_le"] or "")[:16] or (ligne["added_at"] or now_iso())[:16]
        conn.execute(
            "INSERT INTO benefices (siret, siren, libelle, montant_cents, encaisse_le,"
            " encaisse_par, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("", ligne["siren"], nom, ligne["montant_cents"], quand,
             ligne["added_by"] or "?", now_iso()))


def _migration_tracked(conn) -> None:
    """Ajoute les colonnes du benefice aux bases creees avant leur introduction."""
    colonnes = {ligne["name"] for ligne in conn.execute("PRAGMA table_info(tracked)")}
    if "montant_cents" not in colonnes:
        conn.execute("ALTER TABLE tracked ADD COLUMN montant_cents INTEGER NOT NULL DEFAULT 0")
    if "signe_le" not in colonnes:
        conn.execute("ALTER TABLE tracked ADD COLUMN signe_le TEXT")


def _migration_suivi(conn) -> None:
    """Ajoute les colonnes du suivi d'equipe aux bases creees avant leur introduction.

    Une base existante garde donc ses entreprises suivies : elles apparaissent
    simplement comme « personne ne s'en occupe », sans appel enregistre.
    """
    colonnes = {ligne["name"] for ligne in conn.execute("PRAGMA table_info(tracked)")}
    a_ajouter = (
        ("pris_par", "TEXT NOT NULL DEFAULT ''"),
        ("pris_le", "TEXT"),
        ("appele_le", "TEXT"),
        ("appele_par", "TEXT"),
        ("appels", "INTEGER NOT NULL DEFAULT 0"),
        ("relance_le", "TEXT"),
    )
    for nom, type_sql in a_ajouter:
        if nom not in colonnes:
            conn.execute(f"ALTER TABLE tracked ADD COLUMN {nom} {type_sql}")


def _migration_livrables(conn) -> None:
    """Ajoute les colonnes des livrables aux bases creees avant leur introduction."""
    colonnes = {ligne["name"] for ligne in conn.execute("PRAGMA table_info(tracked)")}
    for nom, type_sql in (("url_vitrine", "TEXT NOT NULL DEFAULT ''"),
                          ("dossier_site", "TEXT NOT NULL DEFAULT ''"),
                          ("zip_lien", "TEXT NOT NULL DEFAULT ''"),
                          ("zip_nom", "TEXT NOT NULL DEFAULT ''"),
                          ("livrables_maj_le", "TEXT")):
        if nom not in colonnes:
            conn.execute(f"ALTER TABLE tracked ADD COLUMN {nom} {type_sql}")


def _migration_role(conn) -> None:
    """Ajoute la colonne `role` aux bases creees avant son introduction.

    Le compte le plus ancien (celui du dirigeant) devient l'administrateur, afin
    qu'une base existante continue de fonctionner sans intervention.
    """
    colonnes = {ligne["name"] for ligne in conn.execute("PRAGMA table_info(users)")}
    if "role" not in colonnes:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'associe'")
    aucun_admin = not conn.execute(
        "SELECT 1 FROM users WHERE role = 'admin'").fetchone()
    if aucun_admin and conn.execute("SELECT 1 FROM users").fetchone():
        conn.execute("UPDATE users SET role = 'admin'"
                     " WHERE id = (SELECT MIN(id) FROM users)")


def init_app(app) -> None:
    base = os.path.join(app.config["DATA_DIR"], "mbdv.sqlite3")
    conn = _connect(base)
    try:
        conn.executescript(SCHEMA)
        _migration_role(conn)
        _migration_tracked(conn)
        _migration_suivi(conn)
        _migration_livrables(conn)
        _migration_benefices(conn)
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
            "admin",
            "MBDV_ADMIN_PASSWORD",
        ),
        (
            os.environ.get("MBDV_ASSOCIE_USER", "associe"),
            os.environ.get("MBDV_ASSOCIE_PASSWORD", "MBDV-associe-2026"),
            "Associe",
            "associe",
            "MBDV_ASSOCIE_PASSWORD",
        ),
    ]
    conn = _connect(os.path.join(app.config["DATA_DIR"], "mbdv.sqlite3"))
    try:
        for username, password, display, role, variable in defaults:
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                continue
            conn.execute(
                "INSERT INTO users (username, display_name, password_hash, created_at, role)"
                " VALUES (?, ?, ?, ?, ?)",
                (username, display, hash_password(password), now_iso(), role),
            )
            if password in DEFAULT_PASSWORDS:
                app.logger.warning(
                    "Compte '%s' cree avec le mot de passe par defaut : changez-le "
                    "depuis le menu Mot de passe (ou definissez %s avant le premier "
                    "demarrage).", username, variable,
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


def run_all(statements) -> None:
    """Execute plusieurs ecritures dans une seule transaction (tout ou rien)."""
    db = get_db()
    try:
        for sql, params in statements:
            db.execute(sql, params)
        db.commit()
    except Exception:
        db.rollback()
        raise
