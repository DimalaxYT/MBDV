"""Bot de maintien en vie : le site se sollicite lui-meme pour ne pas s'endormir.

Pourquoi c'est necessaire : les hebergements gratuits (Render, par exemple)
arretent un service qui n'a recu **aucune requete entrante** pendant 15 minutes,
puis le rallument a la demande suivante - avec 30 a 60 secondes d'attente. Un
ping regulier remet ce compteur a zero.

Comment ca marche : un fil d'execution discret demande l'adresse **publique** du
site (pas `localhost`, sinon la requete ne passe pas par le proxy et ne compte
pas comme trafic). L'adresse est deduite automatiquement quand l'hebergeur la
fournit (`RENDER_EXTERNAL_URL` sur Render, `RAILWAY_PUBLIC_DOMAIN` sur Railway),
ou donnee a la main.

Reglages :

    MBDV_KEEPALIVE_URL   adresse publique du site (ex. https://balise.onrender.com)
    MBDV_SITE_URL        meme chose, utilisee aussi par d'autres outils
    MBDV_KEEPALIVE       "1" force, "0" desactive ; sinon actif si une adresse est connue
    MBDV_KEEPALIVE_INTERVAL  periode entre deux pings (600 s par defaut, "10m" accepte)

Limite a connaitre : un service **deja endormi** ne peut pas se reveiller lui-meme
(son fil dort avec lui). Le bot empeche l'endormissement ; pour repartir d'un
service arrete (redemarrage de la plateforme, plantage), il faut une sollicitation
externe - un moniteur d'uptime sur `/sante` (voir README).
"""
import logging
import os
import re
import threading

import requests

UA = "Balise-Prospection-keepalive/1.0 (+outil interne MBDV)"
CHEMIN_SANTE = "/sante"

# Periode entre deux pings. 10 minutes : la demande initiale, et surtout un cran
# sous le seuil de 15 minutes des hebergements gratuits. Au-dela de 14 minutes,
# le ping arriverait trop tard pour empecher l'endormissement.
INTERVALLE_DEFAUT = 600
INTERVALLE_MIN = 60
INTERVALLE_MAX = 840
# Premier ping : laisse le serveur finir de demarrer avant de se solliciter.
PREMIER_PING = 30
DELAI_REPONSE = 20
# Au bout de quelques echecs d'affilee, c'est un vrai probleme (adresse fausse,
# site en panne) et non un incident passager : on le dit plus fort.
SEUIL_ALERTE = 3

LOGGER = logging.getLogger(__name__)

_VERROU = threading.Lock()
_THREAD = None
_STOP = None


def _flag(nom: str, defaut: bool = False) -> bool:
    """Variable d'environnement booleenne, meme convention que le reste de l'app."""
    valeur = (os.environ.get(nom) or "").strip().lower()
    if not valeur:
        return defaut
    return valeur in {"1", "true", "oui", "yes", "on"}


def _desactive() -> bool:
    return (os.environ.get("MBDV_KEEPALIVE") or "").strip().lower() in {
        "0", "false", "non", "no", "off"}


def _force() -> bool:
    return _flag("MBDV_KEEPALIVE")


def _normalise(adresse: str) -> str:
    """Adresse utilisable : schema ajoute si absent, barre oblique finale retiree."""
    adresse = (adresse or "").strip().rstrip("/")
    if not adresse:
        return ""
    if not re.match(r"^https?://", adresse, re.IGNORECASE):
        adresse = "https://" + adresse
    return adresse.rstrip("/")


def url_publique() -> str:
    """Adresse publique du site, ou chaine vide si elle n'est pas connue.

    Ordre : reglage explicite, adresse du site deja fournie pour d'autres usages,
    puis variables des hebergeurs : Render expose `RENDER_EXTERNAL_URL`, Railway
    `RAILWAY_PUBLIC_DOMAIN` (un simple nom d'hote, complete en https).
    """
    for nom in ("MBDV_KEEPALIVE_URL", "MBDV_SITE_URL", "RENDER_EXTERNAL_URL",
                "RAILWAY_PUBLIC_DOMAIN"):
        adresse = _normalise(os.environ.get(nom) or "")
        if adresse:
            return adresse
    return ""


def cible(url: str) -> str:
    """Adresse interrogee : le point de controle `/sante`, public et tres leger."""
    return _normalise(url) + CHEMIN_SANTE


