"""Detection heuristique de la presence d'un site web.

Principe : on construit des noms de domaine probables a partir de la raison sociale
et de l'enseigne (maison-larue.fr, maisonlarue.com...), puis on tente une resolution
DNS. Un domaine qui resout est un indice fort d'un site existant ; l'absence totale
de resolution est un indice fort que l'entreprise n'a pas de site.

La methode est volontairement prudente : les resultats restent des indices, et
chaque associe peut corriger manuellement depuis la fiche entreprise.
"""
import logging
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import db
from .gov_api import slug_ascii

LOGGER = logging.getLogger(__name__)

CACHE_TTL_JOURS = 30
LOOKUP_TIMEOUT_TOTAL = 2.5    # budget par entreprise (secondes)
TIMEOUT_RESOLUTION = 1.5      # echeance d'une resolution DNS (voir _resolveur_dns.py)
TLD_CHOICES = ["fr", "com", "net"]
_RESOLVEUR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_resolveur_dns.py")

MOTS_FORME_JURIDIQUE = {
    "SARL", "SAS", "SASU", "EURL", "SCI", "SNC", "SA", "SCP", "SCM", "SELARL",
    "SELAS", "SEL", "EI", "EIRL", "ETS", "STE", "ST", "SOCIETE", "CIE", "GIE",
    "ASSOCIATION", "COOP", "SCOP", "MICRO", "ENTREPRISE", "INDIVIDUELLE",
    "FRANCE", "PME",
}
MOTS_LIAISON = {"DE", "DU", "DES", "LA", "LE", "LES", "L", "D", "A", "AU", "AUX",
                "ET", "EN", "SUR", "SOUS", "PAR", "POUR"}

# Chaque verification occupe un processus de resolution : on garde peu de
# verifications simultanees (memoire du serveur) et un delai par resolution, ce
# qui borne la duree de la page de resultats.
_pool = ThreadPoolExecutor(max_workers=12)


def _tokens(nom: str):
    texte = re.sub(r"[^A-Za-z0-9\s]", " ", slug_ascii(nom))
    return [t for t in texte.split() if t]


def _variantes(nom: str, enseigne=None):
    """Genere une liste dedupliquee de candidats de domaine (sans TLD).

    Ordre de priorite : denomination (collee puis avec tirets), enseigne, puis
    denomination + enseigne. Les abreviations (premier/dernier mot, deux premiers
    mots) ne servent que de dernier recours, sinon elles mangent le quota de cinq
    candidats et l'enseigne ne serait jamais testee.
    """
    variantes = []

    def ajoute(tokens):
        if not tokens:
            return
        keep = [t for t in tokens if t not in MOTS_FORME_JURIDIQUE] or tokens
        for candidat in ("".join(keep).lower(), "-".join(keep).lower()):
            if 3 <= len(candidat) <= 30 and candidat not in variantes:
                variantes.append(candidat)

    tokens_nom = _tokens(nom)
    tokens_enseigne = _tokens(enseigne) if enseigne else []
    utiles_enseigne = [t for t in tokens_enseigne if t not in MOTS_LIAISON] or tokens_enseigne

    ajoute(tokens_nom)
    ajoute(utiles_enseigne)
    base = tokens_nom[0] if tokens_nom else ""
    if base and utiles_enseigne:
        ajoute([base, *utiles_enseigne[:2]])

    keep = [t for t in tokens_nom if t not in MOTS_FORME_JURIDIQUE] or tokens_nom
    if len(keep) >= 3:
        ajoute([keep[0], keep[-1]])
    if len(keep) >= 2:
        ajoute(keep[:2])
    return variantes[:5]


