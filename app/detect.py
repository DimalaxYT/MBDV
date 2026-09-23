"""Detection heuristique de la presence d'un site web.

Principe : on construit des noms de domaine probables a partir de la raison sociale
et de l'enseigne (maison-larue.fr, maisonlarue.com...), puis on tente une resolution
DNS. Un domaine qui resout est un indice fort d'un site existant ; l'absence totale
de resolution est un indice fort que l'entreprise n'a pas de site.

La methode est volontairement prudente : les resultats restent des indices, et
chaque associe peut corriger manuellement depuis la fiche entreprise.
"""
import logging
import re
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timedelta, timezone

from . import db
from .gov_api import slug_ascii

LOGGER = logging.getLogger(__name__)

CACHE_TTL_JOURS = 30
LOOKUP_TIMEOUT_TOTAL = 2.5   # budget global par entreprise (secondes)
TLD_CHOICES = ["fr", "com", "net"]

MOTS_FORME_JURIDIQUE = {
    "SARL", "SAS", "SASU", "EURL", "SCI", "SNC", "SA", "SCP", "SCM", "SELARL",
    "SELAS", "SEL", "EI", "EIRL", "ETS", "STE", "ST", "SOCIETE", "CIE", "GIE",
    "ASSOCIATION", "COOP", "SCOP", "MICRO", "ENTREPRISE", "INDIVIDUELLE",
    "FRANCE", "PME",
}
MOTS_LIAISON = {"DE", "DU", "DES", "LA", "LE", "LES", "L", "D", "A", "AU", "AUX",
                "ET", "EN", "SUR", "SOUS", "PAR", "POUR"}

_dns_pool = ThreadPoolExecutor(max_workers=64)
_pool = ThreadPoolExecutor(max_workers=32)


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
    try:
        socket.setdefaulttimeout(1.5)
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return True
    except (socket.gaierror, OSError):
        return False


def _verifie_domaines(nom: str, enseigne=None):
    """Resout jusqu'a ~12 domaines candidats dans un budget de temps fixe."""
    candidats = []
    bases = _variantes(nom, enseigne)
    # On teste en priorite les extensions .fr (la cible prioritaire en France)
    for tld in TLD_CHOICES:
        for base in bases:
            c = f"{base}.{tld}"
            if c not in candidats:
                candidats.append(c)
    candidats = candidats[:12]

    futures = {_dns_pool.submit(_resout, c): c for c in candidats}
    trouve = None
    try:
        for f in as_completed(futures.keys(), timeout=LOOKUP_TIMEOUT_TOTAL):
            try:
                res = f.result()
            except (socket.gaierror, OSError):
                continue
            if res:
                c = futures[f]
                if c.endswith(".fr"):
                    return c
                if not trouve:
                    trouve = c
                    fr_futures = [fut for fut, host in futures.items()
                                  if host.endswith(".fr") and not fut.done()]
                    if fr_futures:
                        done_fr, _ = wait(fr_futures, timeout=0.15)
                        fr_matches = [futures[df] for df in done_fr
                                      if not df.cancelled() and not df.exception() and df.result()]
                        if fr_matches:
                            return fr_matches[0]
                    return trouve
    except TimeoutError:
        return trouve if trouve else "TIMEOUT"
    finally:
        for rem in futures:
            rem.cancel()

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
    HTTP en cours (a terme, la detection meriterait une tache de fond).
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