def intervalle() -> int:
    """Periode demandee, bornee a ce qui garde un service eveille.

    `MBDV_KEEPALIVE_INTERVAL` accepte les secondes (« 600 ») comme une duree
    lisible (« 10m », « 90s »). Une valeur trop haute est ramenee au maximum :
    au-dela, l'hebergeur se serait deja endormi entre deux pings.
    """
    brut = (os.environ.get("MBDV_KEEPALIVE_INTERVAL") or "").strip().lower()
    if not brut:
        return INTERVALLE_DEFAUT
    correspondance = re.fullmatch(r"(\d+)\s*(s|sec|secondes?|m|min|minutes?|h|heures?)?", brut)
    if not correspondance:
        LOGGER.warning(
            "MBDV_KEEPALIVE_INTERVAL illisible (%r) : %d s utilisees.", brut, INTERVALLE_DEFAUT)
        return INTERVALLE_DEFAUT
    valeur = int(correspondance.group(1))
    unite = correspondance.group(2) or "s"
    if unite.startswith("m"):
        valeur *= 60
    elif unite.startswith("h"):
        valeur *= 3600
    return max(INTERVALLE_MIN, min(valeur, INTERVALLE_MAX))


_session = None


def _client() -> requests.Session:
    """Session HTTP reutilisee par le fil de maintien en vie (connexion gardee)."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": UA})
    return _session


def ping(url: str, timeout: float = DELAI_REPONSE) -> tuple:
    """Sollicite le site une fois. Renvoie (succes, detail) et ne leve jamais."""
    try:
        reponse = _client().get(url, timeout=timeout, allow_redirects=False)
    except requests.RequestException as exc:
        return False, exc.__class__.__name__
    if reponse.status_code >= 400:
        return False, f"HTTP {reponse.status_code}"
    return True, f"HTTP {reponse.status_code}"


def _boucle(app, url: str, periode: float, stop: threading.Event) -> None:
    """Ping regulier jusqu'a l'arret demande. Aucune erreur ne remonte."""
    app.logger.info("Maintien en vie : %s toutes les %ds.", url, int(periode))
    if stop.wait(PREMIER_PING):
        return
    echecs = 0
    while not stop.is_set():
        try:
            ok, detail = ping(url)
        except Exception:  # noqa: BLE001 - un fil de fond ne doit jamais mourir
            app.logger.exception("Maintien en vie : erreur inattendue")
            ok, detail = False, "erreur interne"
        if ok:
            if echecs:
                app.logger.info("Maintien en vie : %s repond de nouveau (apres %d echec(s)).",
                                url, echecs)
            echecs = 0
            app.logger.info("Maintien en vie : %s -> %s", url, detail)
        else:
            echecs += 1
            if echecs < SEUIL_ALERTE:
                app.logger.info("Maintien en vie : %s sans reponse (%s), echec %d.",
                                url, detail, echecs)
            else:
                app.logger.warning(
                    "Maintien en vie : %s injoignable depuis %d tentatives (%s). "
                    "Verifiez l'adresse (MBDV_KEEPALIVE_URL) et l'etat du service.",
                    url, echecs, detail)
        if stop.wait(periode):
            return


def demarrer(app, url: str, periode: int) -> threading.Thread:
    """Lance le fil de maintien en vie (sans doublon : un seul par processus)."""
    global _THREAD, _STOP
    with _VERROU:
        if _THREAD is not None and _THREAD.is_alive():
            return _THREAD
        _STOP = threading.Event()
        _THREAD = threading.Thread(target=_boucle, args=(app, url, periode, _STOP),
                                   name="mbdv-keepalive", daemon=True)
        _THREAD.start()
        app.extensions["mbdv_keepalive"] = {"url": url, "intervalle": periode}
        return _THREAD


def arreter(delai: float = 2.0) -> None:
    """Arrete le fil (utilise a l'extinction et par les tests)."""
    with _VERROU:
        stop, thread = _STOP, _THREAD
    if stop is not None:
        stop.set()
    if thread is not None:
        thread.join(timeout=delai)


def actif() -> bool:
    """Un fil de maintien en vie tourne-t-il dans ce processus ?"""
    return bool(_THREAD is not None and _THREAD.is_alive())


def init_app(app) -> None:
    """Active le bot quand c'est utile, sans rien forcer sur un poste de travail.

    Actif si une adresse publique est connue (Render la fournit, sinon
    `MBDV_KEEPALIVE_URL` / `MBDV_SITE_URL`), sauf `MBDV_KEEPALIVE=0`. En
    developpement local, aucune adresse n'est connue : il ne se passe rien.
    """
    if _desactive():
        app.logger.info("Maintien en vie desactive (MBDV_KEEPALIVE=0).")
        return
    url = url_publique()
    if not url:
        if _force():
            app.logger.warning(
                "Maintien en vie demande (MBDV_KEEPALIVE=1) mais aucune adresse publique "
                "connue : definir MBDV_KEEPALIVE_URL, par exemple "
                "https://mon-site.onrender.com")
        return
    demarrer(app, cible(url), intervalle())
