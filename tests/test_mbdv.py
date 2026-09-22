"""Tests de non-regression MBDV.

Aucun acces reseau : l'API officielle et les resolutions DNS sont remplacees par
des doubles de test, ce qui rend la suite rapide et deterministe.
"""
import io
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from html.parser import HTMLParser

import pytest
import requests

from app import auth, create_app, db, detect, gov_api, views

# --------------------------------------------------------------------------
# Garde-fou reseau : la suite doit rester entierement hors ligne.
# Sans cela, un test peut passer en local (reseau bloque) et echouer en
# integration continue (reseau disponible) - ou l'inverse.
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _aucun_appel_reseau(monkeypatch):
    """Tout appel HTTP direct fait echouer le test qui le declenche."""

    def interdit(*args, **kwargs):
        raise AssertionError(
            "Appel reseau interdit dans les tests : remplacez l'appel par un double "
            "(monkeypatch de gov_api.search / fetch_by_siren, ou detect._resout).")

    monkeypatch.setattr(requests.Session, "request", interdit)
    monkeypatch.setattr(requests.api, "request", interdit)


def _api_officielle_indisponible(monkeypatch):
    """Simule une base officielle injoignable, comme en developpement hors ligne."""
    monkeypatch.setattr(gov_api, "fetch_by_siren", lambda siren: None)

# --------------------------------------------------------------------------
# Doubles de test
# --------------------------------------------------------------------------

def entreprise(siren: str, nom: str, **extra) -> dict:
    """Une entreprise au format normalise de gov_api.normalize_result()."""
    base = {
        "siren": siren,
        "nom": nom,
        "sigle": None,
        "forme": "Société par actions simplifiée (SAS)",
        "nature_code": "5710",
        "date_creation": "2019-04-01",
        "date_fermeture": None,
        "categorie": "PME",
        "actif": True,
        "naf_code": "96.02A",
        "naf_label": "Coiffure",
        "section": "S",
        "effectif_code": "01",
        "effectif": "1 à 2 salariés",
        "nb_etablissements": 1,
        "adresse": "14 RUE DES SABLES 44600 SAINT-NAZAIRE",
        "rue": "14 RUE DES SABLES",
        "code_postal": "44600",
        "commune": "SAINT-NAZAIRE",
        "departement": "44",
        "region": "Pays de la Loire",
        "siret_siege": siren + "00012",
        "date_debut_activite": "2019-04-01",
        "enseigne": None,
        "lat": None,
        "lng": None,
        "dirigeants": [{"nom": "Alice Perrin", "qualite": "Président", "naissance": "1970",
                        "morale": False}],
        "finances": {"annee": None, "ca": None, "resultat_net": None},
        "tva": None,
        "complements": {"bio": False, "rge": False, "ess": False, "ei": False,
                        "formation": False, "qualiopi": False, "association": False,
                        "avocat": False, "societe_mission": False},
    }
    base.update(extra)
    return base


def faux_search(items_par_page, total_results=100, total_pages=3):
    """Fabrique un remplacant de gov_api.search (25 elements par page)."""
    def _search(q="", page=1, **kwargs):
        page = max(1, int(page))
        items = items_par_page(page)
        return {"items": items, "total_results": total_results,
                "page": page, "total_pages": total_pages}
    return _search


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("MBDV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("MBDV_TRUST_PROXY", raising=False)
    monkeypatch.delenv("MBDV_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("MBDV_DEMO", raising=False)   # jeu fictif desactive par defaut
    # Ni DNS ni API : tout est deterministe et hors ligne.
    monkeypatch.setattr(detect, "_resout", lambda host: False)
    _api_officielle_indisponible(monkeypatch)
    auth._RATE.clear()
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def connexion(client, username="admin", password="MBDV-admin-2026",  # noqa: S107
              ip="127.0.0.1", formulaire=None, **params):
    page = client.get("/connexion")
    token = re.search(r'name="_csrf" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    donnees = {"username": username, "password": password, "_csrf": token}
    donnees.update(formulaire or {})
    return client.post("/connexion", query_string=params,
                       data=donnees, environ_base={"REMOTE_ADDR": ip})


def jeton(client, path="/"):
    page = client.get(path)
    trouve = re.search(r'<meta name="csrf" content="([^"]+)"', page.get_data(as_text=True))
    return trouve.group(1) if trouve else None


class _Attributs(HTMLParser):
    """Releve des valeurs d'attributs pour verifier ce que le navigateur recevra."""

    def __init__(self, nom):
        super().__init__()
        self.nom = nom
        self.valeurs = []

    def handle_starttag(self, tag, attrs):
        attributs = dict(attrs)
        if self.nom in attributs:
            self.valeurs.append(attributs[self.nom])


def snapshots(html: str) -> list:
    parseur = _Attributs("data-snapshot")
    parseur.feed(html)
    return [json.loads(valeur) for valeur in parseur.valeurs]


# --------------------------------------------------------------------------
# Connexion, session, CSRF
# --------------------------------------------------------------------------

def test_connexion_ok(client):
    reponse = connexion(client)
    assert reponse.status_code == 302
    assert client.get("/").status_code == 200


def test_connexion_refusee(client):
    reponse = connexion(client, password="mauvais")
    assert reponse.status_code == 200
    assert "incorrect" in reponse.get_data(as_text=True)


def test_ecriture_sans_jeton_csrf_refusee(client):
    connexion(client)
    assert client.post("/api/masquer", json={"siren": "123456789", "nom": "X",
                                             "raison": "Doublon"}).status_code == 400


def test_api_et_fiche_protegees(client):
    assert client.get("/entreprise/123456789/detail").status_code == 401
    # sans session, le garde CSRF repond avant le decorateur : 400 ou 401, jamais 200
    assert client.post("/api/suivre", json={}).status_code in (400, 401)
    assert client.get("/portefeuille").status_code == 302


def test_redirection_ouverte_bloquee(app):
    for cible in ("//evil.example.com", "https://evil.example.com", "\\\\evil.example.com"):
        reponse = connexion(app.test_client(), next=cible)
        assert reponse.headers["Location"] == "/accueil", cible


def test_retour_apres_connexion_conserve(client):
    page = client.get("/connexion?next=/portefeuille").get_data(as_text=True)
    assert 'action="/connexion?next=/portefeuille"' in page
    reponse = connexion(client, next="/portefeuille")
    assert reponse.headers["Location"] == "/portefeuille"


def test_limitiation_tentatives_par_identifiant(client):
    for _ in range(13):
        connexion(client, password="mauvais", ip="10.0.0.1")
    reponse = connexion(client, ip="10.0.0.1")
    assert "Trop de tentatives" in reponse.get_data(as_text=True)
    # un autre associe, depuis une autre adresse, n'est pas bloque
    autre = connexion(client, username="associe", password="MBDV-associe-2026", ip="10.0.0.2")
    assert autre.status_code == 302


def test_x_forwarded_for_non_usurpable(client, monkeypatch):
    """X-Forwarded-For ne doit pas permettre de reinitialiser le compteur."""
    for _ in range(13):
        connexion(client, password="mauvais", ip="10.0.0.3")
    reponse = connexion(client, ip="10.0.0.3", headers={"X-Forwarded-For": "9.9.9.9"})
    assert "Trop de tentatives" in reponse.get_data(as_text=True)


def test_formulaire_sans_cookie_reste_utilisable(client, caplog):
    """Cookie de session perdu (apercu en iframe) : on reaffiche le formulaire."""
    client.application.logger.propagate = True  # pour que caplog voie le journal
    reponse = client.post("/connexion", data={"username": "admin", "password": "MBDV-admin-2026"})
    corps = reponse.get_data(as_text=True)
    assert reponse.status_code == 400
    assert "Bad Request" not in corps
    assert 'name="_csrf"' in corps            # un jeton neuf est fourni
    assert "cookie de session" in corps       # message actionnable
    assert "Ecriture refusee (CSRF)" in caplog.text


def test_api_sans_cookie_repond_en_json(client):
    reponse = client.post("/api/masquer", json={"siren": "123456789"})
    assert reponse.status_code == 400
    assert reponse.is_json and reponse.get_json()["ok"] is False


def test_page_erreur_soignee(client):
    reponse = client.get("/inexistant")
    assert reponse.status_code == 404
    corps = reponse.get_data(as_text=True)
    assert "404" in corps and "Retour à la recherche" in corps


@pytest.fixture()
def app_demo(tmp_path, monkeypatch):
    """Application ou le jeu de demonstration peut etre demande explicitement."""
    monkeypatch.setenv("MBDV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MBDV_DEMO", "1")
    monkeypatch.setattr(detect, "_resout", lambda host: False)
    monkeypatch.setattr(gov_api, "search", _api_hors_service)
    _api_officielle_indisponible(monkeypatch)
    auth._RATE.clear()
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def app_embarque(tmp_path, monkeypatch):
    """Application en mode apercu embarque (aucun cookie de session accepte)."""
    monkeypatch.setenv("MBDV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MBDV_EMBEDDED_SESSION", "1")
    monkeypatch.setattr(detect, "_resout", lambda host: False)
    _api_officielle_indisponible(monkeypatch)
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise("848902672", "CARACOLE COIFFURE")]))
    auth._RATE.clear()
    application = create_app()
    application.config.update(TESTING=True)
    return application


def _connexion_sans_cookie(client, rester=None):
    """Parcours d'apercu : le navigateur ne conserve aucun cookie.

    Renvoie (url d'arrivee, jeton de session) — l'arrivee est la page d'accueil.
    """
    page = client.get("/connexion").get_data(as_text=True)
    jeton = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
    donnees = {"username": "admin", "password": "MBDV-admin-2026", "_csrf": jeton}
    if rester is not None:
        donnees["rester_connecte"] = rester
    reponse = client.post("/connexion", data=donnees)
    assert reponse.status_code == 302
    cible = reponse.headers["Location"]
    assert "_s=" in cible
    return cible, cible.split("_s=")[1]


def test_apercu_embarque_connexion_sans_cookie(app_embarque):
    client = app_embarque.test_client()
    cible, jeton = _connexion_sans_cookie(client)
    assert cible.startswith("/accueil")
    # Le client de test conserve le cookie : on repart d'un client vierge pour
    # simuler un navigateur qui refuse le cookie de session.
    vierge = app_embarque.test_client()
    page = vierge.get("/?_s=" + jeton + "&q=coiffure")
    assert page.status_code == 200
    corps = page.get_data(as_text=True)
    assert "CARACOLE COIFFURE" in corps            # session reconnue sans cookie
    assert "Espace associés" not in corps
    assert 'name="session-token" content=""' not in corps   # jeton propage au JS


def test_apercu_embarque_post_sans_cookie(app_embarque):
    client = app_embarque.test_client()
    _, jeton = _connexion_sans_cookie(client)
    page = client.get("/?_s=" + jeton).get_data(as_text=True)
    jeton_csrf = re.search(r'<meta name="csrf" content="([^"]+)"', page).group(1)
    # une requete JSON doit passer avec le jeton d'URL et le CSRF signe
    reponse = client.post("/api/suivre?_s=" + jeton,
                          json={"snapshot": entreprise("848902672", "CARACOLE COIFFURE")},
                          headers={"X-CSRF-Token": jeton_csrf})
    assert reponse.status_code == 200, reponse.get_data(as_text=True)
    assert reponse.get_json()["tracked"] is True


def test_apercu_embarque_jeton_invalide_ignore(app_embarque):
    client = app_embarque.test_client()
    reponse = client.get("/?_s=nimportequoi")
    assert reponse.status_code == 302   # pas de session ouverte -> redirection


@pytest.fixture()
def app_deja_connecte(tmp_path, monkeypatch):
    """Apercu embarque avec MBDV_DEJA_CONNECTE=1 : session ouverte sans formulaire."""
    monkeypatch.setenv("MBDV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MBDV_EMBEDDED_SESSION", "1")
    monkeypatch.setenv("MBDV_DEJA_CONNECTE", "1")
    monkeypatch.setattr(detect, "_resout", lambda host: False)
    _api_officielle_indisponible(monkeypatch)
    auth._RATE.clear()
    application = create_app()
    application.config.update(TESTING=True)
    return application


