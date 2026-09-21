"""Client de l'API officielle Recherche d'entreprises (recherche-entreprises.api.gouv.fr).

API publique du Ministere de l'Economie, donnees INSEE / RNE sous licence ouverte.
Aucune cle d'API n'est necessaire.
"""
import time
import unicodedata

import requests

BASE_URL = "https://recherche-entreprises.api.gouv.fr/search"
TIMEOUT = 15
PER_PAGE = 25


class ApiError(Exception):
    """Erreur d'appel a l'API officielle (reseau, rate limit, reponse invalide)."""


NATURE_JURIDIQUE = {
    "1000": "Entreprise individuelle",
    "1101": "Artisan-commerçant",
    "5202": "Société en nom collectif (SNC)",
    "5442": "Société à responsabilité limitée (SARL)",
    "5443": "EURL (SARL unipersonnelle)",
    "5499": "SARL (autre forme)",
    "5515": "Société anonyme à conseil d'administration",
    "5517": "Société anonyme à directoire",
    "5710": "Société par actions simplifiée (SAS)",
    "5720": "SAS unipersonnelle (SASU)",
    "6540": "Société civile de construction-vente",
    "6901": "Société civile immobilière (SCI)",
}

EFFECTIFS = {
    "NN": "Non renseigné",
    "00": "0 salarié",
    "01": "1 à 2 salariés",
    "02": "3 à 5 salariés",
    "03": "6 à 9 salariés",
    "11": "10 à 19 salariés",
    "12": "20 à 49 salariés",
    "21": "50 à 99 salariés",
    "22": "100 à 199 salariés",
    "31": "200 à 249 salariés",
    "32": "250 à 499 salariés",
    "41": "500 à 999 salariés",
    "42": "1 000 à 1 999 salariés",
    "51": "2 000 à 4 999 salariés",
    "52": "5 000 à 9 999 salariés",
    "53": "10 000 salariés et plus",
}

SECTIONS = {
    "A": "Agriculture, sylviculture et pêche",
    "B": "Industries extractives",
    "C": "Industrie manufacturière",
    "D": "Énergie (électricité, gaz)",
    "E": "Eau, assainissement, déchets",
    "F": "Construction (artisans du bâtiment)",
    "G": "Commerce et réparation automobile",
    "H": "Transports et entreposage",
    "I": "Hébergement et restauration",
    "J": "Information et communication",
    "K": "Finance et assurance",
    "L": "Immobilier",
    "M": "Activités spécialisées (conseil, ingénierie)",
    "N": "Services administratifs et de soutien",
    "O": "Administration publique",
    "P": "Enseignement",
    "Q": "Santé et action sociale",
    "R": "Arts, spectacles et loisirs",
    "S": "Autres services (coiffure, beauté...)",
    "T": "Ménages employeurs",
    "U": "Organisations extraterritoriales",
}

REGIONS = {
    "01": "Guadeloupe", "02": "Martinique", "03": "Guyane", "04": "La Réunion",
    "06": "Mayotte", "11": "Île-de-France", "24": "Centre-Val de Loire",
    "27": "Bourgogne-Franche-Comté", "28": "Normandie", "32": "Hauts-de-France",
    "44": "Grand Est", "52": "Pays de la Loire", "53": "Bretagne",
    "54": "Nouvelle-Aquitaine", "75": "Nouvelle-Aquitaine", "76": "Occitanie",
    "84": "Auvergne-Rhône-Alpes", "93": "Provence-Alpes-Côte d'Azur", "94": "Corse",
}