def _resout(host: str) -> bool:
    """Vrai si le nom resout, avec une echeance reellement applicable.

    `socket.getaddrinfo` ignore `socket.setdefaulttimeout` : dans un thread, une
    resolution qui ne repond pas l'occupe indefiniment. La resolution est donc
    faite dans un processus separe (app/_resolveur_dns.py), tue a l'echeance :
    le thread est toujours rendu, meme face a un DNS muet.
    """
    try:
        # Commande fixe (interpreteur courant + resolveur du projet) : `host` est
        # transmis en argument, jamais interprete par un shell.
        resultat = subprocess.run(  # noqa: S603
            [sys.executable, _RESOLVEUR, host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT_RESOLUTION,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return resultat.returncode == 0


def _verifie_domaines(nom: str, enseigne=None):
    """Resout les domaines candidats, .fr d'abord, dans un budget de temps fixe.

    Renvoie le domaine trouve, None si aucun ne repond, ou "TIMEOUT" si le budget
    est epuise avant d'avoir pu tester tous les candidats sans rien trouver : dans
    ce dernier cas l'appelant ne met rien en cache, la question restant ouverte.
    """
    candidats = []
    for tld in TLD_CHOICES:
        for base in _variantes(nom, enseigne):
            candidat = f"{base}.{tld}"
            if candidat not in candidats:
                candidats.append(candidat)
    candidats = candidats[:12]

    debut = time.monotonic()
    trouve = None
    for domaine in candidats:
        if _resout(domaine):
            if domaine.endswith(".fr"):
                return domaine            # la cible prioritaire en France
            if not trouve:
                trouve = domaine
        if time.monotonic() - debut >= LOOKUP_TIMEOUT_TOTAL:
            # Budget epuise : on repond avec ce qu'on a, ou « on ne sait pas ».
            return trouve or "TIMEOUT"
    return trouve



# --------------------------------------------------------------------------
# Cache et override
# --------------------------------------------------------------------------

def etats_effectifs_lot(companies: list) -> dict:
    """Etat d'affichage pour une liste d'entreprises, en 2 requetes SQL groupees."""
    if not companies:
        return {}
    sirens = list(dict.fromkeys(str(c.get("siren") or "") for c in companies if c.get("siren")))
    if not sirens:
        return {}
    trous = ",".join("?" * len(sirens))
    overrides = {r["siren"]: r["value"] for r in db.query(
        f"SELECT siren, value FROM overrides WHERE siren IN ({trous})", sirens)}  # noqa: S608
    caches = {r["siren"]: r for r in db.query(
        f"SELECT * FROM site_cache WHERE siren IN ({trous})", sirens)}  # noqa: S608

    result = {}
    for c in companies:
        siren = str(c.get("siren") or "")
        if not siren:
            continue
        if siren in overrides:
            result[siren] = {"status": "aucun", "domain": None, "source": "manuel_sans",
                             "checked_at": None}
        elif siren in caches and _frais(caches[siren]["checked_at"]):
            row = caches[siren]
            result[siren] = {"status": row["status"], "domain": row["domain"],
                             "source": row["source"], "checked_at": row["checked_at"]}
        else:
            result[siren] = None
    return result


def etat_effectif(company: dict) -> dict:
    """Etat d'affichage : applique l'override manuel, sinon le cache, sinon None."""
    siren = str(company.get("siren") or "")
    if not siren:
        return None
    return etats_effectifs_lot([company]).get(siren)


def _frais(checked_at: str) -> bool:
    try:
        dt = datetime.strptime(checked_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt < timedelta(days=CACHE_TTL_JOURS)
    except (ValueError, TypeError):
        return False


def _sauve_cache(siren: str, status: str, domain, source: str) -> None:
    db.execute(
        "INSERT INTO site_cache (siren, status, domain, source, checked_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(siren) DO UPDATE SET status=excluded.status,"
        " domain=excluded.domain, source=excluded.source, checked_at=excluded.checked_at",
        (siren, status, domain, source, db.now_iso()),
    )


def verifie(company: dict, force: bool = False) -> dict:
    """Verifie une seule entreprise (fiche detail) et met a jour le cache."""
    if not force:
        etat = etat_effectif(company)
        if etat:
            return etat
    domaine = _verifie_domaines(company.get("nom") or "", company.get("enseigne"))
    if domaine == "TIMEOUT":
        return {"status": "inconnu", "domain": None, "source": "dns", "checked_at": None}
    if domaine:
        _sauve_cache(company["siren"], "site", domaine, "dns")
        return {"status": "site", "domain": domaine, "source": "dns",
                "checked_at": db.now_iso()}
    _sauve_cache(company["siren"], "aucun", None, "dns")
    return {"status": "aucun", "domain": None, "source": "dns", "checked_at": db.now_iso()}


def verifie_lot(companies: list, budget_total: float = 3.0) -> None:
    """Verifie en parallele un lot d'entreprises (page de resultats).

    Reste volontairement borne dans le temps : cette fonction bloque la requete
    HTTP en cours (a terme, la detection meriterait une tache de fond). Les
    verifications lancées et non terminees a l'echeance continuent en arriere-plan
    puis s'arretent d'elles-memes (chaque resolution a son propre delai) ; leur
    resultat n'est simplement pas mis en cache pour cette requete.
    """
    if not companies:
        return
    etats = etats_effectifs_lot(companies)
    restantes = [c for c in companies if not etats.get(str(c.get("siren") or ""))]
    if not restantes:
        return

    futures = {c["siren"]: _pool.submit(_verifie_domaines, c.get("nom") or "",
                                        c.get("enseigne"))
               for c in restantes}
    t0 = datetime.now(timezone.utc)
    a_sauver = []
    for siren, future in futures.items():
        ecoule = (datetime.now(timezone.utc) - t0).total_seconds()
        reste = budget_total - ecoule
        if reste <= 0:
            for f in futures.values():
                if not f.done():
                    f.cancel()
            break
        try:
            res = future.result(timeout=reste)
        except TimeoutError:
            future.cancel()
            LOGGER.info("Detection DNS non terminee dans le budget pour %s", siren)
            continue
        except Exception:
            LOGGER.exception("Detection DNS en echec pour %s", siren)
            continue
        if res == "TIMEOUT":
            continue
        if res:
            a_sauver.append((siren, "site", res, "dns"))
        else:
            a_sauver.append((siren, "aucun", None, "dns"))

    if a_sauver:
        maintenant = db.now_iso()
        statements = [
            (
                "INSERT INTO site_cache (siren, status, domain, source, checked_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(siren) DO UPDATE SET status=excluded.status,"
                " domain=excluded.domain, source=excluded.source, checked_at=excluded.checked_at",
                (siren, status, dom, src, maintenant),
            )
            for siren, status, dom, src in a_sauver
        ]
        db.run_all(statements)
