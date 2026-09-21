"""Authentification : sessions, CSRF, limitation des tentatives, filtres de rendu."""
import time
from datetime import datetime, timezone
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    abort, current_app, redirect, request, session, url_for,
)
from markupsafe import Markup, escape

from . import db

PARIS_TZ = ZoneInfo("Europe/Paris")

# 12 tentatives par identifiant toutes les 5 minutes
_RATE = {}
_RATE_LIMIT = 12
_RATE_WINDOW = 300


def _client_ip() -> str:
    return (request.headers.get("X-Forwarded-For", "").split(",")[0]
            or request.remote_addr or "?").strip()


def login_attempts_exhausted(username: str) -> bool:
    key = f"{_client_ip()}:{username}"
    count, first = _RATE.get(key, (0, time.time()))
    if time.time() - first > _RATE_WINDOW:
        _RATE.pop(key, None)
        return False
    return count >= _RATE_LIMIT


def register_failed_attempt(username: str) -> None:
    key = f"{_client_ip()}:{username}"
    count, first = _RATE.get(key, (0, time.time()))
    if time.time() - first > _RATE_WINDOW:
        _RATE.clear()
        count, first = 0, time.time()
    _RATE[key] = (count + 1, first)


def login(user_row) -> None:
    session.clear()
    session["uid"] = user_row["id"]
    session["username"] = user_row["username"]
    session["display_name"] = user_row["display_name"]
    session["csrf"] = session.get("csrf") or session.get("_csrf_token") or None
    session.permanent = True


def logout() -> None:
    session.clear()


def current_user():
    if "username" not in session:
        return None
    return {
        "id": session["uid"],
        "username": session["username"],
        "display_name": session.get("display_name") or session["username"],
    }


def csrf_token() -> str:
    token = session.get("csrf")
    if not token:
        import secrets as _secrets
        token = session["csrf"] = _secrets.token_hex(16)
    return token


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            if request.path.startswith("/api/") or request.path.startswith("/entreprise/"):
                abort(401)
            return redirect(url_for("views.connexion", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def init_app(app) -> None:
    @app.before_request
    def _csrf_guard():
        if request.method != "POST":
            return None
        sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf", "")
        good = session.get("csrf", "")
        import secrets as _secrets
        if not good or not sent or not _secrets.compare_digest(sent, good):
            abort(400, "Session expiree ou requete non autorisee. Rechargez la page.")
        return None

    @app.context_processor
    def _inject():
        return {
            "current_user": current_user(),
            "csrf_token": csrf_token,
        }

    _register_filters(app)


def _fmt_dt(value: str) -> str:
    """'2026-01-05T14:23:10Z' -> '05/01/2026 a 15:23' (heure de Paris)."""
    if not value:
        return "-"
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        local = dt.astimezone(PARIS_TZ)
        return local.strftime("%d/%m/%Y à %H:%M")
    except (ValueError, TypeError):
        return str(value)


def _fmt_date(value: str) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.strptime(str(value)[:10], "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return str(value)


def _fmt_eur(value) -> str:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "-"
    neg = n < 0
    s = f"{abs(n):,}".replace(",", " ")
    return f"{'-' if neg else ''}{s} \u20ac"


def _register_filters(app) -> None:
    app.add_template_filter(_fmt_dt, "dt")
    app.add_template_filter(_fmt_date, "frdate")
    app.add_template_filter(_fmt_eur, "eur")

    @app.template_filter("mono")
    def _mono(value: str) -> Markup:
        return Markup(f'<code class="mono">{escape(value or "")}</code>')