def test_apercu_deja_connecte_sans_identifiants(app_deja_connecte):
    """La racine ouvre la session du dirigeant et atterrit sur l'accueil."""
    vierge = app_deja_connecte.test_client()          # aucun cookie, aucun jeton
    reponse = vierge.get("/")
    assert reponse.status_code == 302
    cible = reponse.headers["Location"]
    assert cible.startswith("/accueil") and "_s=" in cible

    page = vierge.get(cible)
    corps = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'action="/deconnexion"' in corps         # deja connecte, sans mot de passe
    assert 'name="password"' not in corps           # plus de formulaire de connexion

    # Les ecritures reservees au dirigeant restent possibles (onglet Comptes).
    assert "Dirigeant" in vierge.get("/staff").get_data(as_text=True)


def test_apercu_deja_connecte_absent_par_defaut(app_embarque):
    """Sans MBDV_DEJA_CONNECTE, l'apercu embarque redemande bien la connexion."""
    vierge = app_embarque.test_client()
    assert vierge.get("/").status_code == 302
    assert "/connexion" in vierge.get("/").headers["Location"]
    assert 'action="/deconnexion"' not in vierge.get("/connexion").get_data(as_text=True)


def test_apercu_deja_connecte_ignore_hors_apercu(app, monkeypatch):
    """Meme avec la variable, un site normal garde son formulaire de connexion."""
    monkeypatch.setenv("MBDV_DEJA_CONNECTE", "1")
    application = create_app()
    application.config.update(TESTING=True)
    client = application.test_client()
    reponse = client.get("/")
    assert reponse.status_code == 302
    assert "/connexion" in reponse.headers["Location"]


def test_apercu_embarque_en_tetes_de_reponse(app_embarque):
    client = app_embarque.test_client()
    reponse = client.get("/connexion")
    assert reponse.headers.get("Referrer-Policy") == "same-origin"
    assert reponse.headers.get("Cache-Control") == "no-store"


def test_cookies_partitionnes_pour_apercu_embarque(tmp_path, monkeypatch):
    """Mode apercu : cookie tiers accepte (CHIPS) et jeton d'URL en secours."""
    monkeypatch.setenv("MBDV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MBDV_EMBEDDED_COOKIES", "1")
    application = create_app()
    assert application.config["SESSION_COOKIE_SAMESITE"] == "None"
    assert application.config["SESSION_COOKIE_SECURE"] is True
    assert application.config["SESSION_COOKIE_PARTITIONED"] is True
    assert application.config["EMBEDDED_SESSION"] is True

    client = application.test_client()
    page = client.get("/connexion", base_url="https://exemple.test")
    jeton = re.search(r'name="_csrf" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    reponse = client.post("/connexion", base_url="https://exemple.test",
                          data={"username": "admin", "password": "MBDV-admin-2026",
                                "_csrf": jeton})
    cookie = reponse.headers.get("Set-Cookie", "")
    assert "Partitioned" in cookie
    assert "SameSite=None" in cookie
    assert "Secure" in cookie


# --------------------------------------------------------------------------
# Page d'accueil apres connexion
# --------------------------------------------------------------------------

def test_connexion_ouvre_la_page_d_accueil(client):
    reponse = connexion(client)
    assert reponse.status_code == 302
    assert reponse.headers["Location"] == "/accueil"
    page = client.get("/accueil")
    assert page.status_code == 200
    corps = page.get_data(as_text=True)
    assert "Ce que fait l'outil, étape par étape" in corps
    assert "Aucune donnée inventée" in corps
    assert 'href="/"' in corps                      # bouton vers la recherche
    assert "Lancer une recherche" in corps


def test_accueil_accessible_depuis_la_navigation(client):
    connexion(client)
    html = client.get("/").get_data(as_text=True)
    assert 'href="/accueil"' in html                # entree de menu + marque
    assert "Accueil" in html
    # barre reduite : les icones doivent rester identifiables
    for infobulle in ("Accueil", "Recherche d'entreprises", "Portefeuille commercial",
                      "Panel staff", "Mon mot de passe"):
        assert f'title="{infobulle}"' in html


def test_accueil_protege_par_la_connexion(client):
    reponse = client.get("/accueil")
    assert reponse.status_code == 302
    assert reponse.headers["Location"].endswith("/connexion?next=/accueil")


def test_next_prime_sur_la_page_d_accueil(client):
    reponse = connexion(client, next="/staff")
    assert reponse.headers["Location"] == "/staff"


def test_visite_de_la_racine_mene_a_l_accueil(client):
    """Simple visite de / sans session : apres connexion, page d'accueil."""
    reponse = client.get("/")
    assert reponse.status_code == 302
    assert reponse.headers["Location"] == "/connexion"      # pas de ?next=/
    reponse = connexion(client)
    assert reponse.headers["Location"] == "/accueil"


def test_visite_d_une_page_precise_est_conservee(client):
    reponse = client.get("/portefeuille")
    assert reponse.headers["Location"] == "/connexion?next=/portefeuille"
    assert connexion(client, next="/portefeuille").headers["Location"] == "/portefeuille"


def test_page_d_accueil_widgets_et_animations(app, client):
    """Widgets alimentes par la base reelle + animations d'apparition."""
    connexion(client)
    with base(client) as b:
        b.execute("INSERT INTO site_cache (siren, status, domain, source, checked_at)"
                  " VALUES ('111111111', 'aucun', NULL, 'dns', '2026-01-01T00:00:00Z')")
        b.execute("INSERT INTO site_cache (siren, status, domain, source, checked_at)"
                  " VALUES ('222222222', 'site', 'exemple.fr', 'dns', '2026-01-01T00:00:00Z')")
        b.execute("INSERT INTO tracked (siren, snapshot, status, added_by, added_at)"
                  " VALUES ('111111111', '{}', 'devis', 'admin', '2026-01-01T00:00:00Z')")
    page = client.get("/accueil").get_data(as_text=True)
    assert "Entreprises suivies" in page
    assert "Sans site détecté" in page
    assert "Le cycle commercial" in page
    assert 'data-count="1"' in page                    # 1 sans-site, 1 suivie
    assert page.count('class="widget reveal"') == 4
    assert "pip-fill st-devis" in page                 # barre du statut reel
    assert page.count("reveal") > 12                   # apparitions animees
    assert "En quatre gestes" in page


def test_pas_de_libelle_pilotage(client):
    """Le libelle de section "Pilotage" a ete retire de la barre laterale."""
    connexion(client)
    html = client.get("/accueil").get_data(as_text=True)
    assert "Pilotage" not in html
    assert "Compte" in html                            # la section Compte reste


def test_barre_laterale_toujours_visible(client):
    """La barre laterale ne s'escamote plus : plus de rail, plus d'auto-masquage."""
    from pathlib import Path
    connexion(client)
    html = client.get("/accueil").get_data(as_text=True)
    assert "data-sidebar-rail" not in html
    assert "sidebar-hidden" not in html

    css = Path("app/static/css/main.css").read_text(encoding="utf-8")
    assert "sidebar-hidden" not in css
    assert "sidebar-rail" not in css
    assert "transition-delay" not in css               # aucun delai sur les etats
    assert ".nav-item:active" in css                   # reponse immediate au clic
    assert "@media (prefers-reduced-motion: reduce)" in css

    js = Path("app/static/js/app.js").read_text(encoding="utf-8")
    assert "sidebar-hidden" not in js
    assert "ESCAMOTAGE" not in js
    assert "prefers-reduced-motion" in js              # animations toujours bridees


def test_barre_laterale_compacte_seulement_sur_ecrans_etroits(client):
    """Les libelles restent affiches sur un ecran de bureau, meme a 1000 px."""
    from pathlib import Path
    css = Path("app/static/css/main.css").read_text(encoding="utf-8")
    assert "@media (max-width: 900px) {" in css
    compacte = css.split("@media (max-width: 900px) {")[1].split("\n}")[0]
    assert ".sidebar {" in compacte                     # icones seules
    assert ".sidebar:hover" in compacte                 # etiquettes immediates au survol
    assert ".sidebar:focus-within" in compacte           # idem au clavier
    assert css.split("@media (max-width: 1020px) {")[1].split("\n}")[0].count(".sidebar") == 0


def test_aucun_delai_sur_les_actions(client):
    """Rien ne doit retarder un clic : ni CSS, ni temporisation dans le script."""
    import re
    from pathlib import Path
    js = Path("app/static/js/app.js").read_text(encoding="utf-8")
    assert "550" not in js                             # ancien rechargement differe
    for valeur in re.findall(r"setTimeout\([^,]+,\s*(\d+)\)", js):
        assert int(valeur) <= 2000                     # aucune attente perceptible
    css = Path("app/static/css/main.css").read_text(encoding="utf-8")
    assert "transition-delay" not in css

    connexion(client)
    html = client.get("/?_q=coiffure&q=coiffure").get_data(as_text=True)
    assert "data-chargement" in html                   # etat "Recherche..." au clic
    assert ".btn.is-loading" in css
    assert "is-loading" in js
    assert "pageshow" in js                            # bouton remis apres retour arriere


def test_nom_du_site_et_logo(client):
    """Nom en rapport avec l'usage du site, et logo (marque + favicon)."""
    from pathlib import Path
    page = client.get("/connexion").get_data(as_text=True)
    assert "<title>Connexion - Balise Prospection</title>" in page
    assert "brand-logo" in page                        # logo inline
    assert ">Balise<" in page

    logo = Path("app/static/logo.svg").read_text(encoding="utf-8")
    favicon = Path("app/static/favicon.svg").read_text(encoding="utf-8")
    assert "<svg" in logo and 'viewBox="0 0 48 48"' in logo
    assert "linearGradient" in logo                    # le degrade du logo
    assert "rect" in favicon and "circle" in favicon    # favicon reprend la marque

    connexion(client)
    html = client.get("/accueil").get_data(as_text=True)
    assert "Bienvenue dans Balise" in html
    assert ">Balise<" in html and "brand-dot" in html
    assert "Balise interroge la base officielle" in html


def test_nom_du_site_configurable(tmp_path, monkeypatch):
    """Le nom peut etre change sans toucher au code : MBDV_SITE_NAME."""
    monkeypatch.setenv("MBDV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MBDV_SITE_NAME", "Nom Sur Mesure")
    application = create_app()
    application.config.update(TESTING=True)
    page = application.test_client().get("/connexion").get_data(as_text=True)
    assert "Nom Sur Mesure" in page
    assert "<title>Connexion - Nom Sur Mesure Prospection</title>" in page


def test_jeton_long_conserve_apres_plusieurs_pages(app_embarque):
    """Case cochee : les pages suivantes resignent un jeton de 30 jours.

    Regression : le choix ne voyageait pas dans le jeton, si bien que la page
    suivante retombait sur la duree courte (12 h) malgre la case cochee.
    """
    import time

    from itsdangerous import URLSafeTimedSerializer
    client = app_embarque.test_client()
    _, jeton = _connexion_sans_cookie(client, rester="1")
    page = client.get("/accueil?_s=" + jeton).get_data(as_text=True)
    nouveau = re.search(r'<meta name="session-token" content="([^"]+)"', page).group(1)
    charge = URLSafeTimedSerializer(app_embarque.config["SECRET_KEY"],
                                    salt="mbdv-session").loads(nouveau)
    assert charge["rester_connecte"] is True
    assert 29 * 86400 < charge["exp"] - time.time() <= 30 * 86400 + 60


def test_jeton_court_sans_la_case(app_embarque):
    """Case decochee : jeton de 12 h, renouvele a chaque page consultee."""
    import time

    from itsdangerous import URLSafeTimedSerializer
    client = app_embarque.test_client()
    _, jeton = _connexion_sans_cookie(client, rester="")     # case decochee
    page = client.get("/accueil?_s=" + jeton).get_data(as_text=True)
    nouveau = re.search(r'<meta name="session-token" content="([^"]+)"', page).group(1)
    charge = URLSafeTimedSerializer(app_embarque.config["SECRET_KEY"],
                                    salt="mbdv-session").loads(nouveau)
    assert charge["rester_connecte"] is False
    assert charge["exp"] - time.time() <= 12 * 3600 + 60


