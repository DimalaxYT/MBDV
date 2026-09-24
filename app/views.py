"""Routes de l'application : recherche, fiches, portefeuille, panel staff, API."""
import csv
import io
import json
import math
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from . import db, demo_data, detect, gov_api
from .auth import (
    admin_required,
    current_user,
    est_admin,
    login,
    login_attempts_exhausted,
    login_required,
    logout,
    register_failed_attempt,
    url_avec_jeton,
)
from .icons import icon

# Fuseau de reference pour les dates affichees (meme source que auth).
PARIS_TZ = ZoneInfo("Europe/Paris")

bp = Blueprint("views", __name__)

RAISONS_MASQUAGE = [
    "Doublon",
    "Possède déjà un site web",
    "Hors secteur cible",
    "Hors zone géographique",
    "Entreprise fermée ou inactive",
    "Structure trop petite ou trop récente",
    "Déjà client ou déjà contacté",
    "Autre",
]

STATUTS_PIPELINE = [
    ("a_contacter", "À contacter"),
    ("contacte", "Contacté"),
    ("discussion", "En discussion"),
    ("devis", "Devis envoyé"),
    ("client", "Client"),
    ("sans_suite", "Sans suite"),
]

class BaseIndisponible(Exception):
    """La base officielle est injoignable et le mode demonstration n'a pas ete demande."""


PER_PAGE = 25
MAX_PAGES_EXPANSION = 6   # pages API analysees quand le filtre "sans site" est actif
MAX_PAGES_EXPORT = 4


@bp.app_context_processor
def _inject_globales():
    return {
        "raisons_masquage": RAISONS_MASQUAGE,
        "statuts_pipeline": STATUTS_PIPELINE,
        "statut_label": dict(STATUTS_PIPELINE),
        "icon": icon,
    }


# --------------------------------------------------------------------------
# Connexion
# --------------------------------------------------------------------------

def _destination_sure(dest) -> str:
    """Chemin interne uniquement : bloque les redirections ouvertes (//exemple.com)."""
    secours = url_for("views.accueil")
    texte = str(dest or "")
    if not texte.startswith("/") or texte.startswith("//") or texte.startswith("/\\"):
        return secours
    analyse = urlparse(texte)
    if analyse.scheme or analyse.netloc:
        return secours
    return texte


