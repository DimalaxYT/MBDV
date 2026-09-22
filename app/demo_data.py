"""Jeu de donnees de demonstration.

Utilise uniquement quand la base officielle (recherche-entreprises.api.gouv.fr)
est injoignable depuis l'environnement d'execution — c'est le cas de l'aperçu
heberge, dont le reseau sortant est filtre. Sur une machine classique, l'API
officielle est utilisee et ce jeu de donnees n'apparaît jamais.

Les entreprises ci-dessous sont fictives mais realistes, et structurees
exactement comme la sortie de gov_api.normalize_result().
"""
from .gov_api import EFFECTIFS, NAF_LABELS, NATURE_JURIDIQUE, section_de


def _e(siren, nom, forme_code, naf, date_creation, rue, cp, commune, dept, region,
       effectif="01", siret_suffix="00019", dirigeants=None, ca=None, res=None,
       annee_fin=None, categorie="PME", nb_etab=1, enseigne=None, actif=True):
    return {
        "siren": siren,
        "nom": nom,
        "sigle": None,
        "forme": NATURE_JURIDIQUE.get(forme_code, forme_code),
        "nature_code": forme_code,
        "date_creation": date_creation,
        "date_fermeture": None,
        "categorie": categorie,
        "actif": actif,
        "naf_code": naf,
        "naf_label": NAF_LABELS.get(naf),
        "section": section_de(naf),
        "effectif_code": effectif,
        "effectif": EFFECTIFS.get(effectif, "Non renseigné"),
        "nb_etablissements": nb_etab,
        "adresse": f"{rue} {cp} {commune}".upper(),
        "rue": rue.upper(),
        "code_postal": cp,
        "commune": commune.upper(),
        "departement": dept,
        "region": region,
        "siret_siege": siren + siret_suffix,
        "date_debut_activite": date_creation,
        "enseigne": enseigne,
        "lat": None,
        "lng": None,
        "dirigeants": dirigeants or [],
        "finances": {"annee": annee_fin, "ca": ca, "resultat_net": res},
        "tva": f"FR{40 + int(siren[0])}{siren[:9]}" if siren else None,
        "complements": {"bio": False, "rge": False, "ess": False, "ei": False,
                        "formation": False, "qualiopi": False, "association": False,
                        "avocat": False, "societe_mission": False},
    }


def _pp(nom, prenom, qualite, annee):
    return {"nom": f"{prenom} {nom}", "qualite": qualite, "naissance": annee,
            "morale": False}


