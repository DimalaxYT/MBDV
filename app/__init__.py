"""Application MBDV - prospection d'entreprises francaises sans site web."""
import hashlib
import logging
import os
import secrets

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

_DEFAULT_SECRET_FILE = "secret.key"  # noqa: S105 - nom de fichier, pas un secret


def _env_flag(name: str) -> bool:
    """Variable d'environnement booleenne : '1', 'true', 'oui'... active l'option."""
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "oui", "yes", "on"}


def _load_or_create_secret(data_dir: str) -> str:
    """Cle de signature des sessions, persistee pour survivre aux redemarrages."""
    path = os.path.join(data_dir, _DEFAULT_SECRET_FILE)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            secret = fh.read().strip()
            if secret:
                return secret
    secret = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(secret)
    return secret


def _empreinte_assets(dossier: str) -> str:
    """Empreinte courte des fichiers statiques, ajoutee en ?v= aux URL /static/.

    Sans elle, un navigateur - ou un cache intermediaire devant l'application -
    peut continuer a servir un CSS ou un JS perime apres un deploiement : la page
    s'affiche alors sans mise en forme. L'empreinte change des qu'un fichier change.
    """
    morceaux = []
    for racine, _dossiers, fichiers in os.walk(dossier):
        for nom in fichiers:
            chemin = os.path.join(racine, nom)
            try:
                infos = os.stat(chemin)
            except OSError:
                continue
            relatif = os.path.relpath(chemin, dossier).replace(os.sep, "/")
            morceaux.append(f"{relatif}:{int(infos.st_mtime)}:{infos.st_size}")
    brut = "|".join(sorted(morceaux)).encode("utf-8")
    return hashlib.sha1(brut).hexdigest()[:10]  # noqa: S324 - empreinte de cache, pas de securite


def create_app() -> Flask:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.environ.get("MBDV_DATA_DIR", os.path.join(base_dir, "data"))
    os.makedirs(data_dir, exist_ok=True)

    app = Flask(__name__)
    # Apercu affiche dans une iframe d'un autre site : les navigateurs traitent
    # alors le cookie comme un cookie tiers et le refusent en SameSite=Lax.
    # SameSite=None + Secure + Partitioned (CHIPS) est la combinaison acceptee.
    embarque = _env_flag("MBDV_EMBEDDED_COOKIES") or _env_flag("MBDV_EMBEDDED_SESSION")
    app.config.update(
        SECRET_KEY=_load_or_create_secret(data_dir),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="None" if embarque else "Lax",
        # A activer (MBDV_COOKIE_SECURE=1) des que le site est servi en HTTPS.
        SESSION_COOKIE_SECURE=embarque or _env_flag("MBDV_COOKIE_SECURE"),
        SESSION_COOKIE_PARTITIONED=embarque,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        DATA_DIR=data_dir,
        # Session transportee dans l'URL (jeton signe) : apercu en iframe tierce.
        EMBEDDED_SESSION=embarque,
        # Jeu de demonstration (entreprises fictives) : jamais utilise par defaut,
        # seulement sur demande explicite (?demo=1) et si cette option est active.
        DEMO_ALLOWED=_env_flag("MBDV_DEMO"),
        # Nom affiche du site (l'association reste signee MBDV). Modifiable sans
        # toucher au code : MBDV_SITE_NAME="Autre nom".
        SITE_NAME=(os.environ.get("MBDV_SITE_NAME") or "Balise").strip(),
        ASSET_VERSION=_empreinte_assets(app.static_folder),
    )
    @app.context_processor
    def _globaux_de_rendu():
        return {
            "asset_version": app.config["ASSET_VERSION"],
            "site_name": app.config["SITE_NAME"],
        }

    @app.after_request
    def _pas_de_cache_pour_les_assets(reponse):
        """CSS et JS : aucun stockage intermediaire.

        L'empreinte en ?v= suffit normalement, mais un cache intermediaire qui
        conserverait une copie partielle du fichier laisserait la page sans mise
        en forme. Ici, ces fichiers sont toujours revalides.
        """
        if request.path.startswith("/static/") and request.path.endswith((".css", ".js")):
            reponse.headers["Cache-Control"] = "no-store, must-revalidate"
        return reponse

    app.logger.setLevel(logging.INFO)
    # Flask installe son propre handler : sans cela, chaque ligne sort deux fois.
    app.logger.propagate = False

    # ProxyFix ne doit etre active que derriere un reverse proxy de confiance :
    # sinon n'importe qui peut usurper son adresse via X-Forwarded-For.
    if _env_flag("MBDV_TRUST_PROXY"):
        try:
            from werkzeug.middleware.proxy_fix import ProxyFix
            app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # type: ignore[assignment]
        except ImportError:  # pragma: no cover - werkzeug est une dependance de Flask
            app.logger.warning("ProxyFix indisponible : X-Forwarded-For ignore.")

    from . import db
    db.init_app(app)

    from . import auth
    auth.init_app(app)

    from . import views
    app.register_blueprint(views.bp)

    @app.after_request
    def _en_tetes(reponse):
        if embarque:
            # Le jeton de session circule dans l'URL : pas de fuite par Referer,
            # pas de mise en cache par un intermediaire.
            reponse.headers.setdefault("Referrer-Policy", "same-origin")
            reponse.headers.setdefault("Cache-Control", "no-store")
        return reponse

    @app.errorhandler(HTTPException)
    def _erreur_http(exc):
        """Erreurs HTTP dans le style de l'application, et en JSON pour le JS."""
        if request.path.startswith(("/api/", "/entreprise/")):
            return jsonify({"ok": False, "error": exc.description or exc.name}), exc.code
        return render_template(
            "erreur.html", code=exc.code, titre=exc.name, message=exc.description
        ), exc.code

    return app