@bp.route("/connexion", methods=["GET", "POST"])
def connexion():
    if current_user():
        return redirect(url_avec_jeton(url_for("views.accueil")))
    erreur = None
    if request.method == "GET":
        # Trace utile pour diagnostiquer un apercu qui perd son cookie de session
        # (cookie tiers refuse dans une iframe) : voir aussi auth._csrf_guard.
        current_app.logger.info(
            "Formulaire de connexion servi (cookie de session recu : %s, contexte : %r)",
            bool(request.cookies.get("session")), request.headers.get("Sec-Fetch-Dest"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        if login_attempts_exhausted(username):
            erreur = "Trop de tentatives. Attendez quelques minutes avant de reessayer."
        else:
            row = db.one("SELECT * FROM users WHERE username = ?", (username,))
            if row and db.verify_password(password, row["password_hash"]):
                # Case "rester connecte" du formulaire (cochee par defaut).
                rester = str(request.form.get("rester_connecte") or "").lower() not in {
                    "", "0", "non", "false", "off"}
                login(row, rester_connecte=rester)
                return redirect(url_avec_jeton(_destination_sure(request.args.get("next"))))
            register_failed_attempt(username)
            erreur = "Identifiant ou mot de passe incorrect."
    return render_template("login.html", erreur=erreur)


@bp.route("/deconnexion", methods=["POST"])
def deconnexion():
    logout()
    return redirect(url_for("views.connexion"))


# --------------------------------------------------------------------------
# Vitrine officielle de presentation (accessible publiquement)
# --------------------------------------------------------------------------

@bp.route("/presentation")
def presentation():
    """Page vitrine officielle et espace interactif complet de la plateforme Balise Prospection."""
    # Préparation du catalogue complet d'entreprises locales réalistes avec états de site
    entreprises_raw = [dict(c) for c in demo_data.DEMO_COMPANIES]
    etats = detect.etats_effectifs_lot(entreprises_raw)
    for c in entreprises_raw:
        etat = etats.get(c["siren"])
        c["site"] = etat or {"status": "aucun", "domain": None, "source": "dns", "checked_at": None}
        c["tracked"] = False
        c["statut"] = "a_contacter"

    sections_activite = [
        ("A", "Agriculture, sylviculture et pêche"),
        ("B", "Industries extractives"),
        ("C", "Industrie manufacturière"),
        ("D", "Énergie"),
        ("E", "Eau, assainissement, déchets"),
        ("F", "Construction (artisans du bâtiment)"),
        ("G", "Commerce et réparation automobile"),
        ("H", "Transports et entreposage"),
        ("I", "Hébergement et restauration"),
        ("J", "Information et communication"),
        ("K", "Finance et assurance"),
        ("L", "Immobilier"),
        ("M", "Activités spécialisées, scientifiques"),
        ("N", "Services administratifs et de soutien"),
        ("P", "Enseignement"),
        ("Q", "Santé et action sociale"),
        ("R", "Arts, spectacles et loisirs"),
        ("S", "Autres services (coiffure, beauté…)"),
    ]

    tranches_effectif = [
        ("", "Tous les effectifs"),
        ("0", "0 salarié"),
        ("01", "1 ou 2 salariés"),
        ("02", "3 à 5 salariés"),
        ("03", "6 à 9 salariés"),
        ("11", "10 à 19 salariés"),
        ("12", "20 à 49 salariés"),
        ("21", "50 à 99 salariés"),
        ("22", "100 à 249 salariés"),
    ]

    return render_template(
        "presentation.html",
        page_id="presentation",
        titre="Balise — Prospection B2B locale pour créateurs de sites web",
        site_name=current_app.config["SITE_NAME"],
        entreprises=entreprises_raw,
        raisons_masquage=RAISONS_MASQUAGE,
        statuts_pipeline=STATUTS_PIPELINE,
        sections_activite=sections_activite,
        tranches_effectif=tranches_effectif,
        est_connecte=bool(session.get("uid")),
    )


def _compte(sql: str, params=()) -> int:
    """Compte en base, tolerant : une table absente ne doit pas casser une page.

    Utilise par les pages d'accueil et de presentation, qui affichent des
    indicateurs : mieux vaut un zero explique qu'une erreur 500 (et jamais un
    chiffre invente, qui ne serait pas une mesure).
    """
    try:
        return db.one(sql, params)["n"]
    except (sqlite3.Error, KeyError, TypeError):
        return 0


# --------------------------------------------------------------------------
# Accueil (page d'explication, affichee juste apres la connexion)
# --------------------------------------------------------------------------

@bp.route("/accueil")
@login_required
def accueil():
    """Page d'accueil : explication de l'outil et indicateurs reels de l'equipe."""
    def compte(sql, params=()):
        return _compte(sql, params)

    suivies = compte("SELECT COUNT(*) n FROM tracked")
    par_statut = {r["status"]: r["n"] for r in db.query(
        "SELECT status, COUNT(*) n FROM tracked GROUP BY status")}
    pipeline = [
        {"code": code, "label": label, "n": par_statut.get(code, 0)}
        for code, label in STATUTS_PIPELINE
    ]
    max_pipeline = max([etape["n"] for etape in pipeline] + [1])

    stats = {
        "suivies": suivies,
        "a_contacter": par_statut.get("a_contacter", 0),
        "masquees": compte("SELECT COUNT(*) n FROM hides WHERE restored_at IS NULL"),
        "sans_site": compte("SELECT COUNT(*) n FROM site_cache WHERE status = 'aucun'"),
        "avec_site": compte("SELECT COUNT(*) n FROM site_cache WHERE status = 'site'"),
        "analysees": compte("SELECT COUNT(*) n FROM site_cache"),
        "clients": par_statut.get("client", 0),
    }
    return render_template(
        "accueil.html",
        page_id="accueil",
        titre="Bienvenue dans Balise Prospection",
        sous_titre=("L'outil des associés pour trouver, qualifier et suivre les "
                    "entreprises qui n'ont pas encore de site web"),
        stats=stats,
        pipeline=pipeline,
        pipeline_max=max_pipeline,
        taux_sans_site=(round(100 * stats["sans_site"] / stats["analysees"])
                        if stats["analysees"] else 0),
    )


# --------------------------------------------------------------------------
# Recherche
# --------------------------------------------------------------------------

def _normalise_departement(dep: str) -> str:
    """Normalise un code ou nom de departement saisi dans les filtres."""
    dep = (dep or "").strip().upper()
    if not dep:
        return ""
    if dep in gov_api.DEPARTEMENTS_FR:
        return dep
    if dep.isdigit() and len(dep) == 3 and dep.startswith("0") and dep[1:] in gov_api.DEPARTEMENTS_FR:
        return dep[1:]
    dep_slug = gov_api.slug_ascii(dep).lower().replace("-", " ")
    for code, nom in gov_api.DEPARTEMENTS_FR.items():
        nom_slug = gov_api.slug_ascii(nom).lower().replace("-", " ")
        if dep_slug == nom_slug:
            return code
    return dep


def _parse_geo_dans_requete(q: str, dep: str, cp: str):
    """Extrait le departement ou code postal glisse dans le champ principal."""
    q = (q or "").strip()
    dep = _normalise_departement(dep)
    cp = (cp or "").strip()

    # Si c'est un SIREN (9 chiffres) ou SIRET (14 chiffres), on ne touche a rien.
    chiffres_seuls = re.sub(r"\s+", "", q)
    if chiffres_seuls.isdigit() and len(chiffres_seuls) in (9, 14):
        return q, dep, cp

    # Code postal a 5 chiffres dans la requete (ex. "boulangerie 91000", "91000")
    m_cp = re.search(r"\b(0[1-9]\d{3}|[1-8]\d{4}|9[0-8]\d{3})\b", q)
    if m_cp:
        trouve_cp = m_cp.group(1)
        if not cp:
            cp = trouve_cp
        if not dep:
            # Meme deduction que pour les fiches officielles : « 20200 » vaut 2A
            # (le departement 20 n'existe pas) et non « 20 », qui ne renvoyait
            # aucun resultat sans explication.
            dep = gov_api.departement_depuis_code_postal(trouve_cp)
        q = (q[:m_cp.start()] + " " + q[m_cp.end():]).strip()
        q = re.sub(r"\s+", " ", q)

    # Noms de departements (ex. "boulangerie essonne", "restaurant seine-et-marne")
    if not dep:
        q_norm = gov_api.slug_ascii(q).lower()
        noms_tries = sorted(gov_api.DEPARTEMENTS_FR.items(), key=lambda x: -len(x[1]))
        for code, nom in noms_tries:
            nom_norm = gov_api.slug_ascii(nom).lower().replace("-", " ")
            pattern = r"\b" + re.escape(nom_norm) + r"\b"
            if re.search(pattern, q_norm.replace("-", " ")):
                dep = code
                pattern_orig = r"\b" + r"[\s\-]".join(re.escape(w) for w in nom_norm.split()) + r"\b"
                q = re.sub(pattern_orig, "", q, flags=re.IGNORECASE).strip()
                q = re.sub(r"\s+", " ", q)
                break

    # Prefixe explicite "dans le 91", "en 91", "dept 91", "departement 91"
    m_pref = re.search(
        r"\b(?:dans\s+l[e\']\s*|en\s+|dept\.?\s+|d[ée]partement\s+)(9[0-5]|2[AB]|97[1-6]|0[1-9]|[1-8][0-9])\b",
        q, re.IGNORECASE,
    )
    if m_pref:
        trouve_dep = m_pref.group(1).upper()
        if not dep:
            dep = trouve_dep
        q = (q[:m_pref.start()] + " " + q[m_pref.end():]).strip()
        q = re.sub(r"\s+", " ", q)

    # Departement en fin/debut de requete ou requete composee uniquement du departement ("91")
    if not dep:
        m_dep = re.search(
            r"(?:^|\s)(9[0-5]|2[AB]|97[1-6]|0[1-9]|[1-8][0-9])(?:\s|$)",
            q, re.IGNORECASE,
        )
        if m_dep:
            trouve_dep = m_dep.group(1).upper()
            dep = trouve_dep
            q = (q[:m_dep.start()] + " " + q[m_dep.end():]).strip()
            q = re.sub(r"\s+", " ", q)

    return q, dep, cp


def _params_recherche():
    q = (request.args.get("q") or "").strip()
    dep = _normalise_departement(request.args.get("departement") or "")
    cp = (request.args.get("code_postal") or "").strip()
    q, dep, cp = _parse_geo_dans_requete(q, dep, cp)
    return {
        "q": q,
        "departement": dep,
        "code_postal": cp,
        "naf": (request.args.get("naf") or "").strip(),
        "section": (request.args.get("section") or "").strip(),
        "effectif": (request.args.get("effectif") or "").strip(),
        "sans_site": request.args.get("sans_site") == "1",
        "inclure_grandes": request.args.get("inclure_grandes") == "1",
        "inclure_masquees": request.args.get("inclure_masquees") == "1",
        "inclure_fermees": request.args.get("inclure_fermees") == "1",
        # Jeu de demonstration : uniquement sur demande explicite.
        "demo": request.args.get("demo") == "1" and bool(current_app.config["DEMO_ALLOWED"]),
        "page": max(1, request.args.get("page", 1, type=int) or 1),
    }


def _a_des_criteres(p) -> bool:
    return bool(p["q"] or p["departement"] or p["code_postal"] or p["naf"]
                or p["section"] or p["effectif"])


# Motifs pour lesquels un resultat officiel n'est pas affiche : chacun est compte
# et annonce a l'utilisateur, pour qu'un resultat ecarte ne soit jamais invisible.
ECART_MASQUEE = "masquees"
ECART_GRANDE = "grandes"
ECART_ZONE = "hors_zone"
LIBELLES_ECART = {
    ECART_MASQUEE: ("masquée", "masquées"),
    ECART_GRANDE: ("grande enseigne", "grandes enseignes"),
    ECART_ZONE: ("hors zone", "hors zone"),
}


def _raison_exclusion(c, p, masquees) -> str:
    """Pourquoi ce prospect est ecarte, ou None s'il est affichable.

    Masquage, grandes entreprises et localisation etaient jusqu'ici melanges dans
    un simple booleen : impossible de dire a l'utilisateur combien de resultats
    avaient disparu, ni pourquoi.
    """
    if not p["inclure_masquees"] and c["siren"] in masquees:
        return ECART_MASQUEE
    if not p.get("inclure_grandes") and gov_api.est_grande_entreprise(c):
        return ECART_GRANDE
    if p["departement"] and c.get("departement") != p["departement"]:
        return ECART_ZONE
    if p["code_postal"] and c.get("code_postal") != p["code_postal"]:
        return ECART_ZONE
    return None


def _synthese_ecartes(comptes: dict) -> str:
    """« 12 masquées, 8 grandes enseignes » (ou chaine vide si rien n'est ecarte)."""
    morceaux = []
    for code in (ECART_MASQUEE, ECART_GRANDE, ECART_ZONE):
        n = comptes.get(code, 0)
        if not n:
            continue
        singulier, pluriel = LIBELLES_ECART[code]
        morceaux.append(f"{n} {singulier if n == 1 else pluriel}")
    return ", ".join(morceaux)


def _valide_entreprise(c, p, masquees):
    """Controle la validite d'un prospect (masquage, grandes entreprises, localisation)."""
    return _raison_exclusion(c, p, masquees) is None


def _chercher(p, max_pages: int):
    """Recherche avec detection, filtre 'sans site' et exclusion des masquees.

    Renvoie un dict : items, total_results, page, total_pages, demo, analysees.
    """
    masquees = {r["siren"] for r in db.query(
        "SELECT siren FROM hides WHERE restored_at IS NULL")}

    def recherche_source(page: int):
        try:
            res = gov_api.search(
                q=p["q"], page=page, departement=p["departement"],
                code_postal=p["code_postal"], naf=p["naf"], section=p["section"],
                effectif=p["effectif"], actives=not p["inclure_fermees"],
            )
            return res, False
        except gov_api.ApiError as exc:
            # Aucune donnee inventee : soit l'utilisateur a explicitement demande le
            # jeu de demonstration, soit la recherche echoue en le disant.
            if not p.get("demo"):
                current_app.logger.warning(
                    "API officielle injoignable (q=%r, departement=%r, page=%s)",
                    p["q"], p["departement"], page)
                raise BaseIndisponible(str(exc)) from exc
            current_app.logger.info(
                "Jeu de demonstration utilise a la demande (q=%r, departement=%r)",
                p["q"], p["departement"])
            return demo_data.cherche(
                q=p["q"], departement=p["departement"], code_postal=p["code_postal"],
                section=p["section"], effectif=p["effectif"],
                actives=not p["inclure_fermees"], page=page,
                inclure_grandes=p.get("inclure_grandes", False),
            ), True

    ecartes: dict = {}

    def compte_ecartes(lot):
        for c in lot:
            raison = _raison_exclusion(c, p, masquees)
            if raison:
                ecartes[raison] = ecartes.get(raison, 0) + 1

    if not p["sans_site"]:
        premier, demo = recherche_source(p["page"])
        compte_ecartes(premier["items"])
        items = [c for c in premier["items"] if _valide_entreprise(c, p, masquees)]
        detect.verifie_lot(items)
        return {
            "items": items, "total_results": premier["total_results"],
            "page": premier["page"], "total_pages": premier["total_pages"],
            "demo": demo, "analysees": len(premier["items"]),
            "suite_possible": False, "ecartes": ecartes,
        }

    # Filtre "sans site" actif : on analyse toujours depuis la premiere page de
    # l'API, sinon le jeu de resultats change d'une page a l'autre.
    collectees: list = []
    api_page = 1
    analysees = 0
    epuise = False
    batch, demo = recherche_source(1)
    while True:
        compte_ecartes(batch["items"])
        candidats = [c for c in batch["items"] if _valide_entreprise(c, p, masquees)]
        detect.verifie_lot(candidats)
        analysees += len(batch["items"])
        etats = detect.etats_effectifs_lot(candidats)
        for c in candidats:
            etat = etats.get(c["siren"])
            if etat and etat["status"] == "aucun":
                collectees.append(c)
        epuise = api_page >= batch["total_pages"]
        if len(collectees) >= p["page"] * PER_PAGE or epuise:
            break
        if api_page >= max_pages:
            break
        api_page += 1
        batch, demo = recherche_source(api_page)
        if not batch.get("items"):
            epuise = True
            break

    total = len(collectees)
    total_pages = max(1, math.ceil(total / PER_PAGE))
    page = min(p["page"], total_pages)
    debut = (page - 1) * PER_PAGE
    return {
        "items": collectees[debut:debut + PER_PAGE],
        "total_results": total,
        "page": page,
        "total_pages": total_pages,
        "demo": demo,
        "analysees": analysees,
        "suite_possible": not epuise,
        "ecartes": ecartes,
    }


def _attache_etats(items):
    """Attache a chaque entreprise son etat de detection, suivi et masquage."""
    if not items:
        return items
    sirens = [c["siren"] for c in items]
    # Les trous de la clause IN sont uniquement des "?" : aucune valeur concatenee.
    trous = ",".join("?" * len(sirens))
    tracked = {r["siren"]: r for r in db.query(
        f"SELECT * FROM tracked WHERE siren IN ({trous})", sirens)}  # noqa: S608
    masquees = {r["siren"]: r for r in db.query(
        f"SELECT siren FROM hides WHERE restored_at IS NULL AND siren IN ({trous})",  # noqa: S608
        sirens)}
    etats = detect.etats_effectifs_lot(items)
    for c in items:
        etat = etats.get(c["siren"])
        c["site"] = etat or {"status": "inconnu", "domain": None, "source": "dns",
                             "checked_at": None}
        t = tracked.get(c["siren"])
        c["tracked"] = bool(t)
        c["statut"] = t["status"] if t else None
        c["masquee"] = c["siren"] in masquees
    return items


@bp.route("/")
@login_required
def recherche():
    p = _params_recherche()
    ctx = {
        "page_id": "recherche",
        "titre": "Trouvez les entreprises qui n'ont pas encore de site",
        "sous_titre": (
            "Recherchez des entreprises locales, filtrez les prospects pertinents "
            "et identifiez rapidement ceux dont aucun site web n'a été détecté."
        ),
        "p": p,
        "recherche_lancee": _a_des_criteres(p),
        "resultats": None,
        "erreur": None,
        # Utile pour expliquer un resultat vide en mode demonstration.
        "departements_demo": demo_data.DEPARTEMENTS,
        "nb_demo": len(demo_data.DEMO_COMPANIES),
        "demo_disponible": bool(current_app.config["DEMO_ALLOWED"]),
    }
    qs = {k: v for k, v in {
        "q": p["q"], "departement": p["departement"], "code_postal": p["code_postal"],
        "naf": p["naf"], "section": p["section"], "effectif": p["effectif"],
        "sans_site": "1" if p["sans_site"] else "",
        "inclure_grandes": "1" if p["inclure_grandes"] else "",
        "inclure_masquees": "1" if p["inclure_masquees"] else "",
        "inclure_fermees": "1" if p["inclure_fermees"] else "",
        "demo": "1" if p["demo"] else "",
    }.items() if v}
    ctx["qs_base"] = urlencode(qs)
    # Lien vers le jeu de demonstration (entreprises fictives), sur demande explicite.
    ctx["demo_liens"] = urlencode({**qs, "demo": "1"})
    if ctx["recherche_lancee"]:
        try:
            resultats = _chercher(p, MAX_PAGES_EXPANSION)
            resultats["items"] = _attache_etats(resultats["items"])
            resultats["ecartes_texte"] = _synthese_ecartes(resultats.get("ecartes") or {})
            ctx["resultats"] = resultats
        except BaseIndisponible as exc:
            ctx["erreur"] = ("La base officielle (recherche-entreprises.api.gouv.fr) est "
                             f"injoignable depuis cet environnement. {exc}")
        except Exception:
            current_app.logger.exception(
                "Recherche en echec (q=%r, departement=%r, page=%s)",
                p["q"], p["departement"], p["page"])
            ctx["erreur"] = ("Une erreur inattendue est survenue pendant la recherche. "
                             "Réessayez dans un instant.")
    return render_template("search.html", **ctx)


@bp.route("/export.csv")
@login_required
def export_csv():
    p = _params_recherche()
    if not _a_des_criteres(p):
        abort(400)
    try:
        res = _chercher(p, MAX_PAGES_EXPORT)
    except BaseIndisponible as exc:
        abort(503, f"Export impossible : la base officielle est injoignable. {exc}")
    items = _attache_etats(res["items"])

    tampon = io.StringIO()
    writer = csv.writer(tampon, delimiter=";")
    writer.writerow([
        "SIREN", "SIRET siège", "Dénomination", "Forme juridique", "Code NAF",
        "Libellé activité", "Adresse", "Code postal", "Commune", "Département",
        "Région", "Effectif", "Date de création", "Dirigeant(s)",
        "Site détecté", "Domaine", "Suivi", "Statut pipeline", "Source des données",
    ])
    source = ("Jeu de démonstration (entreprises fictives)" if res["demo"]
              else "Base officielle INSEE / RNE")
    for c in items:
        dirigeants_list = c.get("dirigeants") or []
        dirigeants = " / ".join(
            (d.get("nom") or "") for d in dirigeants_list if isinstance(d, dict) and d.get("nom")
        )
        site = c.get("site") or {}
        statut_site = site.get("status")
        writer.writerow([
            c["siren"], c.get("siret_siege") or "", c["nom"], c.get("forme") or "",
            c.get("naf_code") or "", c.get("naf_label") or "", c.get("adresse") or "",
            c.get("code_postal") or "", c.get("commune") or "", c.get("departement") or "",
            c.get("region") or "", c.get("effectif") or "",
            (c.get("date_creation") or "")[:10],
            dirigeants,
            "non" if statut_site == "aucun" else ("oui" if statut_site == "site" else "inconnu"),
            site.get("domain") or "",
            "oui" if c.get("tracked") else "non",
            c.get("statut") or "",
            source,
        ])
    donnees = "\ufeff" + tampon.getvalue()
    marqueur = "-DEMO" if res["demo"] else ""
    nom_fichier = f"prospection-balise{marqueur}-{datetime.now(timezone.utc):%Y%m%d}.csv"
    return Response(
        donnees, mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={nom_fichier}"},
    )


# --------------------------------------------------------------------------
# Fiche entreprise (panneau lateral)
# --------------------------------------------------------------------------

def _entreprise_complete(siren: str, demo_autorise: bool = False):
    """Recherche l'entreprise : API officielle, sinon instantane local (jamais inventee)."""
    try:
        c = gov_api.fetch_by_siren(str(siren))
        if c:
            return c, "api"
    except gov_api.ApiError:
        pass
    if demo_autorise:
        c = demo_data.par_siren(siren)
        if c:
            return c, "demo"
    row = db.one("SELECT snapshot FROM tracked WHERE siren = ?", (siren,))
    if row:
        try:
            return json.loads(row["snapshot"]), "instantane"
        except ValueError:
            pass
    row = db.one("SELECT snapshot FROM hides WHERE siren = ? ORDER BY id DESC", (siren,))
    if row and row["snapshot"]:
        try:
            return json.loads(row["snapshot"]), "instantane"
        except ValueError:
            pass
    return None, None


@bp.route("/entreprise/<siren>/detail")
@login_required
def detail_entreprise(siren):
    if not re.fullmatch(r"\d{9}", siren or ""):
        abort(404)
    demo = request.args.get("demo") == "1" and bool(current_app.config["DEMO_ALLOWED"])
    company, source = _entreprise_complete(siren, demo_autorise=demo)
    if not company:
        return render_template("partials/detail_indisponible.html", siren=siren), 200
    try:
        company["site"] = detect.verifie(company)
    except Exception:
        current_app.logger.exception("Detection de site impossible pour %s", siren)
        company["site"] = {"status": "inconnu", "domain": None, "source": "dns",
                           "checked_at": None}
    company["source"] = source
    track = db.one("SELECT * FROM tracked WHERE siren = ?", (siren,))
    company["tracked"] = bool(track)
    company["statut"] = track["status"] if track else None
    hide = db.one(
        "SELECT * FROM hides WHERE siren = ? AND restored_at IS NULL", (siren,))
    company["masquee"] = bool(hide)
    company["hide_id"] = hide["id"] if hide else None
    return render_template("partials/detail.html", c=company)


# --------------------------------------------------------------------------
# API d'actions (JSON)
# --------------------------------------------------------------------------

def _json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


# Champs d'une fiche que le serveur accepte de conserver dans un instantane. Le
# navigateur renvoie la ligne qu'il a recue ; le filtrage ci-dessous fait que
# c'est le serveur, et non le client, qui decide ce qui entre en base.
CHAMPS_SNAPSHOT = (
    "siren", "nom", "sigle", "forme", "nature_code", "date_creation", "date_fermeture",
    "categorie", "actif", "naf_code", "naf_label", "section", "effectif_code", "effectif",
    "nb_etablissements", "adresse", "rue", "code_postal", "commune", "departement",
    "region", "siret_siege", "siret_etab", "date_debut_activite", "enseigne", "lat", "lng",
    "dirigeants", "finances", "tva", "complements", "source", "site", "tracked", "statut",
)
LONGUEUR_TEXTE_SNAPSHOT = 500      # longueur maximale d'un champ texte
TAILLE_SNAPSHOT_MAX = 32_000       # taille maximale de l'instantane conserve (octets)


def _valeur_snapshot(valeur):
    """Valeur d'instantane nettoyee : texte tronque, conteneur borne, type verifie."""
    if isinstance(valeur, bool) or valeur is None or isinstance(valeur, (int, float)):
        return valeur
    if isinstance(valeur, str):
        return valeur[:LONGUEUR_TEXTE_SNAPSHOT]
    if isinstance(valeur, list):
        return [_valeur_snapshot(v) for v in valeur[:12]]
    if isinstance(valeur, dict):
        return {str(cle)[:60]: _valeur_snapshot(v)
                for cle, v in list(valeur.items())[:20]}
    return None


def _nettoie_snapshot(brut) -> dict:
    """Ne conserve d'un instantane que des champs connus, bornes et bien types.

    L'instantane vient du navigateur (ligne du tableau, panneau fiche) : il etait
    jusqu'ici stocke tel quel, si bien que le client choisissait le contenu de la
    base. Un instantane trop volumineux est ignore : la fiche sera relue depuis la
    base officielle, ce qui vaut mieux que de stocker n'importe quoi.
    """
    if not isinstance(brut, dict):
        return {}
    propre = {cle: _valeur_snapshot(brut[cle])
              for cle in CHAMPS_SNAPSHOT if cle in brut}
    if len(json.dumps(propre, ensure_ascii=False)) > TAILLE_SNAPSHOT_MAX:
        current_app.logger.warning(
            "Instantane refuse (au-dela de %s octets) : la fiche sera relue a l'ouverture.",
            TAILLE_SNAPSHOT_MAX)
        return {}
    return propre


def _texte_court(valeur, longueur: int = 200) -> str:
    """Champ texte libre envoye par le client, borne avant enregistrement."""
    return str(valeur or "").strip()[:longueur]


def _badge_site(c):
    return render_template("partials/badge_site.html", c=c).strip()


@bp.route("/api/masquer", methods=["POST"])
@login_required
def api_masquer():
    d = _json()
    siren = str(d.get("siren") or "")
    nom = _texte_court(d.get("nom"))
    raison = str(d.get("raison") or "").strip()
    details = _texte_court(d.get("details"), 500)
    if not re.fullmatch(r"\d{9}", siren) or not nom:
        return jsonify({"ok": False, "error": "Entreprise invalide."}), 400
    if raison not in RAISONS_MASQUAGE:
        return jsonify({"ok": False, "error": "Indiquez une raison de masquage."}), 400
    if raison == "Autre" and not details:
        return jsonify({"ok": False, "error": "Précisez la raison (champ détails)."}), 400
    deja = db.one("SELECT id FROM hides WHERE siren = ? AND restored_at IS NULL", (siren,))
    ecritures = []
    if not deja:
        ecritures.append((
            "INSERT INTO hides (siren, nom, raison, details, snapshot, hidden_by, hidden_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (siren, nom, raison, details,
             json.dumps(_nettoie_snapshot(d.get("snapshot")), ensure_ascii=False),
             current_user()["username"], db.now_iso()),
        ))
    # Masquage et retrait du portefeuille dans la meme transaction : jamais
    # d'entreprise a la fois masquee et suivie.
    ecritures.append(("DELETE FROM tracked WHERE siren = ?", (siren,)))
    db.run_all(ecritures)
    return jsonify({"ok": True})


@bp.route("/api/retablir", methods=["POST"])
@login_required
def api_retablir():
    d = _json()
    try:
        hide_id = int(d.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Identifiant invalide."}), 400
    row = db.one("SELECT id FROM hides WHERE id = ? AND restored_at IS NULL", (hide_id,))
    if not row:
        return jsonify({"ok": False, "error": "Entrée introuvable ou déjà restaurée."}), 404
    db.execute(
        "UPDATE hides SET restored_at = ?, restored_by = ? WHERE id = ?",
        (db.now_iso(), current_user()["username"], hide_id),
    )
    return jsonify({"ok": True})


@bp.route("/api/suivre", methods=["POST"])
@login_required
def api_suivre():
    d = _json()
    snapshot = _nettoie_snapshot(d.get("snapshot"))
    siren = str(snapshot.get("siren") or "")
    if not re.fullmatch(r"\d{9}", siren) or not snapshot.get("nom"):
        return jsonify({"ok": False, "error": "Entreprise invalide."}), 400
    deja = db.one("SELECT id FROM tracked WHERE siren = ?", (siren,))
    if deja:
        db.execute("DELETE FROM tracked WHERE id = ?", (deja["id"],))
        return jsonify({"ok": True, "tracked": False})
    moi = current_user()["username"]
    maintenant = db.now_iso()
    db.run_all([
        (
            "INSERT INTO tracked (siren, snapshot, added_by, added_at) VALUES (?, ?, ?, ?)",
            (siren, json.dumps(snapshot, ensure_ascii=False), moi, maintenant),
        ),
        (
            "UPDATE hides SET restored_at = ?, restored_by = ? WHERE siren = ? AND restored_at IS NULL",
            (maintenant, moi, siren),
        ),
    ])
    return jsonify({"ok": True, "tracked": True})


@bp.route("/api/statut", methods=["POST"])
@login_required
def api_statut():
    d = _json()
    siren = str(d.get("siren") or "")
    statut = str(d.get("statut") or "")
    if statut not in dict(STATUTS_PIPELINE):
        return jsonify({"ok": False, "error": "Statut inconnu."}), 400
    if not db.one("SELECT id FROM tracked WHERE siren = ?", (siren,)):
        return jsonify({"ok": False, "error": "Entreprise non suivie."}), 404
    db.execute(
        "UPDATE tracked SET status = ?, status_updated_by = ?, status_updated_at = ?"
        " WHERE siren = ?",
        (statut, current_user()["username"], db.now_iso(), siren),
    )
    return jsonify({"ok": True})


@bp.route("/api/recheck", methods=["POST"])
@login_required
def api_recheck():
    d = _json()
    siren = str(d.get("siren") or "")
    if not re.fullmatch(r"\d{9}", siren):
        return jsonify({"ok": False, "error": "SIREN invalide."}), 400
    company = {"siren": siren, "nom": _texte_court(d.get("nom")),
               "enseigne": _texte_court(d.get("enseigne"))}
    etat = detect.verifie(company, force=True)
    c = {"siren": siren, "site": etat}
    return jsonify({"ok": True, "site": etat, "badge_html": _badge_site(c)})


@bp.route("/api/site-override", methods=["POST"])
@login_required
def api_site_override():
    d = _json()
    siren = str(d.get("siren") or "")
    valeur = d.get("value")
    if not re.fullmatch(r"\d{9}", siren):
        return jsonify({"ok": False, "error": "SIREN invalide."}), 400
    if valeur == "sans":
        db.execute(
            "INSERT INTO overrides (siren, value, by, at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(siren) DO UPDATE SET value=excluded.value, by=excluded.by,"
            " at=excluded.at",
            (siren, "sans", current_user()["username"], db.now_iso()),
        )
    else:
        db.execute("DELETE FROM overrides WHERE siren = ?", (siren,))
    company = {"siren": siren, "nom": _texte_court(d.get("nom")),
               "enseigne": _texte_court(d.get("enseigne"))}
    etat = detect.etat_effectif(company)
    c = {"siren": siren, "site": etat or {"status": "inconnu", "domain": None,
                                          "source": "dns", "checked_at": None}}
    return jsonify({"ok": True, "site": c["site"], "badge_html": _badge_site(c)})


# --------------------------------------------------------------------------
# Portefeuille
# --------------------------------------------------------------------------

MOIS_LABELS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août",
               "sept.", "oct.", "nov.", "déc.")
BENEFICE_MOIS = 12          # profondeur du graphique d'encaissements


def _decalage_mois(annee: int, mois: int, recul: int) -> tuple[int, int]:
    """Recule de `recul` mois depuis (annee, mois), sans dependance externe."""
    index = annee * 12 + (mois - 1) - recul
    return index // 12, index % 12 + 1


def benefice_du_portefeuille():
    """Encaissements : totaux, panier moyen, serie mensuelle et detail par entreprise.

    La source est la table `benefices` (que le panel staff alimente). Les montants
    sont des entiers de centimes ; `encaisse_le` est une date locale (heure de Paris).
    """
    lignes = db.query(
        "SELECT id, siret, siren, libelle, montant_cents, encaisse_le, encaisse_par"
        " FROM benefices ORDER BY encaisse_le DESC, id DESC")

    total_cents = 0
    par_mois = {}
    par_siren = {}
    for ligne in lignes:
        montant = int(ligne["montant_cents"] or 0)
        total_cents += montant
        date = (ligne["encaisse_le"] or "")[:10]
        # Date locale : le mois se lit directement dessus. Sans date exploitable,
        # l'encaissement compte sur le mois courant.
        valide = len(date) >= 7 and date[:4].isdigit() and date[5:7].isdigit()
        cle = date[:7] if valide else db.now_iso()[:7]
        par_mois[cle] = par_mois.get(cle, 0) + montant
        if ligne["siren"]:
            cumul = par_siren.setdefault(ligne["siren"], {"cents": 0, "dernier": None})
            cumul["cents"] += montant
            if cumul["dernier"] is None or (ligne["encaisse_le"] or "") > cumul["dernier"]:
                cumul["dernier"] = ligne["encaisse_le"]

    maintenant = datetime.now(PARIS_TZ)
    serie = []
    for recul in range(BENEFICE_MOIS - 1, -1, -1):
        annee, mois = _decalage_mois(maintenant.year, maintenant.month, recul)
        cle = f"{annee:04d}-{mois:02d}"
        serie.append({
            "cle": cle,
            "label": MOIS_LABELS[mois - 1],
            "annee": annee,
            "mois": mois,
            "cents": par_mois.get(cle, 0),
        })
    maximum = max([point["cents"] for point in serie] + [0])
    cumul = 0
    for point in serie:
        point["hauteur"] = round(100 * point["cents"] / maximum) if maximum else 0
        point["pct_max"] = point["hauteur"]
        cumul += point["cents"]
        point["cumul_hauteur"] = round(100 * cumul / (total_cents or 1))

    clients = db.one("SELECT COUNT(*) n FROM tracked WHERE status = 'client'")["n"]
    return {
        "total_cents": total_cents,
        "clients": clients,
        "nombre": len(lignes),
        "panier_cents": round(total_cents / len(lignes)) if lignes else 0,
        "ce_mois_cents": par_mois.get(maintenant.strftime("%Y-%m"), 0),
        "serie": serie,
        "maximum_cents": maximum,
        "mois_courant": maintenant.strftime("%Y-%m"),
        "lignes": lignes,
        "par_siren": par_siren,
    }


# --------------------------------------------------------------------------
# Tableau de bord d'equipe : qui travaille quoi, et quels appels passer
# --------------------------------------------------------------------------

# Un client signe ou une affaire classee n'a plus besoin d'appel.
STATUTS_SANS_APPEL = {"client", "sans_suite"}


def _maintenant() -> datetime:
    """Heure de Paris : les dates de suivi sont locales, comme celles du benefice."""
    return datetime.now(PARIS_TZ)


def _jour_lisible(valeur: str) -> str:
    """'2026-09-25' -> '25/09/2026' (les dates de suivi sont deja locales)."""
    texte = (valeur or "")[:10]
    if len(texte) == 10 and texte[4] == "-":
        return f"{texte[8:10]}/{texte[5:7]}/{texte[:4]}"
    return texte or "-"


def _heure_de_paris(horodatage: str) -> str:
    """'2026-09-22T17:30:00Z' -> '22/09/2026 a 19:30' (heure de Paris)."""
    if not horodatage:
        return "-"
    try:
        quand = datetime.strptime(horodatage, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return str(horodatage)
    return quand.astimezone(PARIS_TZ).strftime("%d/%m/%Y à %H:%M")


def _etiquette_urgence(relance_le: str, aujourd_hui: str, appele_le: str) -> tuple[str, str]:
    """Classe un appel a passer : renvoie (code, libelle).

    Le retard d'abord (c'est ce qu'on regarde en premier le matin), puis
    aujourd'hui, puis les relances a venir ; sans date, l'entreprise reste a
    appeler des que possible.
    """
    if relance_le:
        if relance_le < aujourd_hui:
            jours = (datetime.strptime(aujourd_hui, "%Y-%m-%d")
                     - datetime.strptime(relance_le, "%Y-%m-%d")).days
            return "en_retard", f"En retard de {jours} jour(s)"
        if relance_le == aujourd_hui:
            return "aujourd_hui", "À appeler aujourd’hui"
        return "relance", f"Relance le {_jour_lisible(relance_le)}"
    if appele_le:
        return "relance", "Déjà appelée — à relancer"
    return "a_faire", "À appeler"


def _libelle_prise(pris_par: str, pris_le: str) -> str:
    """« Personne pour l'instant », « Prise par vous le ... », « Prise par X »."""
    if not pris_par:
        return "Personne pour l’instant"
    moi = (current_user() or {}).get("username")
    qui = "vous" if pris_par == moi else pris_par
    if pris_le:
        return f"Prise par {qui} le {_heure_de_paris(pris_le)}"
    return f"Prise par {qui}"


def _libelle_appels(c: dict) -> str:
    """Historique lisible : nombre d'appels, dernier appel, prochain appel."""
    if not c["appels"]:
        return "Aucun appel enregistré"
    morceaux = [f"{c['appels']} appel(s)"]
    if c["appele_le"]:
        fin = f" (par {c['appele_par']})" if c["appele_par"] else ""
        morceaux.append(f"dernier le {_jour_lisible(c['appele_le'])}{fin}")
    if c["relance_le"]:
        morceaux.append(f"prochain le {_jour_lisible(c['relance_le'])}")
    return " · ".join(morceaux)


def _suivi_de_l_entreprise(ligne, aujourd_hui: str, etats: dict = None,
                           masquees: set = None) -> dict:
    """Une entreprise suivie, avec son dossier et l'etat de son appel."""
    try:
        c = json.loads(ligne["snapshot"] or "{}")
        if not isinstance(c, dict):
            c = {}
    except ValueError:
        c = {}
    c["siren"] = ligne["siren"]
    c["nom"] = c.get("nom") or ligne["siren"]
    if etats is not None:
        c["site"] = etats.get(c["siren"]) or {"status": "inconnu", "domain": None,
                                               "source": "dns", "checked_at": None}
    else:
        c["site"] = detect.etat_effectif(c) or {"status": "inconnu", "domain": None,
                                                "source": "dns", "checked_at": None}
    c["tracked"] = True
    c["statut"] = ligne["status"]
    c["statut_label"] = dict(STATUTS_PIPELINE).get(ligne["status"], ligne["status"])
    c["added_by"] = ligne["added_by"]
    c["added_at"] = ligne["added_at"]
    c["pris_par"] = ligne["pris_par"] or ""
    c["pris_le"] = ligne["pris_le"] or ""
    c["pris_label"] = _libelle_prise(c["pris_par"], c["pris_le"])
    c["appele_le"] = ligne["appele_le"] or ""
    c["appele_par"] = ligne["appele_par"] or ""
    c["appels"] = int(ligne["appels"] or 0)
    c["relance_le"] = ligne["relance_le"] or ""
    c["note"] = ligne["note"] or ""
    c["historique"] = _libelle_appels(c)
    c.update(_livrables_de(ligne))
    # Libelles prets a afficher (page et reponses aux actions utilisent les memes).
    c["prise_par_moi"] = c["pris_par"] == (current_user() or {}).get("username")
    c["pris_le_texte"] = _heure_de_paris(c["pris_le"]) if c["pris_le"] else ""
    c["dernier_appel"] = _jour_lisible(c["appele_le"]) if c["appele_le"] else "—"
    c["appels_label"] = f"{c['appels']} appel(s)" if c["appels"] else "—"
    if masquees is not None:
        c["masquee"] = ligne["siren"] in masquees
    else:
        c["masquee"] = bool(db.one("SELECT id FROM hides WHERE siren = ? AND restored_at IS NULL",
                                   (ligne["siren"],)))
    c["urgence"], c["urgence_label"] = _etiquette_urgence(
        c["relance_le"], aujourd_hui, c["appele_le"])
    # Cle de tri : le retard d'abord, puis aujourd'hui, puis les relances datees.
    c["ordre"] = {"en_retard": 0, "aujourd_hui": 1}.get(c["urgence"], 2)
    return c


def _charge_de_l_equipe(membres, entreprises) -> list[dict]:
    """Charge de chacun, en une ligne : ce que personne ne fait n'est pas visible ici."""
    debut_semaine = (_maintenant() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")
    charge = []
    for m in membres:
        miennes = [c for c in entreprises if c["pris_par"] == m["username"]]
        charge.append({
            "nom": m["display_name"] or m["username"],
            "prises": len(miennes),
            "appels_7j": len([c for c in entreprises if c["appele_par"] == m["username"]
                              and c["appele_le"] >= debut_semaine]),
            "en_retard": len([c for c in miennes if c["urgence"] == "en_retard"]),
        })
    return charge


def _donnees_de_suivi() -> dict:
    """Tout le tableau de bord : file d'appels classee, charge de chacun, compteurs."""
    aujourd_hui = _maintenant().strftime("%Y-%m-%d")
    lignes = db.query("SELECT * FROM tracked ORDER BY added_at DESC")
    sirens = [r["siren"] for r in lignes]
    masquees = set()
    etats = {}
    if sirens:
        trous = ",".join("?" * len(sirens))
        masquees = {r["siren"] for r in db.query(
            f"SELECT siren FROM hides WHERE restored_at IS NULL AND siren IN ({trous})",  # noqa: S608
            sirens)}
        candidates = []
        for r in lignes:
            try:
                snap = json.loads(r["snapshot"] or "{}")
                if not isinstance(snap, dict):
                    snap = {}
            except ValueError:
                snap = {}
            snap["siren"] = r["siren"]
            candidates.append(snap)
        etats = detect.etats_effectifs_lot(candidates)

    entreprises = [_suivi_de_l_entreprise(ligne, aujourd_hui, etats=etats, masquees=masquees)
                   for ligne in lignes]

    membres = [dict(r) for r in db.query(
        "SELECT username, display_name, role FROM users ORDER BY id")]

    # File d'appels : tout ce qui n'est ni client ni sans suite. Une entreprise
    # masquee disparait de la file : elle ne sera pas appelee.
    a_appeler = [c for c in entreprises
                 if c["statut"] not in STATUTS_SANS_APPEL and not c["masquee"]]
    a_appeler.sort(key=lambda c: (c["ordre"], c["relance_le"] or "9999-99-99",
                                  c["added_at"] or ""))
    return {
        "appels": a_appeler,
        "equipe": _charge_de_l_equipe(membres, entreprises),
        "aujourd_hui": aujourd_hui,
        "compteurs": {
            "en_retard": len([c for c in a_appeler if c["urgence"] == "en_retard"]),
            "aujourd_hui": len([c for c in a_appeler if c["urgence"] == "aujourd_hui"]),
            "reste": len([c for c in a_appeler if c["urgence"] in {"a_faire", "relance"}]),
            "personne": len([c for c in a_appeler if not c["pris_par"]]),
        },
    }


@bp.route("/suivi")
@login_required
def suivi():
    """Tableau de bord d'equipe : appels a passer et repartition du travail."""
    return render_template(
        "suivi.html",
        page_id="suivi",
        titre="Suivi commercial",
        sous_titre="Gardez une vue claire sur les prospects à traiter et les prochaines actions.",
        **_donnees_de_suivi(),
    )


def _entreprise_suivie(siren: str):
    """Ligne `tracked` de ce SIREN, ou None : ces actions ne visent que le portefeuille."""
    if not re.fullmatch(r"\d{9}", siren or ""):
        return None
    return db.one("SELECT * FROM tracked WHERE siren = ?", (siren,))


def _etat_pour_js(siren: str) -> dict:
    """Etat renvoye au navigateur : des libelles deja calcules, rien a recomposer."""
    ligne = db.one("SELECT * FROM tracked WHERE siren = ?", (siren,))
    if not ligne:
        return {"ok": False, "error": "Entreprise non suivie."}
    c = _suivi_de_l_entreprise(ligne, _maintenant().strftime("%Y-%m-%d"))
    return {
        "ok": True,
        "siren": c["siren"],
        "pris_par": c["pris_par"],
        "pris_label": c["pris_label"],
        "prise_par_moi": c["prise_par_moi"],
        "referent_html": render_template("partials/suivi_referent.html", c=c).strip(),
        "statut": c["statut"],
        "statut_label": c["statut_label"],
        "urgence": c["urgence"],
        "urgence_label": c["urgence_label"],
        "appels": c["appels"],
        "appels_label": c["appels_label"],
        "dernier_appel": c["dernier_appel"],
        "historique": c["historique"],
        "note": c["note"],
        "relance_le": c["relance_le"],
        "badge_html": _badge_site(c),
        "livrables_html": _cellule_livrables(ligne),
        "nom": c["nom"],
    }


# --------------------------------------------------------------------------
# Repertoire : ajouter une entreprise, et ranger ses livrables
# --------------------------------------------------------------------------

# Taille maximale d'une archive televersee, et extensions acceptees.
ZIP_MAX_OCTETS = 25 * 1024 * 1024
LIVRABLES_DOSSIER = "livrables"


def _dossier_livrables() -> str:
    """Dossier local ou sont rangees les archives deposees (hors depots Git)."""
    chemin = os.path.join(current_app.config["DATA_DIR"], LIVRABLES_DOSSIER)
    os.makedirs(chemin, exist_ok=True)
    return chemin


def _adresse_valide(valeur: str) -> bool:
    """Vrai pour une adresse http(s) complete : c'est ce qu'on peut ouvrir."""
    return bool(re.match(r"^https?://[^\s/$.?#][^\s]*$", valeur or ""))


def _cellule_livrables(ligne) -> str:
    """Cellule « Livrables » de la ligne du repertoire (page et reponses d'action)."""
    c = {"siren": ligne["siren"]}
    c.update(_livrables_de(ligne))
    return render_template("partials/livrables.html", c=c).strip()


def _livrables_de(ligne) -> dict:
    """Livrables d'une entreprise, avec ce qu'il faut pour les afficher."""
    c = {
        "siren": ligne["siren"],
        "url_vitrine": ligne["url_vitrine"] or "",
        "dossier_site": ligne["dossier_site"] or "",
        "zip_lien": ligne["zip_lien"] or "",
        "zip_nom": ligne["zip_nom"] or "",
        "livrables_maj_le": ligne["livrables_maj_le"] or "",
    }
    c["vitrine_hote"] = urlparse(c["url_vitrine"]).netloc if _adresse_valide(c["url_vitrine"]) \
        else c["url_vitrine"]
    c["zip_href"] = f"/suivi/livrables/zip/{c['siren']}" if c["zip_nom"] else c["zip_lien"]
    # Etiquette du .zip : le nom de l'archive deposee, sinon celui du lien.
    if c["zip_nom"]:
        c["zip_label"] = c["zip_nom"]
    elif c["zip_lien"]:
        c["zip_label"] = os.path.basename(urlparse(c["zip_lien"]).path) or "archive .zip"
    else:
        c["zip_label"] = ""
    c["a_quelque_chose"] = bool(c["url_vitrine"] or c["dossier_site"] or c["zip_href"])
    return c


def _resume_entreprise(c: dict) -> dict:
    """Ce qu'on affiche d'une entreprise : de quoi la reconnaitre sans l'ouvrir."""
    return {
        "siren": c.get("siren"),
        "nom": c.get("nom") or c.get("siren"),
        "enseigne": c.get("enseigne"),
        "commune": c.get("commune"),
        "code_postal": c.get("code_postal"),
        "naf_label": c.get("naf_label"),
        "naf_code": c.get("naf_code"),
        "effectif": c.get("effectif"),
        "categorie": c.get("categorie"),
        "date_creation": c.get("date_creation"),
        "adresse": c.get("adresse") or c.get("rue"),
        "departement": c.get("departement"),
        "actif": c.get("actif", True),
    }


@bp.route("/api/annuaire")
@login_required
def api_annuaire():
    """Cherche une entreprise dans la base officielle pour l'ajouter au repertoire.

    Aucune fiche inventee : la reponse vient de l'API officielle. Si elle est
    injoignable, on le dit, et la saisie manuelle prend le relais.
    """
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"ok": False, "error": "Indiquez au moins deux caractères."}), 400
    try:
        res = gov_api.search(q=q, page=1)
    except gov_api.ApiError as exc:
        current_app.logger.warning("Annuaire injoignable (q=%r) : %s", q, exc)
        return jsonify({"ok": False, "indisponible": True,
                        "error": "La base officielle est injoignable depuis cet "
                                 "environnement : renseignez l'entreprise à la main."}), 200
    deja = {r["siren"] for r in db.query("SELECT siren FROM tracked")}
    resultats = []
    for c in res.get("items") or []:
        resume = _resume_entreprise(c)
        resume["deja"] = resume["siren"] in deja
        resultats.append(resume)
        if len(resultats) >= 8:
            break
    return jsonify({"ok": True, "resultats": resultats, "total": res.get("total_results", 0)})


def _ajoute_au_repertoire(siren: str, snapshot: dict, source: str):
    """Cree la ligne du repertoire. Renvoie (etat_json, code_http)."""
    if db.one("SELECT id FROM tracked WHERE siren = ?", (siren,)):
        return jsonify({"ok": False, "error": "Cette entreprise est déjà dans le répertoire."}), 409
    moi = current_user()["username"]
    maintenant = db.now_iso()
    db.run_all([
        (
            "INSERT INTO tracked (siren, snapshot, status, added_by, added_at, pris_par, pris_le)"
            " VALUES (?, ?, 'a_contacter', ?, ?, ?, ?)",
            (siren, json.dumps(snapshot, ensure_ascii=False), moi, maintenant, moi, maintenant),
        ),
        (
            "UPDATE hides SET restored_at = ?, restored_by = ? WHERE siren = ? AND restored_at IS NULL",
            (maintenant, moi, siren),
        ),
    ])
    current_app.logger.info("Repertoire : %s (%s) ajoute par %s", siren, source, moi)
    return jsonify(_etat_pour_js(siren)), 200


@bp.route("/api/suivi/ajouter", methods=["POST"])
@login_required
def api_suivi_ajouter():
    """Ajoute une entreprise au repertoire : base officielle, sinon saisie manuelle."""
    d = _json()
    siren = re.sub(r"\D", "", str(d.get("siren") or ""))
    if not re.fullmatch(r"\d{9}", siren):
        return jsonify({"ok": False, "error": "SIREN invalide : 9 chiffres attendus."}), 400

    # 1) fiche officielle, 2) instantane deja connu (masquage), 3) saisie manuelle.
    entreprise, source = _entreprise_complete(siren)
    if not entreprise:
        nom = str(d.get("nom") or "").strip()
        if not nom:
            return jsonify({"ok": False,
                            "error": "Entreprise introuvable dans la base officielle : "
                                     "indiquez au moins son nom."}), 400
        entreprise = {"siren": siren, "nom": nom,
                      "enseigne": str(d.get("enseigne") or "").strip() or None,
                      "commune": str(d.get("commune") or "").strip() or None,
                      "naf_label": str(d.get("activite") or "").strip() or None,
                      "adresse": str(d.get("adresse") or "").strip() or None,
                      "source": "saisie"}
        source = "saisie"
    return _ajoute_au_repertoire(siren, entreprise, source)


@bp.route("/api/suivi/livrables", methods=["POST"])
@login_required
def api_suivi_livrables():
    """Range les livrables d'une entreprise : dossier du site, .zip, URL de vitrine."""
    d = _json()
    siren = str(d.get("siren") or "")
    if not _entreprise_suivie(siren):
        return jsonify({"ok": False, "error": "Entreprise non suivie."}), 404

    vitrine = str(d.get("url_vitrine") or "").strip()
    if vitrine and not _adresse_valide(vitrine):
        # Une vitrine s'ouvre dans un navigateur : il faut une adresse complete.
        if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", vitrine, re.IGNORECASE):
            vitrine = "https://" + vitrine          # « vitrine.fr » suffit a la saisie
        else:
            return jsonify({"ok": False,
                            "error": "URL de vitrine invalide : attendu une adresse "
                                     "https://..."}), 400
    zip_lien = str(d.get("zip_lien") or "").strip()
    if zip_lien and not _adresse_valide(zip_lien):
        return jsonify({"ok": False,
                        "error": "Lien de l'archive invalide : attendu une adresse https://..."}), 400

    db.execute(
        "UPDATE tracked SET url_vitrine = ?, dossier_site = ?, zip_lien = ?,"
        " livrables_maj_le = ? WHERE siren = ?",
        (vitrine, str(d.get("dossier_site") or "").strip()[:500], zip_lien,
         db.now_iso(), siren))
    return jsonify(_etat_pour_js(siren))


@bp.route("/suivi/livrables/zip", methods=["POST"])
@login_required
def suivi_livrables_zip():
    """Depose l'archive .zip du site (une archive par entreprise, la nouvelle remplace)."""
    siren = re.sub(r"\D", "", request.form.get("siren") or "")
    if not _entreprise_suivie(siren):
        return jsonify({"ok": False, "error": "Entreprise non suivie."}), 404
    fichier = request.files.get("fichier")
    if not fichier or not fichier.filename:
        return jsonify({"ok": False, "error": "Choisissez une archive .zip."}), 400
    nom = os.path.basename(fichier.filename)[:120]
    if not nom.lower().endswith(".zip"):
        return jsonify({"ok": False, "error": "Seules les archives .zip sont acceptées."}), 400

    donnees = fichier.read()
    if len(donnees) > ZIP_MAX_OCTETS:
        return jsonify({"ok": False, "error":
                        f"Archive trop lourde : {len(donnees) // 1048576} Mo "
                        f"(maximum {ZIP_MAX_OCTETS // 1048576} Mo)."}), 400
    if donnees[:2] != b"PK":                       # signature d'une archive zip
        return jsonify({"ok": False, "error": "Ce fichier n'est pas une archive .zip."}), 400

    chemin = os.path.join(_dossier_livrables(), f"{siren}.zip")
    with open(chemin, "wb") as sortie:
        sortie.write(donnees)
    db.execute("UPDATE tracked SET zip_nom = ?, zip_lien = '', livrables_maj_le = ?"
               " WHERE siren = ?", (nom, db.now_iso(), siren))
    current_app.logger.info("Repertoire : archive %s deposee pour %s (%s octets)",
                            nom, siren, len(donnees))
    etat = _etat_pour_js(siren)
    etat["message"] = f"Archive {nom} déposée."
    return jsonify(etat)


@bp.route("/suivi/livrables/zip/<siren>")
@login_required
def suivi_livrables_zip_telecharger(siren):
    """Renvoie l'archive deposee pour cette entreprise."""
    ligne = _entreprise_suivie(siren)
    if not ligne or not ligne["zip_nom"]:
        abort(404)
    chemin = os.path.join(_dossier_livrables(), f"{ligne['siren']}.zip")
    if not os.path.exists(chemin):
        abort(404)
    return send_file(chemin, as_attachment=True, download_name=ligne["zip_nom"])


@bp.route("/api/suivi/prendre", methods=["POST"])
@login_required
def api_suivi_prendre():
    """Prendre en charge une entreprise, ou la laisser a l'equipe."""
    d = _json()
    siren = str(d.get("siren") or "")
    ligne = _entreprise_suivie(siren)
    if not ligne:
        return jsonify({"ok": False, "error": "Entreprise non suivie."}), 404

    prendre = d.get("prendre")
    prendre = True if prendre is None else bool(prendre)
    moi = current_user()["username"]
    if prendre:
        db.execute("UPDATE tracked SET pris_par = ?, pris_le = ? WHERE siren = ?",
                   (moi, db.now_iso(), siren))
        current_app.logger.info("Suivi : %s prend %s", moi, siren)
    else:
        autre = ligne["pris_par"] and ligne["pris_par"] != moi
        if autre and not est_admin():
            return jsonify({"ok": False,
                            "error": f"Cette entreprise est déjà suivie par {ligne['pris_par']}."}), 409
        db.execute("UPDATE tracked SET pris_par = '', pris_le = NULL WHERE siren = ?", (siren,))
        current_app.logger.info("Suivi : %s laisse %s a l'equipe", moi, siren)
    return jsonify(_etat_pour_js(siren))


@bp.route("/api/suivi/appel", methods=["POST"])
@login_required
def api_suivi_appel():
    """Enregistre un appel passe : compte rendu, statut et prochain appel."""
    d = _json()
    siren = str(d.get("siren") or "")
    ligne = _entreprise_suivie(siren)
    if not ligne:
        return jsonify({"ok": False, "error": "Entreprise non suivie."}), 404

    relance = str(d.get("relance_le") or "").strip()
    if relance:
        try:
            datetime.strptime(relance, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False,
                            "error": "Date de relance invalide (attendu AAAA-MM-JJ)."}), 400
    note = str(d.get("note") or "").strip()[:2000]

    moi = current_user()["username"]
    maintenant = _maintenant()
    # Un appel passe fait avancer le statut commercial, sans jamais reculer :
    # une entreprise deja en discussion ou signee n'est pas ramenee a « Contacté ».
    statut = "contacte" if ligne["status"] == "a_contacter" else ligne["status"]
    db.execute(
        "UPDATE tracked SET appels = appels + 1, appele_le = ?, appele_par = ?,"
        " relance_le = ?, note = ?, status = ?, status_updated_by = ?, status_updated_at = ?,"
        " pris_par = CASE WHEN pris_par = '' OR pris_par IS NULL THEN ? ELSE pris_par END,"
        " pris_le = CASE WHEN pris_par = '' OR pris_par IS NULL THEN ? ELSE pris_le END"
        " WHERE siren = ?",
        (maintenant.strftime("%Y-%m-%dT%H:%M"), moi, relance, note, statut, moi,
         db.now_iso(), moi, db.now_iso(), siren),
    )
    current_app.logger.info("Suivi : appel de %s enregistre par %s (relance %r)",
                            siren, moi, relance)
    return jsonify(_etat_pour_js(siren))


