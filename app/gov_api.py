"""Client de l'API officielle Recherche d'entreprises (recherche-entreprises.api.gouv.fr).

API publique du Ministere de l'Economie, donnees INSEE / RNE sous licence ouverte.
Aucune cle d'API n'est necessaire.
"""
import re
import threading
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
    "9220": "Association déclarée d'utilité publique",
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
    "75": "Nouvelle-Aquitaine", "76": "Occitanie",
    "84": "Auvergne-Rhône-Alpes", "93": "Provence-Alpes-Côte d'Azur", "94": "Corse",
}

DEPARTEMENTS_FR = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence",
    "05": "Hautes-Alpes", "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes",
    "09": "Ariège", "10": "Aube", "11": "Aude", "12": "Aveyron",
    "13": "Bouches-du-Rhône", "14": "Calvados", "15": "Cantal", "16": "Charente",
    "17": "Charente-Maritime", "18": "Cher", "19": "Corrèze", "2A": "Corse-du-Sud",
    "2B": "Haute-Corse", "21": "Côte-d'Or", "22": "Côtes-d'Armor", "23": "Creuse",
    "24": "Dordogne", "25": "Doubs", "26": "Drôme", "27": "Eure",
    "28": "Eure-et-Loir", "29": "Finistère", "30": "Gard", "31": "Haute-Garonne",
    "32": "Gers", "33": "Gironde", "34": "Hérault", "35": "Ille-et-Vilaine",
    "36": "Indre", "37": "Indre-et-Loire", "38": "Isère", "39": "Jura",
    "40": "Landes", "41": "Loir-et-Cher", "42": "Loire", "43": "Haute-Loire",
    "44": "Loire-Atlantique", "45": "Loiret", "46": "Lot", "47": "Lot-et-Garonne",
    "48": "Lozère", "49": "Maine-et-Loire", "50": "Manche", "51": "Marne",
    "52": "Haute-Marne", "53": "Mayenne", "54": "Meurthe-et-Moselle", "55": "Meuse",
    "56": "Morbihan", "57": "Moselle", "58": "Nièvre", "59": "Nord",
    "60": "Oise", "61": "Orne", "62": "Pas-de-Calais", "63": "Puy-de-Dôme",
    "64": "Pyrénées-Atlantiques", "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales",
    "67": "Bas-Rhin", "68": "Haut-Rhin", "69": "Rhône", "70": "Haute-Saône",
    "71": "Saône-et-Loire", "72": "Sarthe", "73": "Savoie", "74": "Haute-Savoie",
    "75": "Paris", "76": "Seine-Maritime", "77": "Seine-et-Marne", "78": "Yvelines",
    "79": "Deux-Sèvres", "80": "Somme", "81": "Tarn", "82": "Tarn-et-Garonne",
    "83": "Var", "84": "Vaucluse", "85": "Vendée", "86": "Vienne",
    "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne", "90": "Territoire de Belfort",
    "91": "Essonne", "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis",
    "94": "Val-de-Marne", "95": "Val-d'Oise",
    "971": "Guadeloupe", "972": "Martinique", "973": "Guyane",
    "974": "La Réunion", "976": "Mayotte",
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
    "47.11A": "Commerce alimentaire (épicerie, fromagerie)",
    "47.11B": "Commerce d'alimentation générale (cave, primeur)",
    "47.71Z": "Commerce de détail d'habillement",
    "47.72A": "Commerce de détail de chaussures",
    "47.73Z": "Pharmacies",
    "47.75Z": "Parfumerie et produits de beauté",
    "47.77Z": "Horlogerie et bijouterie",
    "47.52A": "Quincaillerie, matériaux de construction",
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
    "74.10Z": "Design, création graphique",
    "69.20Z": "Activités comptables",
    "70.22Z": "Conseil de gestion",
    "81.21Z": "Nettoyage de bâtiments",
    "81.30Z": "Entretien d'espaces verts, paysagisme",
    "85.59A": "Formation continue d'adultes",
    "86.21Z": "Médecine générale et spécialisée",
    "86.23Z": "Chirurgiens-dentistes",
    "88.99B": "Action sociale sans hébergement",
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