def test_styles_et_scripts_mis_en_cache_par_empreinte(client):
    """CSS et JS : cache long quand l'URL porte l'empreinte, sinon revalidation.

    L'empreinte change des qu'un fichier change : la page suivante ne retelecharge
    donc plus la feuille de style, ce qui supprime l'attente au changement de page.
    """
    from pathlib import Path
    page = client.get("/connexion").get_data(as_text=True)
    version = re.search(r"/static/css/main\.css\?v=([0-9a-f]{10})", page).group(1)

    feuille = client.get(f"/static/css/main.css?v={version}")
    assert feuille.status_code == 200
    assert "immutable" in feuille.headers["Cache-Control"]
    assert "max-age=31536000" in feuille.headers["Cache-Control"]
    corps = feuille.get_data(as_text=True)
    assert corps == Path("app/static/css/main.css").read_text(encoding="utf-8")
    # Garde-fou : la feuille servie contient bien toutes les regles de l'accueil,
    # de la barre laterale et de la page de connexion.
    for regle in (".widgets {", ".pip-track", ".step-card", ".brand-logo {",
                  ".check input:checked"):
        assert regle in corps

    script = client.get(f"/static/js/app.js?v={version}")
    assert "immutable" in script.headers["Cache-Control"]

    # Sans empreinte, on reste prudent : revalidation systematique.
    sans = client.get("/static/css/main.css")
    assert sans.headers["Cache-Control"] == "no-store, must-revalidate"
    assert sans.get_data(as_text=True) == corps


def test_reponses_compressees(client):
    """Le HTML, le CSS et le JS voyagent en gzip : pages 4 a 5 fois plus legeres."""
    import gzip as gz
    entetes = {"Accept-Encoding": "gzip, deflate"}

    page = client.get("/connexion", headers=entetes)
    assert page.headers.get("Content-Encoding") == "gzip"
    assert "Accept-Encoding" in page.headers.get("Vary", "")
    assert b"Espace associ" in gz.decompress(page.get_data())   # contenu intact
    assert int(page.headers["Content-Length"]) < len(gz.decompress(page.get_data()))

    css = client.get("/static/css/main.css?v=abc123", headers=entetes)
    assert css.headers.get("Content-Encoding") == "gzip"
    assert b".widgets" in gz.decompress(css.get_data())

    # Un client qui n'annonce pas gzip recoit la reponse telle quelle.
    sec = client.get("/connexion")
    assert sec.headers.get("Content-Encoding") is None


def test_animation_de_changement_de_page(client):
    """Entree du contenu, barre de progression et lien marque des le clic."""
    from pathlib import Path
    css = Path("app/static/css/main.css").read_text(encoding="utf-8")
    assert "@keyframes entree-page" in css
    assert ".main { animation: entree-page" in css
    assert ".progress {" in css and ".progress.is-actif" in css
    assert ".nav-item.is-en-cours" in css
    assert ".main { animation: none; }" in css            # mouvement reduit respecte

    js = Path("app/static/js/app.js").read_text(encoding="utf-8")
    assert "initTransitionsDePage" in js
    assert "demarrerNavigation" in js
    assert "ev.defaultPrevented" in js                    # ne double pas les autres clics
    assert "metaKey" in js and "target" in js             # liens externes / nouvel onglet
    assert 'classList.add("page-en-cours")' in js
    assert "pageshow" in js                               # retour arriere sans barre bloquee


def test_barre_reduite_sans_debordement(client):
    """Regle les deux bugs signales : surlignage qui deborde, icones qui disparaissent.

    Cause : les libelles etaient positionnes hors du flux et les lignes elargies a
    la main (68 -> 234 px) alors que la barre n'en faisait que 68 : le surlignage
    depassait du panneau, et l'icone, centree dans une boite plus etroite que son
    contenu, se retrouvait rognee.
    Desormais la barre s'elargit elle-meme, avec `overflow: hidden` : les lignes
    restent dans le panneau et les libelles sont dans le flux.
    """
    from pathlib import Path
    css = Path("app/static/css/main.css").read_text(encoding="utf-8")
    compacte = css.split("@media (max-width: 900px) {")[1].split("\n}\n")[0]

    # la barre s'elargit elle-meme et coupe proprement ce qui depasse
    assert "width: 68px;" in compacte
    assert "width: 234px;" in compacte
    assert "overflow: hidden;" in compacte
    assert ".sidebar:hover, .sidebar:focus-within {" in compacte
    # plus rien hors du flux, plus de lignes elargies a la main
    assert "position: absolute" not in compacte
    assert "position: fixed" in compacte
    assert ".sidebar::before" not in compacte
    assert "width: 234px; }" not in compacte          # ancien elargissement manuel
    # les libelles restent dans le flux, simplement reveles en fondu
    assert "opacity: 0" in compacte and "white-space: nowrap;" in compacte
    assert ".main { margin-left: 68px; }" in compacte


def test_pictogrammes_visibles_en_barre_reduite(client):
    """Les icones de la barre repliee ne doivent jamais etre ecrasees.

    Un SVG est un element remplacable : dans un conteneur flex etroit (barre de
    68 px, contenu utile de 16 px), le libelle insecable qui le suit absorbe la
    place et le pictogramme se reduit jusqu'a disparaitre s'il n'est pas fige.
    """
    from pathlib import Path
    css = Path("app/static/css/main.css").read_text(encoding="utf-8")
    compacte = css.split("@media (max-width: 900px) {")[1].split("\n}\n")[0]

    # les pictogrammes gardent leur taille, ici comme dans le bloc compact
    assert ".icon { flex: 0 0 auto; }" in css
    assert ".sidebar .icon { flex: 0 0 auto; }" in compacte
    assert ".brand-logo { flex: 0 0 auto;" in css        # deja fige : le logo reste
    assert ".avatar {" in css and "flex: 0 0 32px;" in css
    # et le libelle cede la place au pictogramme au lieu de le pousser dehors
    assert "min-width: 0;" in compacte and "overflow: hidden;" in compacte
    # aucune regle du passage en barre reduite ne les cache
    for disparition in ("display: none", "visibility: hidden", "opacity: 0; }"):
        assert not re.search(r"\.nav-item[^{]*\.icon[^{]*\{[^}]*" + re.escape(disparition),
                             compacte), disparition

    # et les pictogrammes sont bien presents dans chaque entree de la barre
    connexion(client)
    page = client.get("/accueil").get_data(as_text=True)
    for libelle in ("Accueil", "Recherche", "Portefeuille", "Suivi d'équipe", "Panel staff"):
        assert re.search(r'class="nav-item[^"]*"[^>]*>\s*<svg class="icon"[^>]*>.*?</svg>\s*<span>'
                         + re.escape(libelle) + "</span>", page, re.S), libelle


def test_surlignage_de_l_onglet_actif(client):
    """Le trait d'accent est dans l'element (inset), plus un ::before decale.

    Regression : le trait etait un pseudo-element en left:-14px, qui sortait de la
    barre et se desalignait selon la largeur de la mise en page.
    """
    from pathlib import Path
    css = Path("app/static/css/main.css").read_text(encoding="utf-8")
    assert ".nav-item.active::before" not in css
    assert "box-shadow: inset 3px 0 0 var(--accent)" in css
    # l'etat "clic immediat" est identique a l'etat actif : aucun saut visuel
    bloc = css.split(".nav-item.active,")[1][:40]
    assert ".nav-item.is-en-cours" in bloc
    # le focus clavier utilise un contour, sans entrer en conflit avec la lueur interne
    assert "outline: 2px solid var(--accent-2)" in css
    assert ".nav-item:focus-visible { outline: none" not in css


def test_fermeture_de_la_barre_synchronisee_avec_le_texte(client):
    """Le texte ne doit pas rester visible apres le panneau.

    Regression : le fondu avait un retard de 80 ms dans les deux sens, donc a la
    fermeture le texte trainait apres le fond qui se retractait.
    """
    from pathlib import Path
    css = Path("app/static/css/main.css").read_text(encoding="utf-8")
    compacte = css.split("@media (max-width: 900px) {")[1].split("\n}\n")[0]
    # fermeture : fondu court, sans retard
    assert "transition: opacity 0.12s ease;" in compacte
    # ouverture : petit retard, uniquement sur l'etat survol/focus
    assert ".sidebar:hover .brand-text" in compacte
    assert "transition: opacity 0.18s ease 0.08s;" in compacte
    assert compacte.count("transition: opacity 0.18s ease 0.08s;") == 1


def _encaisse(client, montant, date, heure="12:00", siret=""):
    """Enregistre un encaissement depuis le panel staff (comme le formulaire)."""
    return client.post("/staff/benefice", data={
        "_csrf": jeton(client, "/staff"), "siret": siret, "montant": montant,
        "signe_le": date, "heure": heure})


def test_benefice_du_portefeuille(app):
    """Le portefeuille affiche les encaissements : totaux, graphique, montants."""
    client = app.test_client()
    connexion(client)
    with base(client) as b:
        for siren, nom in (("111111111", "Alpha"), ("222222222", "Beta")):
            b.execute("INSERT INTO tracked (siren, snapshot, status, added_by, added_at)"
                      " VALUES (?, ?, 'client', 'admin', '2026-01-01T00:00:00Z')",
                      (siren, json.dumps(entreprise(siren, nom))))

    # aucun encaissement : message d'amorcage, pas de graphique
    page = client.get("/portefeuille").get_data(as_text=True)
    assert "Bénéfice" in page and "Aucun montant renseigné" in page
    assert "graph-svg" not in page

    # le portefeuille ne saisit rien : c'est le panel staff qui enregistre
    assert "encaissement-form" not in page
    assert 'name="montant"' not in page
    assert "panel staff" in page

    assert "pf=montant" in _encaisse(client, "4500", "2026-09-10", "14:30",
                                     siret="11111111100012").headers["Location"]
    _encaisse(client, "1500.50", "2026-08-02", siret="222222222")

    page = client.get("/portefeuille").get_data(as_text=True)
    assert "6\u202f001" in page or "6 001" in page          # 4500 + 1500,50 arrondis
    assert "graph-svg" in page
    assert page.count('<rect class="graph-bar') == 12         # un point par mois
    assert "is-courant" in page and "Cumul" in page
    assert "10/09/2026 à 14:30" in page
    assert "montant-vu-fort" in page
    # l'encaissement rattache par SIRET apparait sur la ligne de l'entreprise
    ligne = re.search(r'<tr class="result-row" data-siren="111111111".*?</tr>', page, re.S)
    assert ligne and "4\u202f500" in ligne.group(0)


def test_encaissement_siret_facultatif(app):
    """Le code SIRET est facultatif : sans lui, l'encaissement est sans rattachement."""
    client = app.test_client()
    connexion(client)
    assert "pf=montant" in _encaisse(client, "900", "2026-03-01").headers["Location"]

    page = client.get("/staff").get_data(as_text=True)
    assert "Sans rattachement" in page
    with app.app_context():
        from app import db
        ligne = db.one("SELECT siret, siren, libelle FROM benefices")
    assert ligne["siret"] == "" and ligne["siren"] is None

    # un SIRET valide rattache l'encaissement a l'entreprise suivie
    with base(client) as b:
        b.execute("INSERT INTO tracked (siren, snapshot, status, added_by, added_at)"
                  " VALUES ('333333333', ?, 'client', 'admin', '2026-01-01T00:00:00Z')",
                  (json.dumps(entreprise("333333333", "Gamma")),))
    _encaisse(client, "1200", "2026-03-02", siret="333 333 333 00025")
    with app.app_context():
        from app import db
        ligne = db.one("SELECT siret, siren, libelle FROM benefices WHERE montant_cents = 120000")
    assert ligne["siret"] == "33333333300025"        # espaces retires
    assert ligne["siren"] == "333333333"
    assert ligne["libelle"] == "Gamma"

    # heure laissee vide : 12:00 (valeur annoncee sur le formulaire)
    _encaisse(client, "90", "2026-03-03", heure="")
    with app.app_context():
        from app import db
        assert db.one("SELECT encaisse_le FROM benefices WHERE montant_cents = 9000"
                      )["encaisse_le"] == "2026-03-03T12:00"


def test_benefice_refuse_les_saisies_invalides(app):
    """SIRET, montant, date : chaque erreur a son message, rien n'est enregistre."""
    client = app.test_client()
    connexion(client)
    csrf = jeton(client, "/staff")

    def envoi(**champs):
        donnees = {"_csrf": csrf, "siret": "", "montant": "1000", "signe_le": "2026-04-01",
                   "heure": "10:00"}
        donnees.update(champs)
        return client.post("/staff/benefice", data=donnees)

    assert "pf_erreur=siret" in envoi(siret="12ab34").headers["Location"]
    assert "pf_erreur=siret" in envoi(siret="123").headers["Location"]
    assert "pf_erreur=montant" in envoi(montant="").headers["Location"]
    assert "pf_erreur=montant" in envoi(montant="-5").headers["Location"]
    assert "pf_erreur=montant" in envoi(montant="abc").headers["Location"]
    assert "pf_erreur=date" in envoi(signe_le="").headers["Location"]
    assert "pf_erreur=date" in envoi(signe_le="31/12/2026").headers["Location"]
    assert "pf_erreur=date" in envoi(heure="99:99").headers["Location"]

    with app.app_context():
        from app import db
        assert db.one("SELECT COUNT(*) n FROM benefices")["n"] == 0

    # chaque message est explique a l'ecran
    for motif, texte in (("siret", "SIRET"), ("montant", "Montant"), ("date", "Date")):
        page = client.get(f"/staff?pf_erreur={motif}").get_data(as_text=True)
        assert texte in page