@bp.route("/api/suivi/note", methods=["POST"])
@login_required
def api_suivi_note():
    """Note d'equipe sur une entreprise suivie (sans compter d'appel)."""
    d = _json()
    siren = str(d.get("siren") or "")
    if not _entreprise_suivie(siren):
        return jsonify({"ok": False, "error": "Entreprise non suivie."}), 404
    db.execute("UPDATE tracked SET note = ? WHERE siren = ?",
               (str(d.get("note") or "").strip()[:2000], siren))
    return jsonify(_etat_pour_js(siren))


@bp.route("/portefeuille")
@login_required
def portefeuille():
    rows = db.query("SELECT * FROM tracked ORDER BY added_at DESC")
    items = []
    for r in rows:
        try:
            c = json.loads(r["snapshot"] or "{}")
            if not isinstance(c, dict):
                c = {}
        except ValueError:
            continue
        c["siren"] = r["siren"]
        c["tracked"] = True
        c["statut"] = r["status"]
        c["added_by"] = r["added_by"]
        c["added_at"] = r["added_at"]
        items.append(c)
    etats = detect.etats_effectifs_lot(items)
    for c in items:
        etat = etats.get(c["siren"])
        c["site"] = etat or {"status": "inconnu", "domain": None, "source": "dns",
                             "checked_at": None}
    compteurs = {code: 0 for code, _ in STATUTS_PIPELINE}
    for c in items:
        compteurs[c["statut"]] = compteurs.get(c["statut"], 0) + 1
    # Le portefeuille ne fait qu'afficher : la saisie du benefice est dans le
    # panel staff (section Benefice).
    return render_template(
        "portfolio.html",
        page_id="portefeuille",
        titre="Portefeuille",
        sous_titre="Suivez vos affaires, vos encaissements et votre activité commerciale.",
        items=items,
        compteurs=compteurs,
        benefice=benefice_du_portefeuille(),
        benefice_gere_au_staff=est_admin(),
    )


