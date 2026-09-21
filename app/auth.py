"""Authentification : sessions, CSRF, limitation des tentatives, filtres de rendu."""
import secrets
import time
from datetime import datetime, timezone
from functools import wraps
from zoneinfo import ZoneInfo

from flask import abort, jsonify, redirect, render_template, request, session, url_for

PARIS_TZ = ZoneInfo("Europe/Paris")

# 12 tentatives par identifiant et 40 tentatives par adresse IP, toutes les 5 minutes.
# Les compteurs sont en memoire : ils sont remis a zero au redemarrage du service.
_RATE = {}
_RATE_LIMIT_USER = 12
_RATE_LIMIT_IP = 40
_RATE_WINDOW = 300
_RATE_MAX_KEYS = 5000


def _client_ip() -> str:
    """Adresse reellement vue par le serveur.

    On ne lit jamais X-Forwarded-For directement : cet en-tete est fourni par le
    client et suffirait a contourner la limitation. Quand l'application est
    derriere un proxy de confiance, c'est ProxyFix (voir MBDV_TRUST_PROXY) qui
    reecrit request.remote_addr.
    """
    return request.remote_addr or "?"


def _compteur(cle: str, maintenant: float) -> tuple[int, float]:
    """Compteur de la fenetre courante ; remis a zero si la fenetre est passee."""
    count, first = _RATE.get(cle, (0, maintenant))
    if maintenant - first > _RATE_WINDOW:
        return 0, maintenant
    return count, first


def login_attempts_exhausted(username: str) -> bool:
    maintenant = time.time()
    ip = _client_ip()
    par_identifiant, _ = _compteur(f"u:{ip}:{username}", maintenant)
    par_ip, _ = _compteur(f"ip:{ip}", maintenant)
    return par_identifiant >= _RATE_LIMIT_USER or par_ip >= _RATE_LIMIT_IP


def register_failed_attempt(username: str) -> None:
    maintenant = time.time()
    ip = _client_ip()
    for cle, plafond in (
        (f"u:{ip}:{username}", _RATE_LIMIT_USER),
        (f"ip:{ip}", _RATE_LIMIT_IP),
    ):
        count, first = _compteur(cle, maintenant)
        _RATE[cle] = (min(count + 1, plafond), first)
    if len(_RATE) > _RATE_MAX_KEYS:
        # Garde-fou memoire : on ne purge que les fenetres expirees, jamais tout.
        for cle in [k for k, (_, first) in _RATE.items()
                    if maintenant - first > _RATE_WINDOW]:
            _RATE.pop(cle, None)


def login(user_row) -> None:
    """Ouvre la session. session.clear() invalide l'ancien jeton CSRF : le
    nouveau est genere a la premiere page rendue (csrf_token)."""
    session.clear()
    session["uid"] = user_row["id"]
    session["username"] = user_row["username"]
    session["display_name"] = user_row["display_name"]
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
        token = session["csrf"] = secrets.token_hex(16)
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
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf", "")
        good = session.get("csrf", "")
        if good and sent and secrets.compare_digest(sent, good):
            return None
        # Diagnostic : c'est ici qu'on voit un apercu heberge qui perd son cookie.
        app.logger.warning(
            "Ecriture refusee (CSRF) sur %s — cookie de session envoye : %s, session "
            "chargee : %s, jeton envoye : %s, contexte : dest=%r site=%r origine=%r "
            "referent=%r",
            request.path, bool(request.cookies.get("session")), bool(good), bool(sent),
            request.headers.get("Sec-Fetch-Dest"), request.headers.get("Sec-Fetch-Site"),
            request.headers.get("Origin"), request.headers.get("Referer"),
        )
        if request.path.startswith("/api/") or request.path.startswith("/entreprise/"):
            return jsonify({"ok": False,
                            "error": "Session expirée. Rechargez la page."}), 400
        if request.path == "/connexion":
            # Formulaire perime (page ouverte avant un redemarrage) ou cookie de
            # session non conserve par le navigateur : on reaffiche le formulaire
            # avec un jeton neuf plutot qu'une page "Bad Request" sans issue.
            message = "Session expirée, merci de saisir vos identifiants à nouveau."
            if not good:
                message = ("Votre navigateur n'a pas conservé le cookie de session. "
                           "Si la page est affichée dans un cadre, ouvrez-la dans un "
                           "onglet dédié puis reconnectez-vous.")
            return render_template("login.html", erreur=message), 400
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
