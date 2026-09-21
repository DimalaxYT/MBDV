"""Routes de l'application : recherche, fiches, portefeuille, panel staff, API."""
import csv
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import db, demo_data, detect, gov_api
from .auth import (
    current_user,
    login,
    login_attempts_exhausted,
    login_required,
    logout,
    register_failed_attempt,
    url_avec_jeton,
)
from .icons import icon

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
# Accueil (page d'explication, affichee juste apres la connexion)
# --------------------------------------------------------------------------

@bp.route("/accueil")
@login_required
def accueil():
    """Page d'accueil : explication de l'outil et indicateurs reels de l'equipe."""
    def compte(sql, params=()):
        return db.one(sql, params)["n"]

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
        titre="Bienvenue dans MBDV Prospection",
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

def _params_recherche():
    return {
        "q": (request.args.get("q") or "").strip(),
        "departement": (request.args.get("departement") or "").strip(),
        "code_postal": (request.args.get("code_postal") or "").strip(),
        "naf": (request.args.get("naf") or "").strip(),
        "section": (request.args.get("section") or "").strip(),
        "effectif": (request.args.get("effectif") or "").strip(),
        "sans_site": request.args.get("sans_site") == "1",
        "inclure_masquees": request.args.get("inclure_masquees") == "1",
        "inclure_fermees": request.args.get("inclure_fermees") == "1",
        # Jeu de demonstration : uniquement sur demande explicite.
        "demo": request.args.get("demo") == "1" and bool(current_app.config["DEMO_ALLOWED"]),
        "page": max(1, request.args.get("page", 1, type=int) or 1),
    }


def _a_des_criteres(p) -> bool:
    return bool(p["q"] or p["departement"] or p["code_postal"] or p["naf"]
                or p["section"] or p["effectif"])


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
            ), True

    if not p["sans_site"]:
        premier, demo = recherche_source(p["page"])
        items = [c for c in premier["items"] if p["inclure_masquees"] or c["siren"] not in masquees]
        detect.verifie_lot(items)
        return {
            "items": items, "total_results": premier["total_results"],
            "page": premier["page"], "total_pages": premier["total_pages"],
            "demo": demo, "analysees": len(premier["items"]),
            "suite_possible": False,
        }

    # Filtre "sans site" actif : on analyse toujours depuis la premiere page de
    # l'API, sinon le jeu de resultats change d'une page a l'autre.
    collectees: list = []
    api_page = 1
    analysees = 0
    epuise = False
    batch, demo = recherche_source(1)
    while True:
        detect.verifie_lot(batch["items"])
        analysees += len(batch["items"])
        for c in batch["items"]:
            if not p["inclure_masquees"] and c["siren"] in masquees:
                continue
            etat = detect.etat_effectif(c)
            if etat and etat["status"] == "aucun":
                collectees.append(c)
        epuise = api_page >= batch["total_pages"]
        if len(collectees) >= p["page"] * PER_PAGE or epuise:
            break
        if api_page >= max_pages:
            break
        api_page += 1
        batch, demo = recherche_source(api_page)

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
    masquees = {r["siren"] for r in db.query(
        f"SELECT siren FROM hides WHERE restored_at IS NULL AND siren IN ({trous})",  # noqa: S608
        sirens)}
    for c in items:
        etat = detect.etat_effectif(c)
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
        "titre": "Recherche d'entreprises",
        "sous_titre": "Base officielle INSEE / RNE — détection automatique des sites web",
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
    p["sans_site"] = request.args.get("sans_site") == "1"
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
        dirigeants = " / ".join(d["nom"] for d in c["dirigeants"] if d["nom"])
        writer.writerow([
            c["siren"], c.get("siret_siege") or "", c["nom"], c.get("forme") or "",
            c.get("naf_code") or "", c.get("naf_label") or "", c.get("adresse") or "",
            c.get("code_postal") or "", c.get("commune") or "", c.get("departement") or "",
            c.get("region") or "", c.get("effectif") or "",
            (c.get("date_creation") or "")[:10],
            dirigeants,
            "non" if c["site"]["status"] == "aucun" else "oui",
            c["site"].get("domain") or "",
            "oui" if c.get("tracked") else "non",
            c.get("statut") or "",
            source,
        ])
    donnees = "\ufeff" + tampon.getvalue()
    marqueur = "-DEMO" if res["demo"] else ""
    nom_fichier = f"prospection-mbdv{marqueur}-{datetime.now(timezone.utc):%Y%m%d}.csv"
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


