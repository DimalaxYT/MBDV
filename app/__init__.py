"""Application MBDV - prospection d'entreprises francaises sans site web."""
import os
import secrets

from flask import Flask

_DEFAULT_SECRET_FILE = "secret.key"


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


def create_app() -> Flask:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.environ.get("MBDV_DATA_DIR", os.path.join(base_dir, "data"))
    os.makedirs(data_dir, exist_ok=True)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=_load_or_create_secret(data_dir),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        DATA_DIR=data_dir,
    )

    try:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)  # type: ignore[assignment]
    except ImportError:
        pass

    from . import db
    db.init_app(app)

    from . import auth
    auth.init_app(app)

    from . import views
    app.register_blueprint(views.bp)

    return app