def _sans_espaces(value) -> str:
    return " ".join(str(value or "").upper().split())


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


def departement_depuis_code_postal(code_postal) -> str:
    """Deduit le departement d'un code postal, Corse et outre-mer compris.

    Les deux premiers chiffres ne suffisent pas : « 20200 » donne « 2A » (et non
    « 20 », qui n'existe pas), « 20600 » donne « 2B », « 97400 » donne « 974 ».
    Toute la deduction passe par ici, pour que la recherche filtree sur le texte
    saisi et la normalisation des fiches officielles donnent le meme resultat.
    """
    cp = _clean(code_postal) or ""
    if not re.fullmatch(r"\d{5}", cp):
        return ""
    if cp.startswith("20"):
        return "2A" if cp < "20200" else "2B"
    if cp.startswith(("97", "98")):
        return cp[:3]
    return cp[:2]


# Marques dont le nom est aussi un mot francais courant : « AVIS IMMOBILIER »
# (agence locale) ou « CRIT'ERRE » (electricien) sont de vraies PME, pas des
# enseignes nationales. Ces marques ne comptent donc que sous leur forme complete
# d'enseigne, jamais quand le mot apparait au milieu d'une denomination.
MARQUES_MOTS_COURANTS = (
    r"AVIS\s+(?:BUDGET|LOCATION|RENT\s+A\s+CAR)",
    r"CRIT\s+(?:FRANCE|INTERIM|INT[ÉE]RIM|BTP|A[ÉE]ROPORT|JOBS|MARITIME|TRAVAUX|CDD)",
)


def _est_marque_carrefour(nom: str, enseigne: str, naf: str) -> bool:
    texte = slug_ascii(f"{nom} {enseigne}").upper()
    if "CARREFOUR" not in texte:
        return False
    # Formats specifiques de l'enseigne Carrefour (Carrefour Market, Carrefour Express, Carrefour City...)
    if re.search(
        r"\bCARREFOUR\s+(?:MARKET|EXPRESS|CITY|CONTACT|PROXIMITE|DRIVE|HYPER|SUPER|BANQUE|VOYAGES|LOCATION|FRANCE|PARTENARIAT|SUPERMARCHE)\b",
        texte,
    ):
        return True
    # 'du carrefour', 'au carrefour'... (lieu geographique : café ou boulangerie au carrefour)
    if re.search(r"\b(?:DU|AU|LE)\s+CARREFOUR\b", texte):
        return False
    # Nom commencant par Carrefour ou enseigne Carrefour
    if re.search(r"^(?:SAS|SARL|SA|EURL|SOCIETE)?\s*CARREFOUR\b", nom.upper()) or (
        enseigne and re.search(r"^CARREFOUR\b", enseigne.upper())
    ):
        return True
    # Grande distribution alimentaire avec Carrefour dans le libelle
    return bool(str(naf).startswith("47.11"))


