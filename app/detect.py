"""Detection heuristique de la presence d'un site web.

Principe : on construit des noms de domaine probables a partir de la raison sociale
et de l'enseigne (maison-larue.fr, maisonlarue.com...), puis on tente une resolution
DNS. Un domaine qui resout est un indice fort d'un site existant ; l'absence totale
de resolution est un indice fort que l'entreprise n'a pas de site.

La methode est volontairement prudente : les resultats restent des indices, et
chaque associe peut corriger manuellement depuis la fiche entreprise.
"""
import re
import socket
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone

from . import db
from .gov_api import slug_ascii

CACHE_TTL_JOURS = 30
LOOKUP_TIMEOUT_TOTAL = 3.4   # budget global par entreprise (secondes)
TLD_CHOICES = ["fr", "com", "net"]

MOTS_FORME_JURIDIQUE = {
    "SARL", "SAS", "SASU", "EURL", "SCI", "SNC", "SA", "SCP", "SCM", "SELARL",
    "SELAS", "SEL", "EI", "EIRL", "ETS", "STE", "ST", "SOCIETE", "CIE", "GIE",
    "ASSOCIATION", "COOP", "SCOP", "MICRO", "ENTREPRISE", "INDIVIDUELLE",
    "FRANCE", "PME",
}
MOTS_LIAISON = {"DE", "DU", "DES", "LA", "LE", "LES", "L", "D", "A", "AU", "AUX",
                "ET", "EN", "SUR", "SOUS", "PAR", "POUR"}

_pool = ThreadPoolExecutor(max_workers=32)


def _tokens(nom: str):
    texte = re.sub(r"[^A-Za-z0-9\s]", " ", slug_ascii(nom))
    return [t for t in texte.split() if t]


def _variantes(nom: str, enseigne=None):
    """Genere une liste deduplique de candidats de domaine (sans TLD)."""
    variantes = []

    def add(tokens):
        keep = [t for t in tokens if t not in MOTS_FORME_JURIDIQUE]
        if not keep:
            keep = tokens
        noms = []
        join = "".join(keep).lower()
        tiret = "-".join(keep).lower()
        noms += [join, tiret]
        if len(keep) >= 3:
            noms.append("".join([keep[0], keep[-1]]).lower())
            noms.append("-".join([keep[0], keep[-1]]).lower())
        if len(keep) >= 2:
            noms.append("".join(keep[:2]).lower())
        for n in noms:
            if 3 <= len(n) <= 30 and n not in variantes:
                variantes.append(n)

    tokens_nom = _tokens(nom)
    if tokens_nom:
        add(tokens_nom)
    if enseigne:
        tokens_ens = _tokens(enseigne)
        base = tokens_nom[0] if tokens_nom else ""
        if tokens_ens and "".join(tokens_ens).lower() != "".join(tokens_nom).lower():
            add([t for t in tokens_ens if t not in MOTS_LIAISON] or tokens_ens)
            if base:
                add([base] + [t for t in tokens_ens if t not in MOTS_LIAISON][:2])
    return variantes[:5]


def _resout(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return True
    except (socket.gaierror, OSError):
        return False


def _verifie_domaines(nom: str, enseigne=None):
    """Resout jusqu'a ~12 domaines candidats dans un budget de temps fixe."""
    candidats = []
    for base in _variantes(nom, enseigne):
        for tld in TLD_CHOICES:
            candidats.append(f"{base}.{tld}")
    candidats = candidats[:12]

    futures = {c: _pool.submit(_resout, c) for c in candidats}
    done, _ = wait(futures.values(), timeout=LOOKUP_TIMEOUT_TOTAL)
    trouves = [c for c, f in futures.items() if f in done and not f.cancelled() and f.done()
               and not f.exception() and f.result()]
    if trouves:
        trouves.sort(key=lambda c: (not c.endswith(".fr"), c))  # priorise le .fr
        return trouves[0]
    if len(done) < len(futures):
        return "TIMEOUT"
    return None


# --------------------------------------------------------------------------
# Cache et override
# --------------------------------------------------------------------------

def etat_effectif(company: dict) -> dict:
    """Etat d'affichage : applique l'override manuel, sinon le cache, sinon None."""
    siren = company["siren"]
    row = db.one("SELECT value FROM overrides WHERE siren = ?", (siren,))
    if row:
        return {"status": "aucun", "domain": None, "source": "manuel_sans",
                "checked_at": None}
    row = db.one("SELECT * FROM site_cache WHERE siren = ?", (siren,))
    if row and _frais(row["checked_at"]):
        return {"status": row["status"], "domain": row["domain"],
                "source": row["source"], "checked_at": row["checked_at"]}
    return None


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


def verifie_lot(companies: list, budget_total: float = 8.0) -> None:
    """Verifie en parallele un lot d'entreprises (page de resultats)."""
    restantes = []
    for c in companies:
        if not etat_effectif(c):
            restantes.append(c)
    if not restantes:
        return

    futures = {c["siren"]: _pool.submit(_verifie_domaines, c.get("nom") or "",
                                        c.get("enseigne"))
               for c in restantes}
    limite = {c["siren"] for c in restantes}
    t0 = datetime.now(timezone.utc)
    for siren, future in futures.items():
        reste = budget_total - (datetime.now(timezone.utc) - t0).total_seconds()
        try:
            res = future.result(timeout=max(0.05, reste))
        except TimeoutError:
            future.cancel()
            continue
        except Exception:
            continue
        if res == "TIMEOUT":
            continue
        if res:
            _sauve_cache(siren, "site", res, "dns")
        else:
            _sauve_cache(siren, "aucun", None, "dns")
    _ = limite