DEMO_COMPANIES = [
    # --- Coiffures, Loire-Atlantique (44) ---
    _e("848902672", "CARACOLE COIFFURE", "5710", "96.02A", "2019-04-01",
       "14 rue des Sables", "44600", "Saint-Nazaire", "44", "Pays de la Loire",
       "02", "00012", [_pp("Le Goff", "Amélie", "Gérante", "1990")], 48210, -1240, "2023"),
    _e("901345127", "COIFFURE NADILEA", "5443", "96.02A", "2021-06-15",
       "3 place Royale", "44000", "Nantes", "44", "Pays de la Loire",
       "01", "00015", [_pp("Berrada", "Nadia", "Gérante", "1986")], 39500, 2100, "2023"),
    _e("822491330", "COIFFURE INSTINC'TIF", "5499", "96.02A", "2016-07-04",
       "6 route des Basses Landes", "44260", "Prinquiau", "44", "Pays de la Loire",
       "01", "00010", [_pp("Levêque", "Laure", "Gérante", "1978")], None, None, None),
    # --- Boulangeries, Charente (16) ---
    _e("810764231", "BOULANGERIE MAISON PERRIN", "5442", "10.71C", "2015-03-19",
       "22 rue d'Angoulême", "16200", "Cognac", "16", "Nouvelle-Aquitaine",
       "03", "00021", [_pp("Perrin", "Marc", "Gérant", "1972"),
                       _pp("Perrin", "Sophie", "Co-gérante", "1975")], 187400, 11200, "2024"),
    _e("884512903", "FOURNIL DE LA CHARENTE", "1000", "10.71C", "2018-09-10",
       "51 avenue de Saintes", "16100", "Cognac", "16", "Nouvelle-Aquitaine",
       "02", "00013", [_pp("Dubois", "Karim", "Entrepreneur individuel", "1983")],
       121900, 7400, "2024"),
    _e("912387645", "BOULANGERIE DU PILAT", "5442", "10.71C", "2022-01-24",
       "8 boulevard Besson Bey", "16000", "Angoulême", "16", "Nouvelle-Aquitaine",
       "01", "00011", [_pp("Marty", "Claire", "Gérante", "1991")], 96300, -3100, "2024"),
    # --- Restaurants, Paris (75) ---
    _e("873450912", "RESTAURANT CALIOPE", "5710", "56.10A", "2017-11-02",
       "37 rue de Charonne", "75011", "Paris", "75", "Île-de-France",
       "03", "00023", [_pp("Fournier", "Angèle", "Présidente", "1980")], 342000, 18400, "2023"),
    _e("893012456", "BRASSERIE VAUBERT", "5442", "56.10A", "2019-02-13",
       "12 rue des Francs-Bourgeois", "75004", "Paris", "75", "Île-de-France",
       "03", "00019", [_pp("Garnier", "Paul", "Gérant", "1977"),
                       _pp("Garnier", "Lucie", "Co-gérante", "1979")], 512400, -8700, "2024"),
    _e("924671035", "CANAILLE ET CIE", "5710", "56.10C", "2023-05-30",
       "9 rue Oberkampf", "75011", "Paris", "75", "Île-de-France",
       "02", "00017", [_pp("Moreau", "Julien", "Président", "1988")], None, None, None),
    # --- Artisans du batiment, Dordogne (24) ---
    _e("834120769", "MENUISERIE RAVELINE", "5442", "43.32B", "2014-06-09",
       "zone artisanale des Granges", "24100", "Bergerac", "24", "Nouvelle-Aquitaine",
       "02", "00016", [_pp("Raveline", "Thierry", "Gérant", "1969")], 148700, 9300, "2024"),
    _e("845670123", "ELEC THIBAUD", "5443", "43.21A", "2017-04-27",
       "14 route de Sarlat", "24000", "Perigueux", "24", "Nouvelle-Aquitaine",
       "01", "00014", [_pp("Thibaud", "Yannick", "Gérant", "1985")], 74200, 4100, "2023"),
    _e("905612348", "MAÇONNERIE LASCAUX BÂTIMENT", "5442", "43.91A", "2020-10-06",
       "19 avenue de la Gare", "24200", "Sarlat-la-Canéda", "24", "Nouvelle-Aquitaine",
       "02", "00012", [_pp("Sirgu", "Damien", "Gérant", "1974")], 216300, 15600, "2024"),
    # --- Variete sectorielle et geographique ---
    _e("816743209", "GARAGE MOTTE ET FILS", "5442", "45.20A", "2013-12-16",
       "89 rue de Paris", "03100", "Montluçon", "03", "Auvergne-Rhône-Alpes",
       "03", "00024", [_pp("Motte", "Alain", "Gérant", "1965"),
                       _pp("Motte", "Kévin", "Co-gérant", "1993")], 388200, 22700, "2024"),
    _e("827104965", "FLEURISTERIE DELACOUR", "5443", "47.71Z", "2018-05-21",
       "5 place du Marche", "46000", "Cahors", "46", "Occitanie",
       "00", "00011", [_pp("Delacour", "Hélène", "Gérante", "1982")], 58400, 3600, "2024"),
    _e("831265847", "STUDIO HERBIN", "5710", "74.10Z", "2021-09-08",
       "18 rue du Taur", "31000", "Toulouse", "31", "Occitanie",
       "01", "00015", [_pp("Herbin", "Thomas", "Président", "1990")], 87100, -2600, "2024"),
    _e("845091236", "SALON ÉCLAT BEAUTÉ", "1000", "96.02B", "2019-07-15",
       "27 rue Alsace Lorraine", "34000", "Béziers", "34", "Occitanie",
       "01", "00013", [_pp("Nguyen", "Thùy", "Entrepreneure individuelle", "1988")],
       46700, 5900, "2023"),
    _e("852130764", "AU FROMAGER DU LOT", "5442", "47.11A", "2016-02-03",
       "3 rue Nationale", "46100", "Figeac", "46", "Occitanie",
       "01", "00012", [_pp("Roussel", "Bernard", "Gérant", "1961")], 132500, 8100, "2023"),
    _e("863409127", "PLOMBERIE THIVELIER", "5442", "43.22A", "2015-08-31",
       "40 route de Lyon", "26000", "Valence", "26", "Auvergne-Rhône-Alpes",
       "03", "00022", [_pp("Thivelier", "Fabrice", "Gérant", "1970")], 276400, 19700, "2024"),
    _e("874512036", "LE RELAIS DES GORGES", "5442", "55.10Z", "2014-04-12",
       "le Village", "12100", "Millau", "12", "Occitanie",
       "03", "00018", [_pp("Ibn Ziad", "Samir", "Gérant", "1976")], 289600, -4200, "2023"),
    _e("881204579", "CAVE ET TERROIRS", "5710", "47.11B", "2020-12-01",
       "11 rue Saint-James", "33000", "Bordeaux", "33", "Nouvelle-Aquitaine",
       "02", "00016", [_pp("Rey", "Charlotte", "Présidente", "1984")], 198300, 11400, "2024"),
    _e("893671240", "AUTO-ÉCOLE DU STADE", "5442", "85.59A", "2012-10-22",
       "2 avenue Gambetta", "06000", "Nice", "06", "Provence-Alpes-Côte d'Azur",
       "02", "00020", [_pp("Santini", "José", "Gérant", "1959")], 164900, 24300, "2023"),
    _e("904128375", "CABINET BODIN CONSEIL", "5443", "70.22Z", "2022-03-14",
       "16 rue de la Paix", "69002", "Lyon", "69", "Auvergne-Rhône-Alpes",
       "00", "00011", [_pp("Bodin", "Marion", "Gérante", "1992")], 64100, 9800, "2024"),
    _e("912045673", "TRAITEUR MARÉCHAL", "5442", "56.21Z", "2018-11-19",
       "23 rue des Archives", "52100", "Saint-Dizier", "52", "Grand Est",
       "02", "00015", [_pp("Maréchal", "Cédric", "Gérant", "1981")], 234800, 13600, "2024"),
    _e("923810649", "PAYSAGES ET JARDINS D'ARLES", "1000", "81.30Z", "2017-06-05",
       "4 chemin du Vieux Moulin", "13200", "Arles", "13", "Provence-Alpes-Côte d'Azur",
       "01", "00014", [_pp("Siméon", "Marc-Antoine", "Entrepreneur individuel", "1979")],
       91700, 6200, "2023"),
    _e("934126708", "PEINTURE DRAGUIGNAN DÉCOR", "5443", "43.34A", "2021-01-11",
       "12 boulevard du General de Gaulle", "83300", "Draguignan", "83",
       "Provence-Alpes-Côte d'Azur", "00", "00012",
       [_pp("Ferrand", "Nathalie", "Gérante", "1987")], None, None, None),
    _e("945018273", "ESTHÉTIQUE NEVERS BEAUTÉ", "5442", "96.02B", "2019-09-30",
       "7 rue Saint-Jacques", "58000", "Nevers", "58", "Bourgogne-Franche-Comté",
       "01", "00013", [_pp("Baptiste", "Sandra", "Gérante", "1993")], 52800, 3800, "2024"),
    _e("956702148", "PRESSING LAVAL NET", "1000", "96.01Z", "2016-12-08",
       "19 rue du Renames", "53000", "Laval", "53", "Pays de la Loire",
       "00", "00011", [_pp("Huyghe", "Véronique", "Entrepreneure individuelle", "1968")],
       71300, 4900, "2023"),
    _e("967124805", "CHOLET MATÉRIAUX OUVRIERS", "5442", "47.52A", "2014-02-25",
       "rue industrielle de la Tessoualle", "49300", "Cholet", "49", "Pays de la Loire",
       "03", "00026", [_pp("Touchard", "Pascal", "Gérant", "1966")], 745100, 31200, "2024"),
    _e("978034126", "LA CHAUSSÉE AUX CHAUSSURES", "5443", "47.72A", "2023-02-06",
       "14 rue de la Chaussee", "08000", "Charleville-Mézières", "08", "Grand Est",
       "00", "00011", [_pp("Kaci", "Sofiane", "Gérant", "1994")], None, None, None),
    _e("981260743", "LE BISTROT DE L'AURILLAC", "5442", "56.10A", "2020-06-17",
       "1 rue des Salins", "15000", "Aurillac", "15", "Auvergne-Rhône-Alpes",
       "02", "00017", [_pp("Valadier", "Gilles", "Gérant", "1971")], 187600, 9900, "2024"),
    # --- Ile-de-France hors Paris (92, 93, 94) ---
    _e("891274536", "COIFFURE LA BOUCLE D'OR", "5443", "96.02A", "2018-03-12",
       "25 rue de Montreuil", "94300", "Vincennes", "94", "Île-de-France",
       "01", "00014", [_pp("Nguyen", "Linh", "Gérante", "1984")], 61400, 5200, "2023"),
    _e("902381457", "BOULANGERIE DU MARCHÉ", "5442", "10.71C", "2016-09-05",
       "3 place de l'Abbaye", "94000", "Créteil", "94", "Île-de-France",
       "02", "00018", [_pp("Bourdin", "Hakim", "Gérant", "1981"),
                       _pp("Bourdin", "Élise", "Co-gérante", "1983")], 143700, 8100, "2024"),
    _e("913402678", "PLOMBERIE CHARRON", "1000", "43.22A", "2012-11-20",
       "8 avenue du Général Leclerc", "94120", "Fontenay-sous-Bois", "94", "Île-de-France",
       "01", "00016", [_pp("Charron", "Bruno", "Entrepreneur individuel", "1970")],
       96800, 6400, "2024"),
    _e("924576310", "GARAGE DE LA MARNE", "5442", "45.20A", "2015-05-27",
       "41 quai des Carrières", "94220", "Charenton-le-Pont", "94", "Île-de-France",
       "02", "00020", [_pp("Bensaid", "Farid", "Gérant", "1976")], 268400, 14300, "2023"),
    _e("935618204", "PRESSING DU PARC", "1000", "96.01Z", "2019-07-15",
       "12 rue de la Grande Varenne", "94130", "Nogent-sur-Marne", "94", "Île-de-France",
       "00", "00011", [_pp("Diallo", "Aminata", "Entrepreneure individuelle", "1989")],
       44300, 2900, "2024"),
    _e("946702351", "RESTAURANT LE VIEUX PONT", "5710", "56.10A", "2017-02-08",
       "67 avenue Jean Jaurès", "92100", "Boulogne-Billancourt", "92", "Île-de-France",
       "12", "00024", [_pp("Lemoine", "Cédric", "Président", "1979")], 486200, -5200, "2024"),
    _e("957124068", "MENUISERIE DES HAUTS-DE-SEINE", "5442", "43.32B", "2013-08-19",
       "5 rue des Longs Prés", "92000", "Nanterre", "92", "Île-de-France",
       "03", "00022", [_pp("Ouedraogo", "Ismaël", "Gérant", "1974")], 173500, 9800, "2023"),
    _e("968245713", "COIFFURE TÊTE-À-TÊTE", "5499", "96.02A", "2021-01-11",
       "44 rue de Paris", "93100", "Montreuil", "93", "Île-de-France",
       "01", "00013", [_pp("Salvador", "Manon", "Gérante", "1992")], 38700, 2100, "2024"),
    _e("979356824", "BOULANGERIE DU CANAL", "5442", "10.71C", "2014-04-02",
       "88 rue du Landy", "93200", "Saint-Denis", "93", "Île-de-France",
       "02", "00019", [_pp("Traoré", "Moussa", "Gérant", "1977")], 132900, 7600, "2024"),
]