def test_suppression_d_un_encaissement(app):
    client = app.test_client()
    connexion(client)
    _encaisse(client, "700", "2026-02-02")
    with app.app_context():
        from app import db
        identifiant = db.one("SELECT id FROM benefices")["id"]

    reponse = client.post("/staff/benefice/supprimer",
                          data={"_csrf": jeton(client, "/staff"), "id": identifiant})
    assert "pf=supprime" in reponse.headers["Location"]
    with app.app_context():
        from app import db
        assert db.one("SELECT COUNT(*) n FROM benefices")["n"] == 0
    # identifiant inconnu : message clair, rien ne casse
    reponse = client.post("/staff/benefice/supprimer",
                          data={"_csrf": jeton(client, "/staff"), "id": 99999})
    assert "pf_erreur=absent" in reponse.headers["Location"]


def test_benefice_reserve_au_dirigeant(app):
    """Le compte associé ne voit pas la section et ne peut pas la manipuler."""
    client = app.test_client()
    connexion(client, username="associe", password="MBDV-associe-2026")
    page = client.get("/staff").get_data(as_text=True)
    assert 'action="/staff/benefice"' not in page
    assert 'action="/staff/benefice/supprimer"' not in page
    csrf = jeton(client, "/staff")
    assert client.post("/staff/benefice", data={
        "_csrf": csrf, "montant": "100", "signe_le": "2026-01-01"}).status_code == 403
    assert client.post("/staff/benefice/supprimer", data={
        "_csrf": csrf, "id": 1}).status_code == 403


def test_migration_des_montants_vers_les_encaissements(tmp_path, monkeypatch):
    """Les montants deja enregistres sur les entreprises sont reportes une fois."""
    import sqlite3
    dossier = tmp_path / "data"
    dossier.mkdir()
    conn = sqlite3.connect(dossier / "mbdv.sqlite3")
    conn.executescript(
        "CREATE TABLE tracked ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " siren TEXT UNIQUE NOT NULL, snapshot TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'a_contacter', note TEXT NOT NULL DEFAULT '',"
        " added_by TEXT NOT NULL, added_at TEXT NOT NULL,"
        " status_updated_by TEXT, status_updated_at TEXT,"
        " montant_cents INTEGER NOT NULL DEFAULT 0, signe_le TEXT);")
    conn.execute("INSERT INTO tracked (siren, snapshot, status, added_by, added_at,"
                 " montant_cents, signe_le) VALUES ('111111111', ?, 'client', 'admin',"
                 " '2026-01-01T00:00:00Z', 450000, '2026-09-10T14:30')",
                 (json.dumps(entreprise("111111111", "Alpha")),))
    conn.execute("INSERT INTO tracked (siren, snapshot, status, added_by, added_at)"
                 " VALUES ('222222222', ?, 'client', 'admin', '2026-01-01T00:00:00Z')",
                 (json.dumps(entreprise("222222222", "Beta")),))
    conn.commit()
    conn.close()

    monkeypatch.setenv("MBDV_DATA_DIR", str(dossier))
    application = create_app()
    application.config.update(TESTING=True)
    with application.app_context():
        from app import db, views
        lignes = db.query("SELECT siren, libelle, montant_cents, encaisse_le FROM benefices")
        assert len(lignes) == 1                       # seule la ligne valorisee
        assert lignes[0]["siren"] == "111111111"
        assert lignes[0]["libelle"] == "Alpha"
        assert lignes[0]["montant_cents"] == 450000
        assert lignes[0]["encaisse_le"] == "2026-09-10T14:30"
        # un second demarrage ne duplique rien
        db.init_app(application)
        assert db.one("SELECT COUNT(*) n FROM benefices")["n"] == 1
        assert views.benefice_du_portefeuille()["total_cents"] == 450000


def test_staff_ajoute_et_retire_du_portefeuille(app):
    """Le dirigeant gere le portefeuille depuis le panel staff."""
    client = app.test_client()
    connexion(client, username="admin", password="MBDV-admin-2026")
    page = client.get("/staff").get_data(as_text=True)
    assert "Portefeuille de l'équipe" in page
    assert 'action="/staff/portefeuille"' in page
    assert "Le portefeuille est vide" in page

    csrf = jeton(client, "/staff")
    # ajout d'une entreprise connue par son instantane (masquee auparavant)
    with app.app_context():
        from app import db
        db.execute("INSERT INTO hides (siren, nom, raison, details, snapshot, hidden_by,"
                   " hidden_at) VALUES ('111111111', 'Alpha', 'Doublon', '',"
                   " ?, 'admin', '2026-01-01T00:00:00Z')",
                   (json.dumps(entreprise("111111111", "Alpha")),))
    reponse = client.post("/staff/portefeuille",
                          data={"_csrf": csrf, "action": "ajouter", "siren": "111111111"})
    assert "pf=ajoute" in reponse.headers["Location"]
    page = client.get(reponse.headers["Location"]).get_data(as_text=True)
    assert "Entreprise ajoutée au portefeuille" in page
    assert "@admin" in page and "111111111" in page

    # doublon refuse
    reponse = client.post("/staff/portefeuille",
                          data={"_csrf": csrf, "action": "ajouter", "siren": "111111111"})
    assert "pf_erreur=deja" in reponse.headers["Location"]
    # SIREN inconnu : jamais de fiche inventee
    reponse = client.post("/staff/portefeuille",
                          data={"_csrf": csrf, "action": "ajouter", "siren": "999999999"})
    assert "pf_erreur=introuvable" in reponse.headers["Location"]

    # retrait
    reponse = client.post("/staff/portefeuille",
                          data={"_csrf": csrf, "action": "retirer", "siren": "111111111"})
    assert "pf=retire" in reponse.headers["Location"]
    with app.app_context():
        from app import db
        assert db.one("SELECT id FROM tracked WHERE siren = '111111111'") is None


def test_staff_ajoute_avec_le_jeu_de_demonstration_sur_demande(app_demo):
    """L'ajout par SIREN utilise le jeu fictif seulement si `demo=1` est demande."""
    client = app_demo.test_client()
    connexion(client)
    with app_demo.app_context():
        from app import db
        db.execute("INSERT INTO hides (siren, nom, raison, details, snapshot, hidden_by,"
                   " hidden_at) VALUES ('111111111', 'Alpha', 'Doublon', '', '',"
                   " 'admin', '2026-01-01T00:00:00Z')")
    # SIREN uniquement connu du jeu de demonstration
    siren_demo = "848902672"

    # sans demande explicite : la fiche n'est pas inventee
    page = client.get("/staff").get_data(as_text=True)
    assert "Mode démonstration" not in page
    reponse = client.post("/staff/portefeuille", data={
        "_csrf": jeton(client, "/staff"), "action": "ajouter", "siren": siren_demo})
    assert "pf_erreur=introuvable" in reponse.headers["Location"]

    # avec ?demo=1 : la fiche fictive est acceptee, et l'information est transmise
    page = client.get("/staff?demo=1").get_data(as_text=True)
    assert "Mode démonstration" in page
    assert 'name="demo" value="1"' in page
    reponse = client.post("/staff/portefeuille", data={
        "_csrf": jeton(client, "/staff?demo=1"), "action": "ajouter",
        "siren": siren_demo, "demo": "1"})
    assert "pf=ajoute" in reponse.headers["Location"]
    assert "demo=1" in reponse.headers["Location"]
    with app_demo.app_context():
        from app import db
        assert db.one("SELECT id FROM tracked WHERE siren = ?", (siren_demo,)) is not None


def test_portefeuille_du_staff_reserve_au_dirigeant(app):
    client = app.test_client()
    connexion(client, username="associe", password="MBDV-associe-2026")
    page = client.get("/staff").get_data(as_text=True)
    assert "Portefeuille de l'équipe" not in page
    reponse = client.post("/staff/portefeuille", data={
        "_csrf": jeton(client, "/staff"), "action": "retirer", "siren": "111111111"})
    assert reponse.status_code == 403


def test_transition_de_page_native(client):
    """Vraie animation de changement de page : transition native + repli anime."""
    from pathlib import Path
    css = Path("app/static/css/main.css").read_text(encoding="utf-8")
    assert "@view-transition { navigation: auto; }" in css
    assert "::view-transition-old(root)" in css
    assert "::view-transition-new(root)" in css
    assert "@keyframes page-sortante" in css and "@keyframes page-entrante" in css
    assert "view-transition-name: barre-laterale" in css  # la barre ne clignote pas
    assert "html.vt .main { animation: none; }" in css      # pas de double animation
    assert ".main.sortie" in css                            # repli : sortie animee
    assert "::view-transition-group(*)" in css              # mouvement reduit respecte

    for gabarit in ("app/templates/base.html", "app/templates/login.html"):
        page = Path(gabarit).read_text(encoding="utf-8")
        assert 'CSS.supports("@view-transition { navigation: auto }")' in page
        assert 'classList.add("vt")' in page

    js = Path("app/static/js/app.js").read_text(encoding="utf-8")
    assert "transitionNative" in js
    assert 'classList.add("sortie")' in js
    assert "window.location.href = lien.href" in js         # navigation differee du repli


def test_suite_hermetique():
    """Le garde-fou reseau est bien actif.

    Il a fallu le verifier en conditions reelles : un test utilisait un SIREN du
    jeu de demonstration qui existe vraiment dans la base officielle. Il passait
    en local (reseau bloque) et echouait en integration continue (reseau
    disponible, l'API renvoyant la vraie entreprise).
    """
    with pytest.raises(AssertionError, match="Appel reseau interdit"):
        requests.get("https://recherche-entreprises.api.gouv.fr/search", timeout=1)