def est_grande_entreprise(c: dict) -> bool:
    """Identifie une grande entreprise (GE, ETI, reseaux, grandes enseignes, ONG).

    Balise prospecte les TPE / PME / artisans sans site : les grands groupes,
    enseignes nationales (Carrefour...), organisations (Croix-Rouge...)
    et administrations sont hors cible.
    """
    # 1. Classification officielle INSEE (GE et ETI)
    cat = (c.get("categorie") or "").upper()
    if cat in {"GE", "ETI"}:
        return True

    # 2. Effectifs massifs (100 salaries et plus)
    eff = str(c.get("effectif_code") or "")
    if eff in {"22", "31", "32", "41", "42", "51", "52", "53"}:
        return True

    # 3. Nature juridique publique ou securite sociale (7xxx, 8xxx)
    nature = str(c.get("nature_code") or "")
    if nature.startswith(("7", "8")):
        return True

    # 4. Multi-etablissements importants (reseaux, chaines de 10+ etablissements)
    nb_etab = c.get("nb_etablissements")
    if nb_etab and isinstance(nb_etab, int) and nb_etab >= 10:
        return True

    # 5. Services publics et collectivites
    comp = c.get("complements") or {}
    if comp.get("est_service_public") or comp.get("est_collectivite_territoriale"):
        return True

    # 6. Activites de tres grandes surfaces (hypermarches 47.11F)
    naf = str(c.get("naf_code") or "")
    if naf.startswith("47.11F"):
        return True

    # 7. Marques nationales, franchises et grandes ONG / associations
    nom = c.get("nom") or ""
    enseigne = c.get("enseigne") or ""
    texte = slug_ascii(f"{nom} {enseigne}").upper()

    # Croix-Rouge et organisations humanitaires majeures
    if re.search(r"\bCROIX[\s\-]+ROUGE\b", texte):
        return True
    if re.search(
        r"\b(?:SECOURS\s+POPULAIRE|SECOURS\s+CATHOLIQUE|RESTOS?\s+DU\s+COEUR|"
        r"RESTAURANTS?\s+DU\s+COEUR|EMMAUS|ARMEE\s+DU\s+SALUT|APF\s+FRANCE\s+HANDICAP|"
        r"LIGUE\s+CONTRE\s+LE\s+CANCER|AFM\s+TELETHON|MEDECINS\s+DU\s+MONDE|"
        r"MEDECINS\s+SANS\s+FRONTIERES|ACTION\s+CONTRE\s+LA\s+FAIM|"
        r"AMNESTY\s+INTERNATIONAL|SNSM|FONDATION\s+DE\s+FRANCE|APPRENTIS\s+D\s*AUTEUIL|"
        r"UNICEF|HANDICAP\s+INTERNATIONAL|GREENPEACE|WWF)\b",
        texte,
    ):
        return True

    # Carrefour (enseignes et filiales, sans toucher aux bistrots/boulangeries 'du carrefour')
    if _est_marque_carrefour(nom, enseigne, naf):
        return True

    # Autres grandes enseignes de distribution, restauration rapide et reseaux
    if re.search(
        r"\b(?:AUCHAN|LECLERC|E\s*LECLERC|INTERMARCHE|NETTO|LIDL|ALDI|MONOPRIX|"
        r"FRANPRIX|SYSTEME\s+U|SUPER\s+U|HYPER\s+U|PICARD\s+SURGELES|GRAND\s+FRAIS|"
        r"CASINO\s+SUPERMARCHES?|CASINO\s+SHOP|PETIT\s+CASINO|GEANT\s+CASINO|"
        r"LEADER\s+PRICE|CORA)\b",
        texte,
    ):
        return True
    if re.search(
        r"\b(?:MCDONALDS?|BURGER\s+KING|KFC|SUBWAY|DOMINO\s*S\s*PIZZA|PIZZA\s+HUT|"
        r"BRIOCHE\s+DOREE|MARIE\s+BLACHERE|LA\s+MIE\s+CALINE|STARBUCKS|QUICK)\b",
        texte,
    ):
        return True
    if re.search(
        r"\b(?:LEROY\s+MERLIN|CASTORAMA|BRICO\s+DEPOT|BRICORAMA|DECATHLON|"
        r"IKEA|CONFORAMA|DARTY|FNAC)\b",
        texte,
    ):
        return True
    if re.search(
        r"\b(?:TOTALENERGIES|NORAUTO|FEU\s+VERT|MIDAS|SPEEDY|POINT\s+S|CARGLASS|"
        r"EUROPCAR|SIXT|HERTZ)\b",
        texte,
    ):
        return True
    # Marques a mot courant (AVIS, CRIT...) : voir MARQUES_MOTS_COURANTS.
    if re.search("|".join(MARQUES_MOTS_COURANTS), texte):
        return True
    if re.search(
        r"\b(?:BNP\s+PARIBAS|SOCIETE\s+GENERALE|CREDIT\s+AGRICOLE|CREDIT\s+MUTUEL|"
        r"CAISSE\s+D\s*EPARGNE|BANQUE\s+POPULAIRE|LCL|LA\s+BANQUE\s+POSTALE|CIC|"
        r"AXA|ALLIANZ|GROUPAMA|MACIF|MAIF|MATMUT|MMA|GMF)\b",
        texte,
    ):
        return True
    if re.search(r"\b(?:ADECCO|MANPOWER|RANDSTAD|PROMAN|SYNERGIE)\b", texte):
        return True
    return bool(
        re.search(
            r"\b(?:LA\s+POSTE|CHRONOPOST|DPD|COLISSIMO|ORANGE|SFR|"
            r"BOUYGUES\s+TELECOM|FREE\s+MOBILE|SNCF|RATP|KEOLIS|TRANSDEV)\b",
            texte,
        )
    )