# Departements couverts par le jeu de demonstration (affiche dans l'interface,
# pour ne pas laisser croire a un resultat vide de la base officielle).
DEPARTEMENTS = sorted({e["departement"] for e in DEMO_COMPANIES})


def cherche(q="", departement="", code_postal="", section="", effectif="",
            actives=True, page=1):
    """Filtre le jeu de demonstration, meme contrat que gov_api.search()."""
    from .gov_api import slug_ascii

    def correspond(e):
        if actives and not e["actif"]:
            return False
        if departement and e["departement"] != departement.strip():
            return False
        if code_postal and e["code_postal"] != code_postal.strip():
            return False
        if section and (e["section"] or "") != section.strip():
            return False
        if effectif and e["effectif_code"] != effectif.strip():
            return False
        if q:
            aiguille = slug_ascii(q).strip()
            foin = slug_ascii(" ".join([e["nom"], e.get("naf_label") or "",
                                        e.get("commune") or ""]))
            if aiguille not in foin:
                return False
        return True

    # Copie superficielle : les vues enrichissent les fiches (etat de detection,
    # suivi, masquage) et ne doivent pas polluer le jeu partage entre requetes.
    items = [dict(e) for e in DEMO_COMPANIES if correspond(e)]
    total = len(items)
    per_page = 25
    debut = (max(1, page) - 1) * per_page
    return {
        "items": items[debut:debut + per_page],
        "total_results": total,
        "page": max(1, page),
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


def par_siren(siren):
    for e in DEMO_COMPANIES:
        if e["siren"] == str(siren):
            return dict(e)
    return None