def test_migration_role_sur_une_base_existante(tmp_path, monkeypatch):
    """Une base creee avant les roles est mise a jour au demarrage.

    Le compte le plus ancien (le dirigeant) devient administrateur : la base de
    production n'a donc besoin d'aucune intervention.
    """
    import sqlite3
    dossier = tmp_path / "data"
    dossier.mkdir()
    conn = sqlite3.connect(dossier / "mbdv.sqlite3")
    conn.executescript(
        "CREATE TABLE users ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " username TEXT UNIQUE NOT NULL,"
        " display_name TEXT NOT NULL,"
        " password_hash TEXT NOT NULL,"
        " created_at TEXT NOT NULL);")
    conn.execute("INSERT INTO users (username, display_name, password_hash, created_at)"
                 " VALUES ('patron', 'Dirigeant', 'scrypt$x$y', '2026-01-01T00:00:00Z')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("MBDV_DATA_DIR", str(dossier))
    application = create_app()
    application.config.update(TESTING=True)
    with application.app_context():
        from app import db
        assert db.one("SELECT role FROM users WHERE username = 'patron'")["role"] == "admin"
        # les comptes par defaut sont completes en tant qu'associes
        assert db.one("SELECT role FROM users WHERE username = 'associe'")["role"] == "associe"


def test_comptes_des_associes_dans_le_panel_staff(app):
    """Le dirigeant change l'identifiant et le mot de passe du compte associé."""
    client = app.test_client()
    connexion(client)                                   # admin = dirigeant

    page = client.get("/staff").get_data(as_text=True)
    assert "Comptes des associés" in page
    assert 'action="/staff/comptes"' in page
    assert "@associe" in page

    csrf = jeton(client, "/staff")
    reponse = client.post("/staff/comptes", data={
        "_csrf": csrf, "cible": "2", "username": "associe2",
        "display_name": "Associé principal", "nouveau": "NouveauMdp-Associe-2026",
        "confirmation": "NouveauMdp-Associe-2026",
    })
    assert reponse.status_code == 302
    assert "compte=ok" in reponse.headers["Location"]

    # les nouveaux identifiants fonctionnent, les anciens non
    autre = app.test_client()
    connexion(autre, username="associe2", password="NouveauMdp-Associe-2026")
    assert autre.get("/accueil").status_code == 200
    ancien = app.test_client()
    reponse = connexion(ancien, username="associe", password="MBDV-associe-2026")
    assert "incorrect" in reponse.get_data(as_text=True)

    # le mot de passe seul peut etre change, sans toucher a l'identifiant
    client.post("/staff/comptes", data={
        "_csrf": jeton(client, "/staff"), "cible": "2", "username": "associe2",
        "display_name": "Associé principal", "nouveau": "Encore-Un-Mot-De-Passe",
        "confirmation": "Encore-Un-Mot-De-Passe",
    })
    encore = app.test_client()
    connexion(encore, username="associe2", password="Encore-Un-Mot-De-Passe")
    assert encore.get("/accueil").status_code == 200
    with app.app_context():
        from app import db
        ligne = db.one("SELECT display_name FROM users WHERE username = ?", ("associe2",))
    assert ligne["display_name"] == "Associé principal"


def test_comptes_refuses_au_compte_associe(app):
    """Le compte associé ne voit pas la section et ne peut pas l'appeler."""
    client = app.test_client()
    connexion(client, username="associe", password="MBDV-associe-2026")
    page = client.get("/staff").get_data(as_text=True)
    assert "Comptes des associés" not in page

    reponse = client.post("/staff/comptes", data={
        "_csrf": jeton(client, "/staff"), "cible": "1", "username": "pirate",
        "display_name": "Pirate", "nouveau": "MotDePasse-Pirate",
        "confirmation": "MotDePasse-Pirate",
    })
    assert reponse.status_code == 403
    with app.app_context():
        from app import db
        assert db.one("SELECT 1 AS n FROM users WHERE username = ?", ("pirate",)) is None
        assert db.one("SELECT username FROM users WHERE id = 1")["username"] == "admin"


def test_comptes_controles_de_saisie(app):
    """Identifiant invalide ou pris, mot de passe trop court, confirmation differente."""
    client = app.test_client()
    connexion(client)
    csrf = jeton(client, "/staff")

    def envoi(**champs):
        donnees = {"_csrf": csrf, "cible": "2", "username": "associe",
                   "display_name": "Associe", "nouveau": "", "confirmation": ""}
        donnees.update(champs)
        return client.post("/staff/comptes", data=donnees)

    assert "3 à 32 caractères" in envoi(username="a").get_data(as_text=True)
    assert "déjà utilisé" in envoi(username="admin").get_data(as_text=True)
    assert "au moins 10 caractères" in envoi(nouveau="court", confirmation="court").get_data(
        as_text=True)
    assert "confirmation ne correspond" in envoi(
        nouveau="MotDePasse-Associe", confirmation="Autre-Mot-De-Passe").get_data(as_text=True)
    assert "propre compte" in envoi(cible="1").get_data(as_text=True)

    # rien n'a bouge
    with app.app_context():
        from app import db
        assert db.one("SELECT username FROM users WHERE id = 2")["username"] == "associe"



def test_version_des_assets_dans_les_url(client):
    """CSS et JS sont appeles avec ?v=<empreinte> : evite de servir un fichier perime."""
    page = client.get("/connexion").get_data(as_text=True)
    versions = re.findall(r"/static/(?:css/main\.css|js/app\.js)\?v=([0-9a-f]{10})", page)
    assert len(versions) == 2
    assert len(set(versions)) == 1

    accueil = client.get("/accueil")
    assert accueil.status_code == 302            # page privee : redirection
    connexion(client)
    page = client.get("/accueil").get_data(as_text=True)
    assert re.search(r"/static/css/main\.css\?v=[0-9a-f]{10}", page)


def test_case_rester_connecte_cochee(client):
    """Case "rester connecte" : cochee, le cookie de session est persistant."""
    page = client.get("/connexion").get_data(as_text=True)
    assert 'name="rester_connecte"' in page
    assert 'value="1" checked' in page           # cochee par defaut

    connexion(client, formulaire={"rester_connecte": "1"})
    cookie = client.get_cookie("session")
    assert cookie is not None
    assert cookie.expires is not None            # cookie persistant


def test_case_rester_connecte_decochee(app):
    """Decochee : cookie de session, efface a la fermeture du navigateur."""
    client = app.test_client()
    connexion(client)                            # la case n'est pas envoyee
    cookie = client.get_cookie("session")
    assert cookie is not None
    assert cookie.expires is None

    with client.session_transaction() as sess:
        assert sess.get("rester_connecte") is False


def test_duree_du_jeton_selon_la_case(app):
    """Mode apercu : le jeton d'URL suit lui aussi le choix fait a la connexion."""
    from flask import session as fsession
    with app.test_request_context("/"):
        fsession["rester_connecte"] = True
        assert auth.duree_jeton() == auth.JETON_DUREE_LONGUE == 30 * 24 * 3600
        fsession["rester_connecte"] = False
        assert auth.duree_jeton() == auth.JETON_DUREE_COURTE == 12 * 3600


def test_contenu_visible_sans_javascript(client):
    """Sans JavaScript, les elements d'apparition restent visibles (classe .js)."""
    from pathlib import Path
    css = Path("app/static/css/main.css").read_text(encoding="utf-8")
    assert "html.js .reveal { opacity: 0; }" in css
    assert "\n.reveal { opacity: 0; }" not in css
    connexion(client)
    page = client.get("/accueil").get_data(as_text=True)
    assert "classList.add(\"js\")" in page     # posee avant le premier rendu


def test_aucun_libelle_de_navigation_masque(client):
    """Les libelles restent rendus partout : plus de texte qui apparait/disparait."""
    from pathlib import Path
    css = Path("app/static/css/main.css").read_text(encoding="utf-8")
    assert ".nav-label { display: none" not in css
    assert ".sidebar-foot .nav-label" not in css
    assert "text-align: center" not in css.split(".nav-label {")[1][:120]
    # Un libelle trop long est coupe proprement, jamais cache.
    bloc = css.split(".nav-label {")[1][:260]
    assert "overflow: hidden" in bloc and "text-overflow: ellipsis" in bloc
    assert "white-space: nowrap" in bloc
    assert "overflow: hidden" in css.split(".sidebar {")[1][:400]


# --------------------------------------------------------------------------
# Attribut data-snapshot : le bug qui cassait masquage / suivi / reverification
# --------------------------------------------------------------------------

def test_snapshot_recherche_parsable(client, app, monkeypatch):
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise(f"84890267{i}", f"ENTREPRISE {i}") for i in range(25)]))
    connexion(client)
    html = client.get("/?q=coiffure&departement=44").get_data(as_text=True)
    valeurs = snapshots(html)
    assert len(valeurs) == 25
    assert all(valeur["siren"] for valeur in valeurs)


def test_snapshot_fiche_parsable(client, monkeypatch):
    monkeypatch.setattr(gov_api, "fetch_by_siren",
                        lambda siren: entreprise(siren, "CARACOLE COIFFURE"))
    connexion(client)
    valeurs = snapshots(client.get("/entreprise/848902672/detail").get_data(as_text=True))
    assert valeurs and valeurs[0]["nom"] == "CARACOLE COIFFURE"


def test_snapshot_portefeuille_parsable(client, monkeypatch):
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise("848902672", "CARACOLE COIFFURE")]))
    connexion(client)
    client.post("/api/suivre", json={"snapshot": entreprise("848902672", "CARACOLE COIFFURE")},
                headers={"X-CSRF-Token": jeton(client)})
    valeurs = snapshots(client.get("/portefeuille").get_data(as_text=True))
    assert valeurs and valeurs[0]["siren"] == "848902672"


# --------------------------------------------------------------------------
# Masquage, portefeuille, panel staff
# --------------------------------------------------------------------------

def test_masquage_et_retablissement(client, monkeypatch):
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise("848902672", "CARACOLE COIFFURE")] if page == 1 else []))
    connexion(client)
    entete = {"X-CSRF-Token": jeton(client)}

    assert "CARACOLE COIFFURE" in client.get("/?q=coiffure").get_data(as_text=True)
    reponse = client.post("/api/masquer", json={
        "siren": "848902672", "nom": "CARACOLE COIFFURE", "raison": "Possède déjà un site web",
        "snapshot": entreprise("848902672", "CARACOLE COIFFURE")}, headers=entete)
    assert reponse.status_code == 200
    assert "CARACOLE COIFFURE" not in client.get("/?q=coiffure").get_data(as_text=True)
    assert "CARACOLE COIFFURE" in client.get("/staff").get_data(as_text=True)

    with base(client) as b:
        masquage = b.one("SELECT id FROM hides WHERE siren = '848902672'")
    assert client.post("/api/retablir", json={"id": masquage["id"]}, headers=entete).status_code == 200
    assert "CARACOLE COIFFURE" in client.get("/?q=coiffure").get_data(as_text=True)


@contextmanager
def base(client):
    """Acces direct a la base pour verifier ce qui a ete ecrit."""
    with client.application.app_context():
        yield db


def test_masquage_exige_une_raison(client, monkeypatch):
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise("848902672", "CARACOLE COIFFURE")]))
    connexion(client)
    entete = {"X-CSRF-Token": jeton(client)}
    corps = {"siren": "848902672", "nom": "CARACOLE COIFFURE", "raison": "Raison inventée"}
    assert client.post("/api/masquer", json=corps, headers=entete).status_code == 400
    corps["raison"] = "Autre"
    assert client.post("/api/masquer", json=corps, headers=entete).status_code == 400


def test_masquer_retire_du_portefeuille(client, monkeypatch):
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise("848902672", "CARACOLE COIFFURE")]))
    connexion(client)
    entete = {"X-CSRF-Token": jeton(client)}
    client.post("/api/suivre", json={"snapshot": entreprise("848902672", "CARACOLE COIFFURE")},
                headers=entete)
    client.post("/api/masquer", json={"siren": "848902672", "nom": "CARACOLE COIFFURE",
                                      "raison": "Doublon"}, headers=entete)
    with base(client) as b:
        assert b.one("SELECT id FROM tracked WHERE siren = '848902672'") is None
        assert b.one("SELECT id FROM hides WHERE siren = '848902672'") is not None


def test_pas_de_double_masquage(client, monkeypatch):
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise("848902672", "CARACOLE COIFFURE")]))
    connexion(client)
    entete = {"X-CSRF-Token": jeton(client)}
    corps = {"siren": "848902672", "nom": "CARACOLE COIFFURE", "raison": "Doublon"}
    client.post("/api/masquer", json=corps, headers=entete)
    client.post("/api/masquer", json=corps, headers=entete)
    with base(client) as b:
        assert b.one("SELECT COUNT(*) n FROM hides WHERE siren = '848902672'")["n"] == 1


def test_statut_pipeline_valide(client, monkeypatch):
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise("848902672", "CARACOLE COIFFURE")]))
    connexion(client)
    entete = {"X-CSRF-Token": jeton(client)}
    client.post("/api/suivre", json={"snapshot": entreprise("848902672", "CARACOLE COIFFURE")},
                headers=entete)
    assert client.post("/api/statut", json={"siren": "848902672", "statut": "client"},
                       headers=entete).status_code == 200
    assert client.post("/api/statut", json={"siren": "848902672", "statut": "n_importe_quoi"},
                       headers=entete).status_code == 400
    with base(client) as b:
        assert b.one("SELECT status FROM tracked WHERE siren = '848902672'")["status"] == "client"


# --------------------------------------------------------------------------
# Detection de site
# --------------------------------------------------------------------------

def test_variantes_de_domaine():
    variantes = detect._variantes("SARL Boulangerie Perrin")
    assert "boulangerieperrin" in variantes
    assert "boulangerie-perrin" in variantes
    assert all("sarl" not in variante for variante in variantes)


def test_variantes_enseigne():
    variantes = detect._variantes("DUPONT ET FILS", "Le Fournil d'Alice")
    assert "fournilalice" in variantes


def test_enseigne_remplie_depuis_l_api():
    brut = {
        "siren": "848902672", "nom_complet": "CARACOLE COIFFURE", "nom_raison_sociale": None,
        "sigle": None, "nature_juridique": "5710", "etat_administratif": "A",
        "activite_principale": "96.02A", "section_activite_principale": "S",
        "tranche_effectif_salarie": "01", "date_creation": "2019-04-01",
        "siege": {"siret": "84890267200012", "code_postal": "44600",
                  "libelle_commune": "SAINT-NAZAIRE", "departement": "44", "region": "52",
                  "liste_enseignes": ["LE FOURNIL D'ALICE"], "nom_commercial": None},
        "complements": {}, "dirigeants": [],
    }
    assert gov_api.normalize_result(brut)["enseigne"] == "LE FOURNIL D'ALICE"
    brut["siege"]["nom_commercial"] = "Caracole Coiffure"
    assert gov_api.normalize_result(brut)["enseigne"] is None  # identique a la denomination
    assert gov_api.normalize_result({"siege": {}, "siren": "1"})["enseigne"] is None


