"""Authentification : sessions, CSRF, limitation des tentatives, filtres de rendu."""
import secrets
import time
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from flask import (
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

PARIS_TZ = ZoneInfo("Europe/Paris")

# Mode apercu embarque (MBDV_EMBEDDED_SESSION=1) : les navigateurs refusent les
# cookies de session dans une iframe tierce, donc la session voyage dans l'URL
# sous forme de jeton signe (parametre _s). A n'activer que pour un apercu.
_JETON_SEL = "mbdv-session"
_CSRF_SEL = "mbdv-csrf"
# Duree du jeton d'URL (mode apercu) : elle suit la case "rester connecte" du
# formulaire de connexion, faute de pouvoir poser un cookie de session ici.
JETON_DUREE_LONGUE = 60 * 60 * 24 * 30  # "rester connecte" coche : 30 jours
JETON_DUREE_COURTE = 60 * 60 * 12       # decoche : le temps d'une journee de travail
JETON_DUREE = JETON_DUREE_LONGUE        # duree maximale acceptee a la lecture
CSRF_DUREE = 60 * 60 * 12               # validite d'un jeton CSRF signe
_META_JETON = 'meta name="session-token"'

# 12 tentatives par identifiant et 40 tentatives par adresse IP, toutes les 5 minutes.
# Les compteurs sont en memoire : ils sont remis a zero au redemarrage du service.
_RATE = {}
_RATE_LIMIT_USER = 12
_RATE_LIMIT_IP = 40
_RATE_WINDOW = 300
_RATE_MAX_KEYS = 5000


def embarque() -> bool:
    """Vrai si l'application tourne en mode apercu embarque (session dans l'URL)."""
    return bool(current_app.config.get("EMBEDDED_SESSION"))


def _serialiseur(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


def duree_jeton() -> int:
    """Duree de validite du jeton d'URL, selon le choix fait a la connexion."""
    return JETON_DUREE_LONGUE if session.get("rester_connecte") else JETON_DUREE_COURTE


def jeton_session() -> str:
    """Jeton signe transportant la session dans l'URL (mode apercu).

    Retourne une chaine vide hors mode apercu, ou si la session n'est pas ouverte.
    """
    if not embarque():
        return ""
    # "rester_connecte" doit voyager avec la session : sans lui, les pages
    # suivantes resigneraient un jeton court et l'utilisateur serait deconnecte
    # au bout de 12 h malgre sa case cochee.
    charge = {cle: session[cle] for cle in
              ("uid", "username", "display_name", "csrf", "rester_connecte")
              if cle in session}
    if not charge:
        # Deja porteur d'un jeton (page anonyme ayant transite par l'URL).
        return str(request.values.get("_s") or "")
    # itsdangerous n'accepte de duree qu'a la lecture : l'echeance voyage donc
    # dans la charge utile, et c'est elle qui distingue "rester connecte" du
    # simple maintien de session.
    charge["exp"] = int(time.time()) + duree_jeton()
    return _serialiseur(_JETON_SEL).dumps(charge)


def url_avec_jeton(url: str) -> str:
    """Ajoute le jeton de session a une URL interne (mode apercu uniquement)."""
    jeton = jeton_session()
    if not jeton:
        return url
    separateur = "&" if "?" in url else "?"
    return f"{url}{separateur}{urlencode({'_s': jeton})}"


def _charge_session() -> None:
    """Hydrate la session depuis le jeton d'URL quand aucun cookie n'est disponible."""
    if not embarque() or "uid" in session:
        return
    jeton = request.values.get("_s")
    if not jeton:
        return
    try:
        donnees = _serialiseur(_JETON_SEL).loads(jeton, max_age=JETON_DUREE)
    except (BadSignature, SignatureExpired):
        return
    if not isinstance(donnees, dict):
        return
    echeance = donnees.pop("exp", None)
    if echeance is not None and float(echeance) < time.time():
        # Jeton emis sans "rester connecte" et arrive a echeance.
        return
    session.update(donnees)


def _csrf_signe_valide(sent: str) -> bool:
    """Verifie un jeton CSRF signe (mode apercu), sans etat cote serveur."""
    try:
        donnees = _serialiseur(_CSRF_SEL).loads(sent, max_age=CSRF_DUREE)
    except (BadSignature, SignatureExpired, TypeError):
        return False
    if not isinstance(donnees, dict):
        return False
    attendu = session.get("csrf", "")
    envoye = str(donnees.get("csrf") or "")
    if attendu:
        return secrets.compare_digest(envoye, str(attendu))
    return True   # page anonyme : la signature suffit


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


def login(user_row, rester_connecte: bool = True) -> None:
    """Ouvre la session (cookie, et jeton d'URL en mode apercu).

    `rester_connecte` vient de la case du formulaire de connexion : cochee, la
    session survit a la fermeture du navigateur (cookie persistant de 30 jours,
    jeton d'URL de 30 jours) ; decochee, elle s'arrete a la fermeture du
    navigateur (cookie de session, jeton d'URL de 12 heures).
    """
    session.clear()
    session["uid"] = user_row["id"]
    session["username"] = user_row["username"]
    session["display_name"] = user_row["display_name"]
    session["csrf"] = secrets.token_hex(16)
    session["rester_connecte"] = bool(rester_connecte)
    session.permanent = bool(rester_connecte)


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
    if embarque():
        # Sans cookie, le jeton doit etre verifiable sans etat : il est signe.
        return _serialiseur(_CSRF_SEL).dumps({"csrf": session.get("csrf") or ""})
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
            # La racine n'a pas besoin de "next" : apres connexion on veut la page
            # d'accueil (explication de l'outil), pas la recherche directement.
            suite = "" if request.path == "/" else request.path
            return redirect(url_for("views.connexion", next=suite or None))
        return view(*args, **kwargs)
    return wrapped


def init_app(app) -> None:
    app.before_request(_charge_session)

    @app.before_request
    def _csrf_guard():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf", "")
        good = session.get("csrf", "")
        if embarque():
            if sent and _csrf_signe_valide(sent):
                return None
        elif good and sent and secrets.compare_digest(sent, good):
            return None
        # Diagnostic : c'est ici qu'on voit un apercu heberge qui perd son cookie.
        app.logger.warning(
            "Ecriture refusee (CSRF) sur %s — mode apercu : %s, cookie de session "
            "envoye : %s, session chargee : %s, jeton envoye : %s, contexte : dest=%r "
            "site=%r origine=%r referent=%r",
            request.path, embarque(), bool(request.cookies.get("session")), bool(good), bool(sent),
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
            "session_token": jeton_session,
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