def normalize_result(r: dict, filter_departement: str = "", filter_code_postal: str = "") -> dict:
    """Transforme une entree API brute en dictionnaire homogene pour les vues."""
    siege = r.get("siege") or {}
    etab_choisi = siege

    # Si un departement ou un code postal est precise et que le siege ne correspond
    # pas, on verifie si un etablissement correspondant existe dans matching_etablissements.
    matching = r.get("matching_etablissements") or []
    if filter_code_postal and _clean(siege.get("code_postal")) != filter_code_postal:
        for etab in matching:
            if _clean(etab.get("code_postal")) == filter_code_postal:
                etab_choisi = etab
                break
    elif filter_departement and _clean(siege.get("departement")) != filter_departement:
        for etab in matching:
            if _clean(etab.get("departement")) == filter_departement:
                etab_choisi = etab
                break

    finances = r.get("finances") or {}

    dernier_annee, dernier_fin = None, None
    for annee, data in sorted(finances.items(), reverse=True) if isinstance(finances, dict) else []:
        dernier_annee, dernier_fin = annee, data
        break

    type_voie = _clean(etab_choisi.get("type_voie"))
    numero = _clean(etab_choisi.get("numero_voie"))
    voie = _clean(etab_choisi.get("libelle_voie"))
    complement = _clean(etab_choisi.get("complement_adresse"))
    rue = " ".join(x for x in [numero, type_voie, voie] if x) or None
    if complement:
        rue = f"{rue}, {complement}" if rue else complement

    commune = _clean(etab_choisi.get("libelle_commune"))
    dept = _clean(etab_choisi.get("departement"))
    region_code = _clean(etab_choisi.get("region"))

    # Secours departement si non fourni directement dans l'etablissement mais deduisible du code postal
    if not dept:
        dept = departement_depuis_code_postal(etab_choisi.get("code_postal")) or None

    # Enseigne : denomination usuelle de l'etablissement (source SIRENE). Utile a la
    # detection de site : "SARL DUPONT" peut exercer sous "Le Fournil d'Alice".
    nom_principal = r.get("nom_complet") or r.get("nom_raison_sociale") or "Sans nom"
    raw_enseignes = etab_choisi.get("liste_enseignes") or siege.get("liste_enseignes") or []
    enseignes = [_clean(e) for e in raw_enseignes]
    enseigne = (_clean(etab_choisi.get("nom_commercial"))
                or _clean(siege.get("nom_commercial"))
                or next((e for e in enseignes if e), None))
    if enseigne and _sans_espaces(enseigne) == _sans_espaces(nom_principal):
        enseigne = None

    nature = _clean(r.get("nature_juridique"))
    # Libelle officiel de l'API en secours : la table locale ne couvre que les
    # formes les plus courantes, et « Forme 6903 » n'apprend rien a personne.
    forme = (NATURE_JURIDIQUE.get(nature)
             or _clean(r.get("libelle_nature_juridique"))
             or (f"Forme {nature}" if nature else None))

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
        "nom": nom_principal,
        "sigle": _clean(r.get("sigle")),
        "forme": forme,
        "nature_code": nature,
        "date_creation": r.get("date_creation"),
        "date_fermeture": r.get("date_fermeture"),
        "categorie": _clean(r.get("categorie_entreprise")),
        "actif": (r.get("etat_administratif") or "A") == "A",
        "naf_code": r.get("activite_principale"),
        "naf_label": naf_label(r.get("activite_principale"))
                     or _clean(r.get("libelle_activite_principale")),
        "section": r.get("section_activite_principale") or section_de(r.get("activite_principale")),
        "effectif_code": r.get("tranche_effectif_salarie"),
        "effectif": EFFECTIFS.get(r.get("tranche_effectif_salarie") or "NN", "Non renseigné"),
        "nb_etablissements": r.get("nombre_etablissements"),
        "adresse": _clean(etab_choisi.get("adresse")) or rue,
        "rue": rue,
        "code_postal": _clean(etab_choisi.get("code_postal")),
        "commune": commune,
        "departement": dept,
        "region": REGIONS.get(region_code or "", region_code),
        "siret_siege": _clean(siege.get("siret")),
        "siret_etab": _clean(etab_choisi.get("siret")),
        "date_debut_activite": _clean(
            etab_choisi.get("date_debut_activite") or siege.get("date_debut_activite")
        ),
        "enseigne": enseigne,
        "lat": etab_choisi.get("latitude") or siege.get("latitude"),
        "lng": etab_choisi.get("longitude") or siege.get("longitude"),
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
            "est_service_public": comp.get("est_service_public"),
            "est_collectivite_territoriale": comp.get("est_collectivite_territoriale"),
        },
    }