def test_site_officiel_dans_les_resultats(client, monkeypatch):
    monkeypatch.setattr(detect, "_resout", lambda host: host == "caracolecoiffure.fr")
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise("848902672", "CARACOLE COIFFURE")]))
    connexion(client)
    html = client.get("/?q=coiffure").get_data(as_text=True)
    assert "caracolecoiffure.fr" in html


# --------------------------------------------------------------------------
# Pagination du filtre "sans site"
# --------------------------------------------------------------------------

def _catalogue(page):
    debut = (page - 1) * 25
    return [entreprise(f"1000000{debut + i:02d}", f"ENTREPRISE {debut + i}") for i in range(25)]


def test_pagination_sans_site_stable(client, monkeypatch, app):
    monkeypatch.setattr(gov_api, "search", faux_search(_catalogue, total_results=75, total_pages=3))
    connexion(client)
    page1 = snapshots(client.get("/?q=coiffure&sans_site=1&page=1").get_data(as_text=True))
    page2 = snapshots(client.get("/?q=coiffure&sans_site=1&page=2").get_data(as_text=True))
    assert len(page1) == 25 and len(page2) == 25
    sirens1 = {item["siren"] for item in page1}
    sirens2 = {item["siren"] for item in page2}
    assert not sirens1 & sirens2
    assert min(sirens2) > max(sirens1)  # page 2 = la suite de la page 1


def test_page_hors_limites_ramenee_a_la_derniere(client, monkeypatch):
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise("848902672", "CARACOLE COIFFURE")], total_pages=1))
    connexion(client)
    html = client.get("/?q=coiffure&sans_site=1&page=4").get_data(as_text=True)
    assert len(snapshots(html)) == 1
    assert "Page 1 sur 1" in html


# --------------------------------------------------------------------------
# Mode demonstration et export
# --------------------------------------------------------------------------

def _api_hors_service(*args, **kwargs):
    raise gov_api.ApiError("injoignable")


def test_base_injoignable_aucune_donnee_inventee(client, monkeypatch):
    """Sans demande explicite : erreur claire, jamais d'entreprises fictives."""
    monkeypatch.setattr(gov_api, "search", _api_hors_service)
    connexion(client)
    html = client.get("/?q=coiffure&departement=44").get_data(as_text=True)
    assert "injoignable" in html
    assert "données de démonstration" not in html
    assert "result-row" not in html                      # aucun resultat affiche
    assert "CARACOLE COIFFURE" not in html               # le jeu fictif n'est pas servi


def test_jeu_de_demonstration_sur_demande_client(app_demo):
    """Avec MBDV_DEMO=1 et ?demo=1 : jeu fictif affiche, et clairement annonce."""
    client = app_demo.test_client()
    connexion(client)
    html = client.get("/?q=coiffure&departement=44&demo=1").get_data(as_text=True)
    assert "CARACOLE COIFFURE" in html
    assert "ne sont pas réels" in html


def test_jeu_de_demonstration_refuse_sans_option(client, monkeypatch):
    """?demo=1 sans MBDV_DEMO : la base reste la seule source."""
    monkeypatch.setattr(gov_api, "search", _api_hors_service)
    connexion(client)
    html = client.get("/?q=coiffure&departement=44&demo=1").get_data(as_text=True)
    assert "injoignable" in html
    assert "CARACOLE COIFFURE" not in html


def test_jeu_de_demonstration_couvre_l_ile_de_france():
    from app import demo_data
    for departement in ("75", "92", "93", "94"):
        assert demo_data.cherche(departement=departement)["items"], departement
    assert len(demo_data.DEPARTEMENTS) == len(set(demo_data.DEPARTEMENTS))
    assert sorted(demo_data.DEPARTEMENTS) == demo_data.DEPARTEMENTS


def test_jeu_de_demonstration_coherent():
    from app import demo_data
    sirens = [e["siren"] for e in demo_data.DEMO_COMPANIES]
    assert len(sirens) == len(set(sirens))                      # pas de doublon
    assert all(len(s) == 9 and s.isdigit() for s in sirens)
    assert all(e["naf_label"] for e in demo_data.DEMO_COMPANIES)  # activite lisible
    assert all(e["enseigne"] is None or isinstance(e["enseigne"], str)
               for e in demo_data.DEMO_COMPANIES)
    example = demo_data.DEMO_COMPANIES[0]
    assert set(example) == set(entreprise("000000000", "X"))     # meme contrat que l'API
    # les fiches rendues sont des copies : le jeu partage ne doit pas etre enrichi
    fiche = demo_data.par_siren(example["siren"])
    fiche["site"] = {"status": "aucun"}
    assert "site" not in demo_data.par_siren(example["siren"])
    assert "site" not in demo_data.cherche(q="")["items"][0]


def test_resultat_vide_en_demonstration_explique_la_limite(app_demo):
    client = app_demo.test_client()
    connexion(client)
    html = client.get("/?departement=2A&q=coiffure&demo=1").get_data(as_text=True)
    assert "Aucune entreprise fictive dans cette zone" in html      # pas de faux vide
    assert "jeu de démonstration" in html
    assert "Aucune n'est située dans le département 2A" in html
    assert "Département 94" in html                                  # raccourcis proposes
    assert "Aucune entreprise ne correspond" not in html              # message reel absent


def test_export_csv_refuse_sans_base(client, monkeypatch):
    """La base est injoignable : l'export echoue au lieu d'exporter du fictif."""
    monkeypatch.setattr(gov_api, "search", _api_hors_service)
    connexion(client)
    reponse = client.get("/export.csv?q=coiffure&departement=44")
    assert reponse.status_code == 503
    assert "CARACOLE COIFFURE" not in reponse.get_data(as_text=True)


def test_export_csv_signale_la_demonstration(app_demo):
    client = app_demo.test_client()
    connexion(client)
    reponse = client.get("/export.csv?q=coiffure&departement=44&demo=1")
    corps = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    assert "Source des données" in corps
    assert "Jeu de démonstration" in corps
    assert "DEMO" in reponse.headers["Content-Disposition"]


def test_export_csv_officiel(client, monkeypatch):
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise("848902672", "CARACOLE COIFFURE")]))
    connexion(client)
    reponse = client.get("/export.csv?q=coiffure")
    corps = reponse.get_data(as_text=True)
    assert "Base officielle INSEE / RNE" in corps
    assert "DEMO" not in reponse.headers["Content-Disposition"]
    assert "CARACOLE COIFFURE" in corps


# --------------------------------------------------------------------------
# Interface : interrupteurs et theme clair / sombre
# --------------------------------------------------------------------------

def test_interrupteurs_pilotent_leur_etat_visuel(client, monkeypatch):
    """Le style doit suivre la case cochee (:has(input:checked)), pas seulement
    la classe rendue par le serveur : c'est ce qui donnait l'impression que les
    interrupteurs ne repondaient pas au clic."""
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise("848902672", "CARACOLE COIFFURE")]))
    connexion(client)
    html = client.get("/?q=coiffure").get_data(as_text=True)
    assert 'name="sans_site"' in html and 'name="inclure_masquees"' in html
    assert 'name="inclure_fermees"' in html
    # aucun interrupteur actif : pas de classe "on"
    assert '<label class="toggle ">' in html or '<label class="toggle">' in html

    coche = client.get("/?q=coiffure&sans_site=1&inclure_fermees=1").get_data(as_text=True)
    assert coche.count('class="toggle on"') == 2
    assert coche.count('checked') >= 2

    css = client.get("/static/css/main.css").get_data(as_text=True)
    assert ".toggle:has(input:checked)" in css
    assert ".toggle:has(input:checked) .toggle-box::after" in css
    assert "opacity: 0" in css.split(".toggle input {")[1][:200]


def test_bouton_de_theme_present_et_javascript(client):
    connexion(client)
    html = client.get("/").get_data(as_text=True)
    assert "data-theme-toggle" in html
    assert 'meta name="color-scheme"' in html
    assert "mbdv-theme" in html                     # choix memorise avant peinture
    js = client.get("/static/js/app.js").get_data(as_text=True)
    assert "appliquerTheme" in js
    assert "prefers-color-scheme" in js
    css = client.get("/static/css/main.css").get_data(as_text=True)
    assert 'html[data-theme="dark"]' in css
    for jeton in ("--paper:", "--card:", "--ink:", "--accent:", "--line:"):
        assert jeton in css.split('html[data-theme="dark"]')[1]


def test_bouton_de_theme_sur_la_page_de_connexion(client):
    html = client.get("/connexion").get_data(as_text=True)
    assert "data-theme-toggle" in html and "mbdv-theme" in html


def test_couleurs_en_dur_absentes_des_regles_hors_theme():
    """Les regles hors bloc de theme ne doivent plus coder les couleurs en dur."""
    from pathlib import Path
    css = Path("app/static/css/main.css").read_text(encoding="utf-8")
    # on retire les deux blocs de jetons : ce sont eux qui portent les couleurs
    clair = re.sub(r":root \{.*?\n\}", "", css, count=1, flags=re.S)
    clair = clair.split("   Theme sombre")[0]
    for couleur in ("#b3a996", "#d8d0bf", "#fbf7ee", "#f0dcd0", "#7c2a17"):
        assert couleur not in clair, couleur


# --------------------------------------------------------------------------
# Base de donnees
# --------------------------------------------------------------------------

INSERTION = "INSERT INTO overrides (siren, value, by, at) VALUES (?, 'sans', 'test', '2026')"


def test_run_all_est_transactionnel(app):
    with app.app_context():
        db.execute(INSERTION, ("111111111",))
        with pytest.raises(sqlite3.OperationalError):
            db.run_all([
                (INSERTION, ("222222222",)),
                ("INSERT INTO table_inexistante (x) VALUES (1)", ()),
            ])
        assert db.one("SELECT 1 FROM overrides WHERE siren = '222222222'") is None
        assert db.one("SELECT 1 FROM overrides WHERE siren = '111111111'") is not None


def test_mot_de_passe_change(app, client):
    connexion(client)
    entete = {"X-CSRF-Token": jeton(client)}
    reponse = client.post("/mot-de-passe",
                          data={"actuel": "MBDV-admin-2026", "nouveau": "NouveauMotDePasse1",
                                "confirmation": "NouveauMotDePasse1", "_csrf": entete["X-CSRF-Token"]})
    assert reponse.status_code == 200
    # la session courante a change de jeton a la connexion : on en reprend un
    client.post("/deconnexion", data={"_csrf": jeton(client)})
    assert connexion(client, password="NouveauMotDePasse1").status_code == 302
    # l'ancien mot de passe ne fonctionne plus (client neuf : le premier est connecte)
    assert connexion(app.test_client(), password="MBDV-admin-2026").status_code == 200


def _aujourd_hui_paris() -> str:
    """Date du jour a Paris : c'est elle qui classe les retards et les relances."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Paris")).strftime("%Y-%m-%d")

# --------------------------------------------------------------------------
# Suivi d'equipe : qui travaille quoi, et quels appels passer
# --------------------------------------------------------------------------

def _suivie(client, siren, nom, statut="a_contacter", **champs):
    """Ajoute une entreprise au portefeuille, comme le fait l'API /api/suivre."""
    colonnes = {"siren": siren, "snapshot": json.dumps(entreprise(siren, nom)),
                "status": statut, "added_by": "admin", "added_at": "2026-01-01T00:00:00Z"}
    colonnes.update(champs)
    noms = ", ".join(colonnes)
    marques = ", ".join("?" for _ in colonnes)
    with base(client) as b:
        # Colonnes litterales du test, valeurs parametrees : aucune donnee externe ici.
        requete = f"INSERT INTO tracked ({noms}) VALUES ({marques})"  # noqa: S608
        b.execute(requete, tuple(colonnes.values()))


def _suivi_json(client, url, corps, entete=None):
    return client.post(url, json=corps,
                       headers=entete or {"X-CSRF-Token": jeton(client, "/suivi")})