def _badge_site(c):
    return render_template("partials/badge_site.html", c=c).strip()


@bp.route("/api/masquer", methods=["POST"])
@login_required
def api_masquer():
    d = _json()
    siren = str(d.get("siren") or "")
    nom = str(d.get("nom") or "").strip()
    raison = str(d.get("raison") or "").strip()
    details = str(d.get("details") or "").strip()
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
             json.dumps(d.get("snapshot") or {}, ensure_ascii=False),
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
    snapshot = d.get("snapshot") or {}
    siren = str(snapshot.get("siren") or "")
    if not re.fullmatch(r"\d{9}", siren) or not snapshot.get("nom"):
        return jsonify({"ok": False, "error": "Entreprise invalide."}), 400
    deja = db.one("SELECT id FROM tracked WHERE siren = ?", (siren,))
    if deja:
        db.execute("DELETE FROM tracked WHERE id = ?", (deja["id"],))
        return jsonify({"ok": True, "tracked": False})
    db.execute(
        "INSERT INTO tracked (siren, snapshot, added_by, added_at) VALUES (?, ?, ?, ?)",
        (siren, json.dumps(snapshot, ensure_ascii=False),
         current_user()["username"], db.now_iso()),
    )
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
    company = {"siren": siren, "nom": d.get("nom") or "", "enseigne": d.get("enseigne")}
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
    company = {"siren": siren, "nom": d.get("nom") or "", "enseigne": d.get("enseigne")}
    etat = detect.etat_effectif(company)
    c = {"siren": siren, "site": etat or {"status": "inconnu", "domain": None,
                                          "source": "dns", "checked_at": None}}
    return jsonify({"ok": True, "site": c["site"], "badge_html": _badge_site(c)})


# --------------------------------------------------------------------------
# Portefeuille
# --------------------------------------------------------------------------

@bp.route("/portefeuille")
@login_required
def portefeuille():
    rows = db.query("SELECT * FROM tracked ORDER BY added_at DESC")
    items = []
    for r in rows:
        try:
            c = json.loads(r["snapshot"])
        except ValueError:
            continue
        etat = detect.etat_effectif(c)
        c["site"] = etat or {"status": "inconnu", "domain": None, "source": "dns",
                             "checked_at": None}
        c["tracked"] = True
        c["statut"] = r["status"]
        c["added_by"] = r["added_by"]
        c["added_at"] = r["added_at"]
        items.append(c)
    compteurs = {code: 0 for code, _ in STATUTS_PIPELINE}
    for c in items:
        compteurs[c["statut"]] = compteurs.get(c["statut"], 0) + 1
    return render_template(
        "portfolio.html",
        page_id="portefeuille",
        titre="Portefeuille commercial",
        sous_titre="Entreprises suivies par l'équipe — de la prise de contact à la signature",
        items=items,
        compteurs=compteurs,
    )


# --------------------------------------------------------------------------
# Panel staff
# --------------------------------------------------------------------------

@bp.route("/staff")
@login_required
def staff():
    etat = request.args.get("etat", "actives")
    q = (request.args.get("q") or "").strip()
    if etat not in ("actives", "restaurees", "toutes"):
        etat = "actives"

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

    return render_template(
        "staff.html",
        page_id="staff",
        titre="Panel staff",
        sous_titre="Historique des entreprises masquées — raison, auteur, date",
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
    )


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