_session = requests.Session()
_session.headers.update({"User-Agent": "MBDV-Prospection/1.0 (outil interne associes)"})
_adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

_CACHE_LOCK = threading.Lock()
_API_CACHE = {}
_API_CACHE_TTL = 300  # 5 minutes
_API_CACHE_MAX = 500


def clear_cache() -> None:
    """Vide le cache memoire des requetes de l'API officielle."""
    with _CACHE_LOCK:
        _API_CACHE.clear()


def _get_cache(key):
    with _CACHE_LOCK:
        if key in _API_CACHE:
            ts, data = _API_CACHE[key]
            if time.time() - ts < _API_CACHE_TTL:
                return data
            del _API_CACHE[key]
    return None


def _set_cache(key, data):
    with _CACHE_LOCK:
        if len(_API_CACHE) >= _API_CACHE_MAX:
            oldest = sorted(_API_CACHE.keys(), key=lambda k: _API_CACHE[k][0])[:100]
            for k in oldest:
                _API_CACHE.pop(k, None)
        _API_CACHE[key] = (time.time(), data)


def _retry_after(resp, defaut: float) -> float:
    """Delai a respecter apres un 429 (en-tete Retry-After), borne a 5 secondes."""
    try:
        return max(0.2, min(float(resp.headers.get("Retry-After", defaut)), 5.0))
    except (TypeError, ValueError):
        return defaut


def _get(params: dict, tries: int = 3) -> dict:
    key = tuple(sorted((str(k), str(v)) for k, v in params.items()))
    cached = _get_cache(key)
    if cached is not None:
        return cached

    last_err = None
    for attempt in range(tries):
        try:
            resp = _session.get(BASE_URL, params=params, timeout=TIMEOUT)
        except requests.RequestException as exc:
            last_err = ApiError(
                f"Connexion a la base officielle impossible ({exc.__class__.__name__})."
            )
            if attempt < tries - 1:
                time.sleep(0.4 * (attempt + 1))
                continue
            raise last_err from exc
        if resp.status_code == 429:
            last_err = ApiError("Limite de requetes atteinte (7/seconde). Reessayez dans quelques secondes.")
            time.sleep(_retry_after(resp, 1.2 * (attempt + 1)))
            continue
        if resp.status_code >= 500:
            last_err = ApiError(f"Service officiel indisponible (HTTP {resp.status_code}).")
            time.sleep(0.8 * (attempt + 1))
            continue
        if resp.status_code != 200:
            raise ApiError(f"Erreur de l'API officielle (HTTP {resp.status_code}).")
        try:
            data = resp.json()
            _set_cache(key, data)
            return data
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
        "items": [
            normalize_result(r, filter_departement=departement, filter_code_postal=code_postal)
            for r in results
        ],
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