def _montant_en_centimes(brut: str):
    """'4 500,50' ou '4 500 €' -> 450050. Renvoie None si le texte n'est pas un montant valide."""
    texte = re.sub(r"(?:euros?|eur|[€\s\u202f\u00a0])", "", (brut or ""), flags=re.IGNORECASE)
    texte = texte.replace(",", ".")
    if not texte:
        return None                    # un encaissement doit avoir un montant
    try:
        centimes = int(round(float(texte) * 100))
    except ValueError:
        return None
    if centimes <= 0 or centimes > 100_000_000_00:     # 0 a 1 milliard d'euros
        return None
    return centimes


def _siret_normalise(brut: str):
    """Code SIRET facultatif : renvoie (siret, siren, erreur).

    Accepte un SIRET (14 chiffres) ou un SIREN (9 chiffres), avec ou sans espaces/séparateurs.
    Vide : aucun rattachement, l'encaissement est simplement sans entreprise.
    """
    texte = re.sub(r"[\s.\u202f\u00a0\-/]", "", brut or "")
    if not texte:
        return "", None, None
    if not texte.isdigit():
        return None, None, "siret"
    if len(texte) == 14:
        return texte, texte[:9], None
    if len(texte) == 9:
        return texte, texte, None
    return None, None, "siret"