NAF_LABELS = {
    "01.11Z": "Culture de céréales et oléagineux",
    "02.40Z": "Services de sylviculture",
    "10.71C": "Boulangerie et boulangerie-pâtisserie",
    "10.71D": "Pâtisserie",
    "10.72Z": "Casse-croûte, restauration rapide à emporter",
    "41.20A": "Construction de bâtiments",
    "42.11A": "Construction de routes et autoroutes",
    "43.21A": "Travaux d'installation électrique",
    "43.22A": "Plomberie, installation d'eau et de gaz",
    "43.22B": "Chauffage et climatisation",
    "43.31A": "Travaux de plâtrerie",
    "43.32A": "Menuiserie métallique et serrurerie",
    "43.32B": "Menuiserie bois et PVC",
    "43.34A": "Travaux de peinture et vitrerie",
    "43.91A": "Maçonnerie et gros œuvre",
    "43.99C": "Maçonnerie générale",
    "45.11Z": "Commerce de voitures",
    "45.20A": "Entretien et réparation de véhicules",
    "47.11C": "Supérettes",
    "47.11E": "Supermarchés",
    "47.11F": "Hypermarchés",
    "47.71Z": "Commerce de détail d'habillement",
    "47.72A": "Commerce de détail de chaussures",
    "47.73Z": "Pharmacies",
    "47.75Z": "Parfumerie et produits de beauté",
    "47.77Z": "Horlogerie et bijouterie",
    "47.81Z": "Commerce alimentaire sur marchés",
    "49.32Z": "Taxis",
    "55.10Z": "Hôtels",
    "55.20Z": "Hébergement touristique (gîtes, chambres d'hôtes)",
    "55.30Z": "Campings",
    "56.10A": "Restauration traditionnelle",
    "56.10B": "Cafétérias et libres-services",
    "56.10C": "Restauration rapide",
    "56.21Z": "Traiteurs",
    "56.30Z": "Bars, débits de boissons",
    "62.01Z": "Programmation informatique",
    "68.20B": "Location de terrains et biens immobiliers",
    "68.31Z": "Agences immobilières",
    "68.32B": "Administration d'immeubles",
    "69.10Z": "Activités juridiques (avocats, notaires)",
    "69.20Z": "Activités comptables",
    "70.22Z": "Conseil de gestion",
    "81.21Z": "Nettoyage de bâtiments",
    "81.30Z": "Entretien d'espaces verts, paysagisme",
    "85.59A": "Formation continue d'adultes",
    "86.21Z": "Médecine générale et spécialisée",
    "86.23Z": "Chirurgiens-dentistes",
    "93.13Z": "Salles de sport et installations sportives",
    "95.23Z": "Cordonnerie, réparation de cuir",
    "96.01Z": "Blanchisserie, teinturerie, pressing",
    "96.02A": "Coiffure",
    "96.02B": "Soins de beauté, esthétique",
    "96.04Z": "Entretien corporel (sauna, massage)",
    "96.09Z": "Autres services personnels",
}


def _clean(value):
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# Passage prefixe NAF (2 chiffres) -> lettre de section (NAF rev. 2)
SECTION_PAR_PREFIXE = {
    "01": "A", "02": "A", "03": "A",
    "05": "B", "06": "B", "07": "B", "08": "B", "09": "B",
    "10": "C", "11": "C", "12": "C", "13": "C", "14": "C", "15": "C", "16": "C",
    "17": "C", "18": "C", "19": "C", "20": "C", "21": "C", "22": "C", "23": "C",
    "24": "C", "25": "C", "26": "C", "27": "C", "28": "C", "29": "C", "30": "C",
    "31": "C", "32": "C", "33": "C",
    "35": "D", "36": "E", "37": "E", "38": "E", "39": "E",
    "41": "F", "42": "F", "43": "F",
    "45": "G", "46": "G", "47": "G",
    "49": "H", "50": "H", "51": "H", "52": "H", "53": "H",
    "55": "I", "56": "I",
    "58": "J", "59": "J", "60": "J", "61": "J", "62": "J", "63": "J",
    "64": "K", "65": "K", "66": "K",
    "68": "L",
    "69": "M", "70": "M", "71": "M", "72": "M", "73": "M", "74": "M", "75": "M",
    "77": "N", "78": "N", "79": "N", "80": "N", "81": "N", "82": "N",
    "84": "O", "85": "P", "86": "Q", "87": "Q", "88": "Q",
    "90": "R", "91": "R", "92": "R", "93": "R",
    "94": "S", "95": "S", "96": "S",
    "97": "T", "98": "T", "99": "U",
}


def section_de(naf_code):
    if not naf_code:
        return None
    return SECTION_PAR_PREFIXE.get(str(naf_code)[:2])


def naf_label(code):
    if not code:
        return None
    return NAF_LABELS.get(code)


