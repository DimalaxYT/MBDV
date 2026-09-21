"""Tests de non-regression MBDV.

Aucun acces reseau : l'API officielle et les resolutions DNS sont remplacees par
des doubles de test, ce qui rend la suite rapide et deterministe.
"""
import json
import re
import sqlite3
from contextlib import contextmanager
from html.parser import HTMLParser

import pytest

from app import auth, create_app, db, detect, gov_api

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
    # Ni DNS ni API : tout est deterministe et hors ligne.
    monkeypatch.setattr(detect, "_resout", lambda host: False)
    auth._RATE.clear()
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def connexion(client, username="admin", password="MBDV-admin-2026",  # noqa: S107
              ip="127.0.0.1", **params):
    page = client.get("/connexion")
    token = re.search(r'name="_csrf" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    return client.post("/connexion", query_string=params,
                       data={"username": username, "password": password, "_csrf": token},
                       environ_base={"REMOTE_ADDR": ip})


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
        assert reponse.headers["Location"] == "/", cible


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


def test_recherche_bascule_en_demonstration_avec_bandeau(client, monkeypatch):
    monkeypatch.setattr(gov_api, "search", _api_hors_service)
    connexion(client)
    html = client.get("/?q=coiffure&departement=44").get_data(as_text=True)
    assert "données de démonstration" in html


def test_export_csv_signale_la_demonstration(client, monkeypatch):
    monkeypatch.setattr(gov_api, "search", _api_hors_service)
    connexion(client)
    reponse = client.get("/export.csv?q=coiffure&departement=44")
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