def _quand_encaissement(date_brute: str, heure_brute: str = ""):
    """Assemble la date et l'heure saisies en 'AAAA-MM-JJTHH:MM' (heure de Paris).

    Accepte aussi un champ `datetime-local` complet dans `date_brute`, et une date
    seule : l'heure vaut alors 12:00, comme annonce sur le formulaire. Renvoie
    (valeur, erreur).
    """
    date_txt = (date_brute or "").strip()
    heure_txt = (heure_brute or "").strip()
    if not date_txt and not heure_txt:
        return None, "date"                  # la date est necessaire au graphique
    if "T" in date_txt:                       # <input type="datetime-local">
        date_txt, _, heure_dans_date = date_txt.partition("T")
        heure_txt = heure_txt or heure_dans_date
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_txt):
        return None, "date"
    try:
        datetime.strptime(date_txt, "%Y-%m-%d")
    except ValueError:
        return None, "date"
    heure_txt = (heure_txt or "12:00").strip()[:5]
    if not re.fullmatch(r"\d{2}:\d{2}", heure_txt):
        return None, "date"
    try:
        datetime.strptime(heure_txt, "%H:%M")
    except ValueError:
        return None, "date"
    return f"{date_txt}T{heure_txt}", None


def _nom_pour_siren(siren) -> str:
    """Nom de l'entreprise suivie portant ce SIREN, sinon chaine vide."""
    if not siren:
        return ""
    ligne = db.one("SELECT snapshot FROM tracked WHERE siren = ?", (siren,))
    if not ligne:
        return ""
    try:
        snap = json.loads(ligne["snapshot"] or "{}")
        if isinstance(snap, dict):
            return snap.get("nom") or ""
        return ""
    except (ValueError, TypeError):
        return ""