def test_suivi_protege_par_la_connexion(client):
    assert client.get("/suivi").status_code == 302
    assert "/connexion" in client.get("/suivi").headers["Location"]


def test_suivi_est_un_repertoire_une_ligne_par_entreprise(client):
    """Un tableau simple : une ligne par entreprise en attente, rien de plus."""
    connexion(client)
    _suivie(client, "111111111", "Alpha Coiffure", "a_contacter")
    _suivie(client, "222222222", "Beta Menuiserie", "client")          # plus d'appel
    _suivie(client, "333333333", "Gamma Toiture", "sans_suite")        # classe
    page = client.get("/suivi").get_data(as_text=True)

    assert "Appels à passer" in page
    assert "En attente d'appel (1)" in page
    assert "Alpha Coiffure" in page
    assert "Beta Menuiserie" not in page        # cliente : rien a appeler
    assert "Gamma Toiture" not in page          # classee : rien a appeler
    # les colonnes essentielles, pour decider d'un appel d'un seul regard
    for attendu in ("Entreprise", "Activité", "Site web", "Référent",
                    "Dernier appel", "À rappeler", "Appel passé"):
        assert attendu in page, attendu
    # une seule ligne de tableau, porteuse de tout ce qu'il faut
    assert page.count('class="result-row"') == 1
    assert 'data-siren="111111111"' in page
    assert "111111111" in page and "Coiffure" in page
    assert '<span class="cell-soft" data-dernier>—</span>' in page
    assert 'data-appels>—<' in page
    # le reste se fait sur la ligne : prendre, appel passe ; le dossier est a un clic
    assert 'data-prendre="111111111"' in page and 'data-mine="0"' in page
    assert 'data-appel="111111111"' in page
    assert 'data-detail="111111111"' in page
    # l'ancien affichage en cartes a disparu
    for disparu in ('class="appel-carte"', 'class="equipe-card"', "appel-dossier",
                    'data-form="appel"'):
        assert disparu not in page, disparu


def test_suivi_ordonne_les_retards_en_tete(client):
    """Retard d'abord, puis aujourd'hui, puis les relances a venir."""
    connexion(client)
    jour = _aujourd_hui_paris()
    _suivie(client, "111111111", "AAAA Sans date")
    _suivie(client, "222222222", "BBBB Relance future", relance_le="2027-01-04")
    _suivie(client, "333333333", "CCCC En retard", relance_le="2026-01-05")
    _suivie(client, "444444444", "DDDD Aujourd hui", relance_le=jour)
    page = client.get("/suivi").get_data(as_text=True)

    positions = [page.find(nom) for nom in
                 ("CCCC En retard", "DDDD Aujourd hui", "BBBB Relance future", "AAAA Sans date")]
    assert all(p > 0 for p in positions), positions
    assert positions == sorted(positions), positions
    assert "En retard de" in page
    assert "À appeler aujourd’hui" in page


def test_prise_en_charge_partagee_entre_collegues(app):
    """Un referent a la fois : le collegue voit la prise, et lui seul peut la retirer."""
    chef = app.test_client()
    connexion(chef)
    associe = app.test_client()
    connexion(associe, username="associe", password="MBDV-associe-2026")  # noqa: S106
    _suivie(chef, "111111111", "Alpha Coiffure")

    # l'associe voit le tableau de bord (ce n'est pas une page reservee au dirigeant)
    assert associe.get("/suivi").status_code == 200

    # l'associe prend l'entreprise
    reponse = _suivi_json(associe, "/api/suivi/prendre", {"siren": "111111111"})
    assert reponse.status_code == 200, reponse.get_data(as_text=True)
    etat = reponse.get_json()
    assert etat["pris_par"] == "associe" and etat["prise_par_moi"] is True
    assert etat["pris_label"].startswith("Prise par vous")
    with base(chef) as b:
        assert b.one("SELECT pris_par FROM tracked WHERE siren = ?",
                     ("111111111",))["pris_par"] == "associe"

    # le dirigeant voit de qui il s'agit, et peut la reprendre pour lui
    page = chef.get("/suivi").get_data(as_text=True)
    assert "Prise par associe" in page
    etat = _suivi_json(chef, "/api/suivi/prendre", {"siren": "111111111"}).get_json()
    assert etat["pris_par"] == "admin" and "vous" in etat["pris_label"]

    # un associe ne peut pas retirer la prise d'un autre
    refus = _suivi_json(associe, "/api/suivi/prendre",
                        {"siren": "111111111", "prendre": False})
    assert refus.status_code == 409
    assert "admin" in refus.get_json()["error"]

    # le dirigeant, si : l'entreprise repart dans la file commune
    laisse = _suivi_json(chef, "/api/suivi/prendre", {"siren": "111111111", "prendre": False})
    assert laisse.status_code == 200
    assert laisse.get_json()["pris_label"] == "Personne pour l’instant"
    with base(chef) as b:
        ligne = b.one("SELECT pris_par, pris_le FROM tracked WHERE siren = ?", ("111111111",))
    assert ligne["pris_par"] == "" and ligne["pris_le"] is None


def test_appel_enregistre_avec_compte_rendu_et_relance(client):
    """Un appel passe : compteur, date, auteur, compte rendu, prochain appel."""
    connexion(client)
    _suivie(client, "111111111", "Alpha Coiffure", note="numero a retrouver")
    etat = _suivi_json(client, "/api/suivi/appel",
                       {"siren": "111111111", "note": "Joint le gerant, devis a envoyer",
                        "relance_le": "2026-10-05"}).get_json()

    assert etat["appels"] == 1
    assert etat["statut"] == "contacte" and etat["statut_label"] == "Contacté"
    assert etat["note"] == "Joint le gerant, devis a envoyer"
    assert etat["historique"].startswith("1 appel(s)")
    assert "prochain le 05/10/2026" in etat["historique"]
    assert etat["urgence"] == "relance" and etat["urgence_label"] == "Relance le 05/10/2026"
    assert etat["prise_par_moi"] is True                    # l'appel attribue l'entreprise
    assert etat["pris_label"].startswith("Prise par vous")
    assert etat["badge_html"].startswith("<span class=\"badge")
    # la ligne du repertoire est decrite par ces libelles, deja calcules
    assert etat["referent_html"].startswith("<span class=\"cell-strong\">vous</span>")
    jour = _aujourd_hui_paris()                       # AAAA-MM-JJ
    assert etat["dernier_appel"] == f"{jour[8:10]}/{jour[5:7]}/{jour[:4]}"
    assert etat["appels_label"] == "1 appel(s)"

    with base(client) as b:
        ligne = b.one("SELECT * FROM tracked WHERE siren = ?", ("111111111",))
    assert ligne["appels"] == 1
    assert ligne["appele_par"] == "admin"
    assert ligne["appele_le"].startswith(_aujourd_hui_paris())
    assert ligne["relance_le"] == "2026-10-05"
    assert ligne["pris_par"] == "admin"
    assert ligne["status_updated_by"] == "admin"

    # deuxieme appel : le compteur continue, sans effacer le premier
    etat = _suivi_json(client, "/api/suivi/appel", {"siren": "111111111"}).get_json()
    assert etat["appels"] == 2


def test_appel_ne_fait_jamais_reculer_le_statut(client):
    """Une entreprise deja en discussion (ou cliente) n'est pas ramenee a « Contacté »."""
    connexion(client)
    _suivie(client, "111111111", "Alpha Coiffure", statut="discussion")
    _suivie(client, "222222222", "Beta Menuiserie", statut="client")
    _suivi_json(client, "/api/suivi/appel", {"siren": "111111111"})
    _suivi_json(client, "/api/suivi/appel", {"siren": "222222222"})
    with base(client) as b:
        statuts = {r["siren"]: r["status"] for r in b.query("SELECT siren, status FROM tracked")}
    assert statuts == {"111111111": "discussion", "222222222": "client"}


def test_suivi_refuse_les_saisies_invalides(client):
    """Relance illisible, entreprise inconnue : rien n'est enregistre."""
    connexion(client)
    _suivie(client, "111111111", "Alpha Coiffure")
    entete = {"X-CSRF-Token": jeton(client, "/suivi")}

    for mauvaise in ("demain", "2026-13-45", "05/10/2026"):
        reponse = client.post("/api/suivi/appel", json={"siren": "111111111",
                                                        "relance_le": mauvaise}, headers=entete)
        assert reponse.status_code == 400, mauvaise
    # le retrait d'une prise sur une entreprise non suivie est refuse aussi
    assert client.post("/api/suivi/prendre", json={"siren": "999999999"},
                       headers=entete).status_code == 404
    assert client.post("/api/suivi/note", json={"siren": "999999999"},
                       headers=entete).status_code == 404
    with base(client) as b:
        ligne = b.one("SELECT appels, relance_le FROM tracked WHERE siren = ?", ("111111111",))
    assert ligne["appels"] == 0 and ligne["relance_le"] is None


def test_note_d_equipe_sans_appel(client):
    """La note se modifie sans compter d'appel ni changer le statut."""
    connexion(client)
    _suivie(client, "111111111", "Alpha Coiffure")
    etat = _suivi_json(client, "/api/suivi/note",
                       {"siren": "111111111", "note": "Telephone : 02 40 00 00 00"}).get_json()
    assert etat["note"] == "Telephone : 02 40 00 00 00"
    assert etat["appels"] == 0
    assert etat["historique"] == "Aucun appel enregistré"
    assert etat["statut"] == "a_contacter"
    assert etat["appels_label"] == "—" and etat["dernier_appel"] == "—"
    # la note part au formulaire d'appel de la ligne (relecture avant l'appel)
    page = client.get("/suivi").get_data(as_text=True)
    assert "Telephone : 02 40 00 00 00" in page
    # la note reste modifiable et suit le compte rendu du prochain appel
    etat = _suivi_json(client, "/api/suivi/appel",
                       {"siren": "111111111", "note": "Joint le gerant"}).get_json()
    assert etat["note"] == "Joint le gerant"


def test_suivi_masque_les_entreprises_masquees(client):
    """Une entreprise masquee quitte la file d'appels, mais reste dans « qui travaille quoi »."""
    connexion(client)
    _suivie(client, "111111111", "Alpha Coiffure", pris_par="admin", pris_le="2026-01-02T09:00:00Z")
    _suivie(client, "222222222", "Beta Menuiserie")
    with base(client) as b:
        b.execute("INSERT INTO hides (siren, nom, raison, details, hidden_by, hidden_at)"
                  " VALUES ('222222222', 'Beta Menuiserie', 'Doublon', '', 'admin',"
                  " '2026-01-03T09:00:00Z')")
    page = client.get("/suivi").get_data(as_text=True)
    assert "Alpha Coiffure" in page and "Beta Menuiserie" not in page
    assert "En attente d'appel (1)" in page
    assert "Charge de l'équipe" in page        # la charge reste visible en une ligne


