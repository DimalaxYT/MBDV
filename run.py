"""Point d'entree de l'application MBDV.

Lancement :
    python run.py

Variables d'environnement reconnues :
    PORT                  port impose par la plateforme d'hebergement (Render...)
    MBDV_PORT             port d'ecoute (5050 par defaut)
    MBDV_HOST             interface d'ecoute (0.0.0.0 par defaut)
    MBDV_DATA_DIR         dossier de donnees (./data par defaut)
    MBDV_ADMIN_USER       identifiant du premier compte (admin)
    MBDV_ADMIN_PASSWORD   mot de passe initial du compte admin
    MBDV_ASSOCIE_USER     identifiant du second compte (associe)
    MBDV_ASSOCIE_PASSWORD mot de passe initial du compte associe
"""
import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT") or os.environ.get("MBDV_PORT") or "5050")
    host = os.environ.get("MBDV_HOST", "0.0.0.0")  # noqa: S104 - conteneur/hebergement
    print(f"MBDV - Prospection  |  http://{host}:{port}")
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port, threaded=True)