# --------------------------------------------------------------------------
# Panel staff
# --------------------------------------------------------------------------

IDENTIFIANT_MOTIF = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MDP_COMPTE_LONGUEUR = 10


def _etat_staff(valeur) -> str:
    return valeur if valeur in ("actives", "restaurees", "toutes") else "actives"


def _rendu_staff(etat: str, q: str, compte_erreur=None, compte_succes=False, valeurs=None):
    """Construit la page du panel staff (partagee entre l'affichage et les erreurs)."""
    clauses, params = [], []
    if etat == "actives":
        clauses.append("restored_at IS NULL")
    elif etat == "restaurees":
        clauses.append("restored_at IS NOT NULL")
    if q:
        clauses.append("(nom LIKE ? OR siren LIKE ? OR details LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    # "where" ne contient que des clauses fixes ; les valeurs passent par params.
    rows = db.query(
        f"SELECT * FROM hides{where} ORDER BY hidden_at DESC LIMIT 300",  # noqa: S608
        params)

    total_actives = db.one("SELECT COUNT(*) n FROM hides WHERE restored_at IS NULL")["n"]
    total_restaurees = db.one("SELECT COUNT(*) n FROM hides WHERE restored_at IS NOT NULL")["n"]
    seuil = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    semaine = db.one(
        "SELECT COUNT(*) n FROM hides WHERE hidden_at >= ?", (seuil,))["n"]
    top_raison = db.one(
        "SELECT raison, COUNT(*) n FROM hides WHERE restored_at IS NULL"
        " GROUP BY raison ORDER BY n DESC LIMIT 1")

    entrees = []
    for r in rows:
        e = dict(r)
        try:
            snap = json.loads(r["snapshot"] or "{}")
        except ValueError:
            snap = {}
        e["commune"] = snap.get("commune")
        e["code_postal"] = snap.get("code_postal")
        entrees.append(e)

    # Gestion des comptes : reservee au dirigeant, et jamais son propre compte.
    comptes = None
    if est_admin():
        comptes = [dict(r) for r in db.query(
            "SELECT id, username, display_name, role FROM users"
            " WHERE id <> ? ORDER BY id", (session["uid"],))]

    # Portefeuille : ajout / retrait d'entreprises suivies (dirigeant uniquement).
    portefeuille = None
    if est_admin():
        portefeuille = []
        for r in db.query("SELECT siren, snapshot, status, added_by, added_at"
                          " FROM tracked ORDER BY added_at DESC LIMIT 200"):
            try:
                snap = json.loads(r["snapshot"] or "{}")
            except ValueError:
                snap = {}
            portefeuille.append({
                "siren": r["siren"],
                "nom": snap.get("nom") or r["siren"],
                "commune": snap.get("commune"),
                "statut": r["status"],
                "ajoute_par": r["added_by"],
                "ajoute_le": r["added_at"],
            })

    demo = (request.values.get("demo") == "1"
            and bool(current_app.config["DEMO_ALLOWED"]))
    return render_template(
        "staff.html",
        page_id="staff",
        demo=demo,
        titre="Staff",
        sous_titre="Gérez les comptes, les accès et la sécurité de votre équipe.",
        entrees=entrees,
        etat=etat,
        q=q,
        stats={
            "actives": total_actives,
            "restaurees": total_restaurees,
            "semaine": semaine,
            "top_raison": top_raison["raison"] if top_raison else None,
            "top_raison_n": top_raison["n"] if top_raison else 0,
        },
        comptes=comptes,
        compte_erreur=compte_erreur,
        compte_succes=compte_succes,
        valeurs=valeurs or {},
        portefeuille=portefeuille,
        pf_erreur=request.args.get("pf_erreur"),
        pf_ok=request.args.get("pf"),
        benefice=benefice_du_portefeuille() if est_admin() else None,
    )


@bp.route("/staff")
@login_required
def staff():
    return _rendu_staff(
        _etat_staff(request.args.get("etat")), (request.args.get("q") or "").strip(),
        compte_succes=request.args.get("compte") == "ok",
    )


def _enregistre_encaissement(siret_ou_siren: str, montant_brut: str, date_brute: str,
                             heure_brute: str = "", redirection=None):
    """Enregistre un encaissement dans la table `benefices` (panel staff).

    Le code SIRET est facultatif : sans lui, l'encaissement est enregistre sans
    rattachement a une entreprise suivie.
    """
    siret, siren, erreur = _siret_normalise(siret_ou_siren)
    if erreur:
        return redirect(url_avec_jeton(url_for("views.staff", pf_erreur=erreur)))
    centimes = _montant_en_centimes(montant_brut)
    if centimes is None:
        return redirect(url_avec_jeton(url_for("views.staff", pf_erreur="montant")))
    quand, erreur = _quand_encaissement(date_brute, heure_brute)
    if erreur:
        return redirect(url_avec_jeton(url_for("views.staff", pf_erreur=erreur)))

    db.execute(
        "INSERT INTO benefices (siret, siren, libelle, montant_cents, encaisse_le,"
        " encaisse_par, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (siret, siren, _nom_pour_siren(siren), centimes, quand,
         session.get("username") or "?", db.now_iso()))
    current_app.logger.info(
        "Encaissement de %s centimes enregistre par %s (siret=%r, le %s)",
        centimes, session.get("username"), siret or "-", quand)
    return redirect(url_avec_jeton(
        redirection or url_for("views.staff", pf="montant")))