def test_migration_des_colonnes_de_suivi(tmp_path, monkeypatch):
    """Une base ancienne gagne les colonnes du suivi sans perdre ses entreprises."""
    monkeypatch.setenv("MBDV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(detect, "_resout", lambda host: False)
    _api_officielle_indisponible(monkeypatch)
    dossier = tmp_path / "data"
    dossier.mkdir(parents=True)
    ancienne = sqlite3.connect(dossier / "mbdv.sqlite3")
    ancienne.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE tracked (id INTEGER PRIMARY KEY AUTOINCREMENT, siren TEXT UNIQUE NOT NULL,
            snapshot TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'a_contacter',
            note TEXT NOT NULL DEFAULT '', added_by TEXT NOT NULL, added_at TEXT NOT NULL,
            status_updated_by TEXT, status_updated_at TEXT);
        INSERT INTO tracked (siren, snapshot, status, added_by, added_at)
            VALUES ('111111111', '{"siren": "111111111", "nom": "Alpha"}', 'contacte',
                    'admin', '2026-01-01T00:00:00Z');
    """)
    ancienne.commit()
    ancienne.close()

    application = create_app()
    application.config.update(TESTING=True)
    with application.app_context():
        colonnes = {ligne["name"] for ligne in db.query("PRAGMA table_info(tracked)")}
        for nom in ("pris_par", "pris_le", "appele_le", "appele_par", "appels", "relance_le"):
            assert nom in colonnes, nom
        ligne = db.one("SELECT * FROM tracked WHERE siren = ?", ("111111111",))
        assert ligne["status"] == "contacte"          # rien n'est perdu
        assert ligne["pris_par"] == "" and ligne["appels"] == 0
        assert ligne["relance_le"] is None
# --------------------------------------------------------------------------
# Repertoire : ajouter une entreprise, ranger ses livrables
# --------------------------------------------------------------------------

def test_repertoire_vide_invite_a_ajouter(client):
    """Sans entreprise suivie : un message clair et le bouton d'ajout, rien de fictif."""
    connexion(client)
    page = client.get("/suivi").get_data(as_text=True)
    assert "Le répertoire est vide" in page
    assert page.count("data-ajouter") == 2          # en-tete et etat vide
    assert "Ajouter une entreprise" in page
    assert "COIFFURE" not in page.upper()            # aucune entreprise inventee
    # l'etat vide explique ce qu'on pourra ranger : dossier, .zip, vitrine
    assert "dossier du site" in page and ".zip" in page and "URL de la vitrine" in page
    assert "table" not in page.split("</section>")[0][-200:]   # aucune ligne fantome

    # des qu'une entreprise existe, la colonne des livrables apparait, vide au depart
    _suivie(client, "111111111", "Alpha Coiffure")
    page = client.get("/suivi").get_data(as_text=True)
    assert "Livrables" in page
    assert '<td class="col-livrables" data-livrables-cellule>' in page
    assert 'data-livrables="111111111"' in page
    assert "Ajouter les livrables" in page        # l'invite, tant qu'il n'y a rien


def test_annuaire_cherche_dans_la_base_officielle(client, monkeypatch):
    """La recherche d'ajout interroge l'API officielle et signale ce qui est deja suivi."""
    connexion(client)
    monkeypatch.setattr(gov_api, "search", faux_search(
        lambda page: [entreprise("111111111", "Alpha Coiffure"),
                      entreprise("222222222", "Beta Menuiserie")]))
    _suivie(client, "222222222", "Beta Menuiserie")

    reponse = client.get("/api/annuaire?q=coiffure")
    assert reponse.status_code == 200
    corps = reponse.get_json()
    assert corps["ok"] is True and len(corps["resultats"]) == 2
    par_siren = {r["siren"]: r for r in corps["resultats"]}
    assert par_siren["111111111"]["deja"] is False
    assert par_siren["222222222"]["deja"] is True       # deja dans le repertoire
    # la fiche reprise sert a reconnaitre l'entreprise sans l'ouvrir
    for champ in ("nom", "commune", "naf_label", "effectif", "adresse"):
        assert champ in par_siren["111111111"], champ

    # recherche trop courte : refusee, rien n'est interroge
    assert client.get("/api/annuaire?q=a").status_code == 400
    assert client.get("/api/annuaire").status_code == 400


def test_annuaire_signale_la_base_indisponible(client, monkeypatch):
    """Base officielle injoignable : on le dit, et la saisie manuelle est proposee."""
    connexion(client)

    def echoue(**kwargs):
        raise gov_api.ApiError("injoignable")

    monkeypatch.setattr(gov_api, "search", echoue)
    corps = client.get("/api/annuaire?q=coiffure").get_json()
    assert corps["ok"] is False and corps["indisponible"] is True
    assert "à la main" in corps["error"]


def test_ajout_au_repertoire_par_siren(client, monkeypatch):
    """Ajouter par SIREN reprend la fiche officielle et attribue la ligne a l'agent."""
    connexion(client)
    monkeypatch.setattr(gov_api, "fetch_by_siren",
                        lambda siren: entreprise(siren, "Alpha Coiffure"))
    reponse = _suivi_json(client, "/api/suivi/ajouter", {"siren": "111111111"})
    assert reponse.status_code == 200, reponse.get_data(as_text=True)
    etat = reponse.get_json()
    assert etat["ok"] is True and etat["prise_par_moi"] is True     # je m'en occupe
    assert etat["statut"] == "a_contacter"
    assert etat["pris_label"].startswith("Prise par vous")
    with base(client) as b:
        ligne = b.one("SELECT * FROM tracked WHERE siren = ?", ("111111111",))
    assert ligne["pris_par"] == "admin"
    assert json.loads(ligne["snapshot"])["nom"] == "Alpha Coiffure"

    # la ligne apparait dans le repertoire, avec sa cellule de livrables vide
    page = client.get("/suivi").get_data(as_text=True)
    assert "Alpha Coiffure" in page
    assert 'data-livrables="111111111"' in page
    assert "En attente d'appel (1)" in page
    # ajouter deux fois la meme entreprise est refuse
    assert _suivi_json(client, "/api/suivi/ajouter",
                       {"siren": "111111111"}).status_code == 409


def test_ajout_manuel_si_la_base_ne_repond_pas(client):
    """Sans fiche officielle : le nom saisi suffit, et rien d'autre n'est invente."""
    connexion(client)                                # API doublee en echec par la fixture
    # sans nom : refus, avec un message qui explique quoi faire
    reponse = _suivi_json(client, "/api/suivi/ajouter", {"siren": "333333333"})
    assert reponse.status_code == 400
    assert "indiquez au moins son nom" in reponse.get_json()["error"]

    reponse = _suivi_json(client, "/api/suivi/ajouter",
                          {"siren": "333 333 333", "nom": "Gamma Toiture",
                           "commune": "Nantes", "activite": "Couverture"})
    assert reponse.status_code == 200
    with base(client) as b:
        fichier = json.loads(b.one("SELECT snapshot FROM tracked WHERE siren = ?",
                                   ("333333333",))["snapshot"])
    assert fichier["nom"] == "Gamma Toiture"
    assert fichier["commune"] == "Nantes" and fichier["naf_label"] == "Couverture"
    assert fichier["source"] == "saisie"             # provenance affichee, pas de faux
    # SIREN invalide : refuse
    assert _suivi_json(client, "/api/suivi/ajouter",
                       {"siren": "12", "nom": "X"}).status_code == 400


def test_livrables_dossier_zip_et_url_de_vitrine(client):
    """Les trois livrables se rangent sur la ligne, avec les verifications d'usage."""
    connexion(client)
    _suivie(client, "111111111", "Alpha Coiffure")
    reponse = _suivi_json(client, "/api/suivi/livrables", {
        "siren": "111111111",
        "dossier_site": "//serveur/projets/alpha/vitrine",
        "zip_lien": "https://exemple.fr/alpha.zip",
        "url_vitrine": "https://alpha.exemple.fr",
    })
    assert reponse.status_code == 200, reponse.get_data(as_text=True)
    etat = reponse.get_json()
    assert "alpha.exemple.fr" in etat["livrables_html"]
    assert "Dossier" in etat["livrables_html"]
    assert "alpha.zip" in etat["livrables_html"]

    with base(client) as b:
        ligne = b.one("SELECT * FROM tracked WHERE siren = ?", ("111111111",))
    assert ligne["dossier_site"] == "//serveur/projets/alpha/vitrine"
    assert ligne["url_vitrine"] == "https://alpha.exemple.fr"
    assert ligne["zip_lien"] == "https://exemple.fr/alpha.zip"
    assert ligne["livrables_maj_le"]

    # la page affiche les livrables de la ligne
    page = client.get("/suivi").get_data(as_text=True)
    assert 'href="https://alpha.exemple.fr"' in page
    assert "alpha.zip" in page

    # une vitrine saisie sans http prend le https ; une adresse fantaisiste est refusee
    etat = _suivi_json(client, "/api/suivi/livrables",
                       {"siren": "111111111", "url_vitrine": "alpha.exemple.fr"}).get_json()
    assert etat["livrables_html"].count("alpha.exemple.fr") >= 1
    with base(client) as b:
        assert b.one("SELECT url_vitrine FROM tracked WHERE siren = ?",
                     ("111111111",))["url_vitrine"] == "https://alpha.exemple.fr"
    for mauvais in ("pas une url", "javascript:alert(1)", "fichier local"):
        refus = _suivi_json(client, "/api/suivi/livrables",
                            {"siren": "111111111", "url_vitrine": mauvais})
        assert refus.status_code == 400, mauvais
    assert _suivi_json(client, "/api/suivi/livrables",
                       {"siren": "999999999", "url_vitrine": "https://x.fr"}).status_code == 404


def test_depot_et_telechargement_de_l_archive(client):
    """Le .zip du site se depose depuis la ligne et se retelcharge tel quel."""
    connexion(client)
    _suivie(client, "111111111", "Alpha Coiffure")
    # une archive minimale mais valide (signature PK)
    archive = b"PK\x03\x04" + b"contenu du site" * 4
    reponse = client.post("/suivi/livrables/zip", data={
        "siren": "111111111", "fichier": (io.BytesIO(archive), "vitrine-alpha.zip"),
    }, content_type="multipart/form-data",
        headers={"X-CSRF-Token": jeton(client, "/suivi")})
    assert reponse.status_code == 200, reponse.get_data(as_text=True)
    corps = reponse.get_json()
    assert corps["ok"] is True and "vitrine-alpha.zip" in corps["message"]
    assert "vitrine-alpha.zip" in corps["livrables_html"]
    with base(client) as b:
        ligne = b.one("SELECT zip_nom, zip_lien FROM tracked WHERE siren = ?", ("111111111",))
    assert ligne["zip_nom"] == "vitrine-alpha.zip" and ligne["zip_lien"] == ""

    # le fichier se recupere depuis le repertoire
    telechargement = client.get("/suivi/livrables/zip/111111111")
    assert telechargement.status_code == 200
    assert telechargement.data == archive
    assert "vitrine-alpha.zip" in telechargement.headers["Content-Disposition"]

    # une autre archive remplace la precedente
    nouvelle = b"PK\x03\x04" + b"version 2"
    client.post("/suivi/livrables/zip", data={
        "siren": "111111111", "fichier": (io.BytesIO(nouvelle), "vitrine-alpha-v2.zip"),
    }, content_type="multipart/form-data",
        headers={"X-CSRF-Token": jeton(client, "/suivi")})
    assert client.get("/suivi/livrables/zip/111111111").data == nouvelle


def test_depot_refuse_ce_qui_n_est_pas_une_archive(client, monkeypatch):
    """Extension, signature, taille et entreprise : chaque refus a son message."""
    connexion(client)
    monkeypatch.setattr(views, "ZIP_MAX_OCTETS", 4096)   # plafond abaisse pour le test
    _suivie(client, "111111111", "Alpha Coiffure")
    entete = {"X-CSRF-Token": jeton(client, "/suivi")}

    def depose(nom, contenu, siren="111111111"):
        return client.post("/suivi/livrables/zip", data={
            "siren": siren, "fichier": (io.BytesIO(contenu), nom),
        }, content_type="multipart/form-data", headers=entete)

    refus = depose("notes.txt", b"PK\x03\x04du texte")
    assert refus.status_code == 400 and ".zip" in refus.get_json()["error"]
    refus = depose("photo.zip", b"pas une archive")
    assert refus.status_code == 400 and "n'est pas une archive" in refus.get_json()["error"]
    refus = depose("enorme.zip", b"PK\x03\x04" + b"x" * 4096)
    assert refus.status_code == 400 and "trop lourde" in refus.get_json()["error"]
    assert depose("ok.zip", b"PK\x03\x04contenu", siren="999999999").status_code == 404
    assert client.post("/suivi/livrables/zip", data={"siren": "111111111"},
                       content_type="multipart/form-data",
                       headers=entete).status_code == 400   # aucun fichier choisi

    with base(client) as b:
        assert b.one("SELECT zip_nom FROM tracked WHERE siren = ?", ("111111111",))["zip_nom"] == ""
    assert client.get("/suivi/livrables/zip/111111111").status_code == 404


def test_l_archive_d_un_autre_collegue_est_accessible(client):
    """Le repertoire est commun : chacun recupere les livrables de l'equipe."""
    chef = client
    connexion(chef)
    _suivie(chef, "111111111", "Alpha Coiffure")
    with chef.application.app_context():
        chemin = os.path.join(views._dossier_livrables(), "111111111.zip")
        with open(chemin, "wb") as sortie:
            sortie.write(b"PK\x03\x04archive de l'equipe")
        db.execute("UPDATE tracked SET zip_nom = 'vitrine.zip' WHERE siren = ?", ("111111111",))

    associe = chef.application.test_client()
    connexion(associe, username="associe", password="MBDV-associe-2026")  # noqa: S106
    page = associe.get("/suivi").get_data(as_text=True)
    assert "vitrine.zip" in page
    assert associe.get("/suivi/livrables/zip/111111111").data == b"PK\x03\x04archive de l'equipe"