def normalize_result(r: dict) -> dict:
    """Transforme une entree API brute en dictionnaire homogene pour les vues."""
    siege = r.get("siege") or {}
    finances = r.get("finances") or {}

    dernier_annee, dernier_fin = None, None
    for annee, data in sorted(finances.items(), reverse=True) if isinstance(finances, dict) else []:
        dernier_annee, dernier_fin = annee, data
        break

    type_voie = _clean(siege.get("type_voie"))
    numero = _clean(siege.get("numero_voie"))
    voie = _clean(siege.get("libelle_voie"))
    complement = _clean(siege.get("complement_adresse"))
    rue = " ".join(x for x in [numero, type_voie, voie] if x) or None
    if complement:
        rue = f"{rue}, {complement}" if rue else complement

    commune = _clean(siege.get("libelle_commune"))
    dept = _clean(siege.get("departement"))
    region_code = _clean(siege.get("region"))

    nature = _clean(r.get("nature_juridique"))

    dirigeants = []
    for d in (r.get("dirigeants") or [])[:8]:
        if d.get("type_dirigeant") == "personne physique":
            dirigeants.append({
                "nom": " ".join(x for x in [d.get("prenoms"), d.get("nom")] if x),
                "qualite": d.get("qualite"),
                "naissance": d.get("annee_de_naissance"),
                "morale": False,
            })
        else:
            dirigeants.append({
                "nom": d.get("denomination"),
                "qualite": d.get("qualite"),
                "naissance": None,
                "morale": True,
            })

    comp = r.get("complements") or {}
    return {
        "siren": r.get("siren"),
        "nom": r.get("nom_complet") or r.get("nom_raison_sociale") or "Sans nom",
        "sigle": _clean(r.get("sigle")),
        "forme": NATURE_JURIDIQUE.get(nature, f"Forme {nature}" if nature else None),
        "nature_code": nature,
        "date_creation": r.get("date_creation"),
        "date_fermeture": r.get("date_fermeture"),
        "categorie": _clean(r.get("categorie_entreprise")),
        "actif": (r.get("etat_administratif") or "A") == "A",
        "naf_code": r.get("activite_principale"),
        "naf_label": naf_label(r.get("activite_principale")),
        "section": r.get("section_activite_principale") or section_de(r.get("activite_principale")),
        "effectif_code": r.get("tranche_effectif_salarie"),
        "effectif": EFFECTIFS.get(r.get("tranche_effectif_salarie") or "NN", "Non renseigné"),
        "nb_etablissements": r.get("nombre_etablissements"),
        "adresse": _clean(siege.get("adresse")) or rue,
        "rue": rue,
        "code_postal": _clean(siege.get("code_postal")),
        "commune": commune,
        "departement": dept,
        "region": REGIONS.get(region_code or "", region_code),
        "siret_siege": _clean(siege.get("siret")),
        "date_debut_activite": _clean(siege.get("date_debut_activite")),
        "enseigne": None,
        "lat": siege.get("latitude"),
        "lng": siege.get("longitude"),
        "dirigeants": dirigeants,
        "finances": {
            "annee": dernier_annee,
            "ca": dernier_fin.get("ca") if dernier_fin else None,
            "resultat_net": dernier_fin.get("resultat_net") if dernier_fin else None,
        },
        "tva": (r.get("tva") or [None])[0],
        "complements": {
            "bio": comp.get("est_bio"),
            "rge": comp.get("est_rge"),
            "ess": comp.get("est_ess"),
            "ei": comp.get("est_entrepreneur_individuel"),
            "formation": comp.get("est_organisme_formation"),
            "qualiopi": comp.get("est_qualiopi"),
            "association": comp.get("est_association"),
            "avocat": comp.get("est_avocat"),
            "societe_mission": comp.get("est_societe_mission"),
        },
    }


_session = requests.Session()
_session.headers.update({"User-Agent": "MBDV-Prospection/1.0 (outil interne associes)"})


def _get(params: dict, tries: int = 3) -> dict:
    last_err = None
    for attempt in range(tries):
        try:
            resp = _session.get(BASE_URL, params=params, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise ApiError(
                f"Connexion a la base officielle impossible ({exc.__class__.__name__})."
            ) from exc  # erreur reseau : inutile de retenter immediatement
        if resp.status_code == 429:
            last_err = ApiError("Limite de requetes atteinte (7/seconde). Reessayez dans quelques secondes.")
            time.sleep(1.2 * (attempt + 1))
            continue
        if resp.status_code >= 500:
            last_err = ApiError(f"Service officiel indisponible (HTTP {resp.status_code}).")
            time.sleep(0.8 * (attempt + 1))
            continue
        if resp.status_code != 200:
            raise ApiError(f"Erreur de l'API officielle (HTTP {resp.status_code}).")
        try:
            return resp.json()
        except ValueError as exc:
            raise ApiError("Reponse illisible de l'API officielle.") from exc
    raise last_err or ApiError("Echec de l'appel a l'API officielle.")


def build_params(q="", page=1, departement="", code_postal="", naf="",
                 section="", effectif="", actives=True) -> dict:
    params = {"per_page": PER_PAGE, "page": max(1, int(page))}
    if q:
        params["q"] = q.strip()
    if departement:
        params["departement"] = departement.strip()
    if code_postal:
        params["code_postal"] = code_postal.strip()
    if naf:
        params["activite_principale"] = naf.strip()
    if section:
        params["section_activite_principale"] = section.strip()
    if effectif:
        params["tranche_effectif_salarie"] = effectif.strip()
    if actives:
        params["etat_administratif"] = "A"
    return params


def search(q="", page=1, departement="", code_postal="", naf="",
           section="", effectif="", actives=True) -> dict:
    """Appelle l'API officielle et renvoie {items, total_results, page, total_pages}."""
    payload = _get(build_params(q, page, departement, code_postal, naf, section, effectif, actives))
    results = payload.get("results") or []
    return {
        "items": [normalize_result(r) for r in results],
        "total_results": int(payload.get("total_results") or 0),
        "page": int(payload.get("page") or page),
        "total_pages": max(1, int(payload.get("total_pages") or 1)),
    }


def fetch_by_siren(siren: str):
    """Retrouve une entreprise precise par SIREN, ou None."""
    payload = _get({"q": str(siren), "per_page": 5, "page": 1})
    for r in payload.get("results") or []:
        if str(r.get("siren")) == str(siren):
            return normalize_result(r)
    return None


def slug_ascii(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in norm if not unicodedata.combining(c)).upper()