@bp.route("/staff/portefeuille", methods=["POST"])
@admin_required
def staff_portefeuille():
    """Ajoute, retire ou valorise une entreprise du portefeuille (panel staff)."""
    action = request.form.get("action") or ""
    siren = (request.form.get("siren") or "").strip()
    etat = _etat_staff(request.form.get("etat"))
    q = (request.form.get("q") or "").strip()
    retour = lambda code: url_avec_jeton(  # noqa: E731 - petite fabrique locale
        url_for("views.staff", etat=etat, q=q or None, **code))

    if not re.fullmatch(r"\d{9}", siren):
        return redirect(retour({"pf_erreur": "siren"}))

    if action == "retirer":
        ligne = db.one("SELECT id FROM tracked WHERE siren = ?", (siren,))
        if not ligne:
            return redirect(retour({"pf_erreur": "absent"}))
        db.execute("DELETE FROM tracked WHERE id = ?", (ligne["id"],))
        current_app.logger.info("Portefeuille : %s retire par %s",
                                siren, session.get("username"))
        return redirect(retour({"pf": "retire"}))

    if action != "ajouter":
        return redirect(retour({"pf_erreur": "action"}))

    if db.one("SELECT id FROM tracked WHERE siren = ?", (siren,)):
        return redirect(retour({"pf_erreur": "deja"}))

    # Jamais de fiche inventee : API officielle, sinon instantane deja connu
    # (masquage, portefeuille). Le jeu de demonstration ne sert que s'il a ete
    # demande explicitement sur le panel (le formulaire transmet l'information).
    demo = (request.values.get("demo") == "1"
            and bool(current_app.config["DEMO_ALLOWED"]))
    entreprise, source = _entreprise_complete(siren, demo_autorise=demo)
    if not entreprise:
        return redirect(retour({"pf_erreur": "introuvable"}))
    if source == "demo":
        current_app.logger.info("Portefeuille : %s ajoute depuis le jeu de demonstration", siren)
    db.execute(
        "INSERT INTO tracked (siren, snapshot, added_by, added_at) VALUES (?, ?, ?, ?)",
        (siren, json.dumps(entreprise, ensure_ascii=False),
         session.get("username") or "?", db.now_iso()),
    )
    current_app.logger.info("Portefeuille : %s (%s) ajoute par %s",
                            siren, source, session.get("username"))
    return redirect(retour({"pf": "ajoute", "demo": "1" if source == "demo" else None}))


