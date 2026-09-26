"""Application Balise Prospection - prospection d'entreprises francaises sans site web."""
import gzip
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
        # Les formulaires sont minuscules, mais une archive .zip de livrable
        # peut peser quelques dizaines de Mo : la limite est donc haute, et
        # chaque depot de .zip est verifie (taille et extension) cote route.
        MAX_CONTENT_LENGTH=32 * 1024 * 1024,
        DATA_DIR=data_dir,
        # Session transportee dans l'URL (jeton signe) : apercu en iframe tierce.
        EMBEDDED_SESSION=embarque,
        # Jeu de demonstration (entreprises fictives) : jamais utilise par defaut,
        # seulement sur demande explicite (?demo=1) et si cette option est active.
        DEMO_ALLOWED=_env_flag("MBDV_DEMO"),
        # Apercu : ouvrir la session du dirigeant sans mot de passe, pour juger
        # l'affichage sans repasser par le formulaire. Ignore hors apercu embarque.
        DEJA_CONNECTE=_env_flag("MBDV_DEJA_CONNECTE"),
        # Nom affiche du site (l'association reste signee MBDV). Modifiable sans
        # toucher au code : MBDV_SITE_NAME="Autre nom".
        SITE_NAME=(os.environ.get("MBDV_SITE_NAME") or "Balise").strip(),
        ASSET_VERSION=_empreinte_assets(app.static_folder),
        # Cadre autorise a afficher le site (anti-clickjacking). Vide par defaut :
        # l'apercu dans une iframe d'un autre site continue de fonctionner. Sur un
        # deploiement reel, MBDV_FRAME_ANCESTORS="'self'" ferme l'affichage en cadre.
        FRAME_ANCESTORS=(os.environ.get("MBDV_FRAME_ANCESTORS") or "").strip(),
    )

    @app.context_processor
    def _globaux_de_rendu():
        return {
            "asset_version": app.config["ASSET_VERSION"],
            "site_name": app.config["SITE_NAME"],
        }

    @app.after_request
    def _cache_des_assets(reponse):
        """CSS et JS : cache long quand l'URL porte son empreinte (?v=...).

        L'empreinte change des qu'un fichier change, donc une copie gardee par le
        navigateur ne peut pas devenir obsolete - et la page suivante n'a plus a
        retelecharger la feuille de style. Sans empreinte (URL tapee a la main),
        on garde la revalidation stricte.
        """
        if request.path.startswith("/static/") and request.path.endswith((".css", ".js")):
            if request.args.get("v"):
                reponse.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                reponse.headers["Cache-Control"] = "no-store, must-revalidate"
        return reponse

    @app.after_request
    def _compression(reponse):
        """Compression gzip des reponses texte (HTML, CSS, JS, JSON, SVG).

        Divise le poids des pages par 4 a 5 : c'est le principal gain sur les
        changements de page, le CSS et le JavaScript restant les plus gros
        fichiers echanges.
        """
        if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
            return reponse
        if reponse.status_code != 200 or reponse.headers.get("Content-Encoding"):
            return reponse
        if reponse.mimetype not in {"text/html", "text/css", "text/plain", "image/svg+xml",
                                    "application/javascript", "text/javascript",
                                    "application/json"}:
            return reponse
        # Les fichiers statiques sont envoyes en flux : on les materialise d'abord
        # (ils sont petits) pour pouvoir les compresser.
        reponse.direct_passthrough = False
        donnees = reponse.get_data()
        if len(donnees) < 1024:
            return reponse
        comprime = gzip.compress(donnees, 6)
        if len(comprime) >= len(donnees):
            return reponse
        reponse.set_data(comprime)
        reponse.headers["Content-Encoding"] = "gzip"
        reponse.headers["Content-Length"] = str(len(comprime))
        reponse.headers.add("Vary", "Accept-Encoding")
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
        reponse.headers.setdefault("X-Content-Type-Options", "nosniff")
        reponse.headers.setdefault("X-XSS-Protection", "1; mode=block")
        # Tout est servi par l'application (polices, icones, styles compris) : une
        # politique restrictive ne casse rien. 'unsafe-inline' reste necessaire pour
        # les scripts et styles poses dans les pages (compteurs animes, theme).
        # frame-ancestors n'est pose que si MBDV_FRAME_ANCESTORS est defini, pour ne
        # pas casser l'apercu en iframe.
        csp = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "form-action 'self'",
            "base-uri 'none'",
            "object-src 'none'",
        ]
        if app.config.get("FRAME_ANCESTORS"):
            csp.append(f"frame-ancestors {app.config['FRAME_ANCESTORS']}")
        reponse.headers.setdefault("Content-Security-Policy", "; ".join(csp))
        if auth.embarque():
            # Le jeton de session circule dans l'URL : pas de fuite par Referer,
            # pas de mise en cache par un intermediaire. Vrai aussi quand le
            # navigateur refuse le cookie : la session passe alors par l'URL sans
            # que le mode ait ete force au demarrage.
            reponse.headers.setdefault("Referrer-Policy", "same-origin")
            reponse.headers.setdefault("Cache-Control", "no-store")
        else:
            reponse.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if app.config["SESSION_COOKIE_SECURE"]:
            # Le site est servi en HTTPS (MBDV_COOKIE_SECURE=1) : on demande alors
            # au navigateur de ne plus jamais revenir en clair.
            reponse.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
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