@bp.route("/staff/benefice", methods=["POST"])
@admin_required
def staff_benefice():
    """Enregistre un encaissement : code SIRET facultatif, montant, date et heure."""
    etat = _etat_staff(request.form.get("etat"))
    q = (request.form.get("q") or "").strip()
    return _enregistre_encaissement(
        request.form.get("siret") or "",
        request.form.get("montant") or "",
        request.form.get("signe_le") or "",
        request.form.get("heure") or "",
        redirection=url_for("views.staff", etat=etat, q=q or None, pf="montant"))


@bp.route("/staff/benefice/supprimer", methods=["POST"])
@admin_required
def staff_benefice_supprimer():
    """Supprime un encaissement enregistre par erreur."""
    etat = _etat_staff(request.form.get("etat"))
    q = (request.form.get("q") or "").strip()
    try:
        identifiant = int(request.form.get("id") or 0)
    except ValueError:
        identifiant = 0
    ligne = db.one("SELECT montant_cents, libelle, siret FROM benefices WHERE id = ?",
                   (identifiant,))
    if not ligne:
        return redirect(url_avec_jeton(url_for("views.staff", etat=etat, q=q or None,
                                               pf_erreur="absent")))
    db.execute("DELETE FROM benefices WHERE id = ?", (identifiant,))
    current_app.logger.info(
        "Encaissement supprime par %s : %s centimes (%s)",
        session.get("username"), ligne["montant_cents"], ligne["libelle"] or ligne["siret"])
    return redirect(url_avec_jeton(
        url_for("views.staff", etat=etat, q=q or None, pf="supprime")))


@bp.route("/staff/comptes", methods=["POST"])
@admin_required
def staff_comptes():
    """Le dirigeant change l'identifiant, le nom affiche ou le mot de passe
    du compte de l'associe (jamais le sien : voir « Mon mot de passe »)."""
    etat = _etat_staff(request.form.get("etat"))
    q = (request.form.get("q") or "").strip()
    try:
        cible = int(request.form.get("cible") or 0)
    except ValueError:
        cible = 0

    valeurs = {
        "cible": cible,
        "username": (request.form.get("username") or "").strip().lower(),
        "display_name": (request.form.get("display_name") or "").strip(),
    }
    nouveau = request.form.get("nouveau") or ""
    confirmation = request.form.get("confirmation") or ""

    compte = db.one("SELECT * FROM users WHERE id = ?", (cible,))
    erreur = None
    if compte is None:
        erreur = "Compte introuvable."
    elif compte["id"] == session.get("uid"):
        erreur = "Utilisez « Mon mot de passe » pour votre propre compte."
    elif not IDENTIFIANT_MOTIF.match(valeurs["username"]):
        erreur = ("L'identifiant doit faire de 3 à 32 caractères : lettres minuscules, "
                  "chiffres, point, tiret ou souligné.")
    elif db.one("SELECT 1 AS n FROM users WHERE username = ? AND id <> ?",
                (valeurs["username"], cible)):
        erreur = f"L'identifiant « {valeurs['username']} » est déjà utilisé."
    elif len(valeurs["display_name"]) > 40:
        erreur = "Le nom affiché ne doit pas dépasser 40 caractères."
    elif nouveau and len(nouveau) < MDP_COMPTE_LONGUEUR:
        erreur = (f"Le mot de passe doit contenir au moins {MDP_COMPTE_LONGUEUR} "
                  "caractères.")
    elif nouveau and nouveau != confirmation:
        erreur = "La confirmation ne correspond pas au mot de passe."
    elif nouveau and nouveau.strip().lower() == valeurs["username"]:
        erreur = "Le mot de passe ne doit pas être l'identifiant."

    if erreur:
        return _rendu_staff(etat, q, compte_erreur=erreur, valeurs=valeurs), 400

    ancien_identifiant = compte["username"]
    db.execute("UPDATE users SET username = ?, display_name = ? WHERE id = ?",
               (valeurs["username"], valeurs["display_name"] or valeurs["username"], cible))
    if nouveau:
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (db.hash_password(nouveau), cible))
    if valeurs["username"] != ancien_identifiant:
        for sql in (
            "UPDATE tracked SET pris_par = ? WHERE pris_par = ?",
            "UPDATE tracked SET added_by = ? WHERE added_by = ?",
            "UPDATE tracked SET appele_par = ? WHERE appele_par = ?",
            "UPDATE tracked SET status_updated_by = ? WHERE status_updated_by = ?",
            "UPDATE benefices SET encaisse_par = ? WHERE encaisse_par = ?",
            "UPDATE hides SET hidden_by = ? WHERE hidden_by = ?",
            "UPDATE hides SET restored_by = ? WHERE restored_by = ?",
            "UPDATE overrides SET by = ? WHERE by = ?",
        ):
            db.execute(sql, (valeurs["username"], ancien_identifiant))
    current_app.logger.info(
        "Compte '%s' (ex. '%s') mis a jour par '%s' : identifiant%s",
        valeurs["username"], ancien_identifiant, session.get("username"),
        " et mot de passe" if nouveau else " seul")
    return redirect(url_avec_jeton(
        url_for("views.staff", etat=etat, q=q or None, compte="ok")))


# --------------------------------------------------------------------------
# Mot de passe
# --------------------------------------------------------------------------

@bp.route("/mot-de-passe", methods=["GET", "POST"])
@login_required
def mot_de_passe():
    erreur, succes = None, False
    if request.method == "POST":
        actuel = request.form.get("actuel") or ""
        nouveau = request.form.get("nouveau") or ""
        confirmation = request.form.get("confirmation") or ""
        user = db.one("SELECT * FROM users WHERE id = ?", (session["uid"],))
        if not db.verify_password(actuel, user["password_hash"]):
            erreur = "Mot de passe actuel incorrect."
        elif len(nouveau) < 8:
            erreur = "Le nouveau mot de passe doit contenir au moins 8 caractères."
        elif nouveau in db.DEFAULT_PASSWORDS:
            erreur = "Veuillez choisir un mot de passe différent des mots de passe par défaut."
        elif nouveau != confirmation:
            erreur = "La confirmation ne correspond pas au nouveau mot de passe."
        else:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (db.hash_password(nouveau), session["uid"]))
            succes = True
    return render_template(
        "password.html",
        page_id="motdepasse",
        titre="Mon mot de passe",
        sous_titre="Sécurisez votre accès à l'espace associés",
        erreur=erreur,
        succes=succes,
    )
