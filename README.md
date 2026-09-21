# MBDV - Prospection

Outil interne pour identifier les entreprises francaises **sans site web** :
la vente de sites aux entreprises qui n'en ont pas encore.

Deux associes, un acces securise chacun, un portefeuille partage, et un panel
staff qui garde la trace de chaque entreprise masquee (raison, auteur, date).

## Lancement

```bash
pip install -r requirements.txt
python run.py
```

Verification du code (lint + tests) :

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

L'application ecoute sur `http://0.0.0.0:5050` (variables `MBDV_HOST` / `MBDV_PORT`).

## Comptes par defaut

| Identifiant | Mot de passe       | Role            |
|-------------|--------------------|-----------------|
| `admin`     | `MBDV-admin-2026`  | Dirigeant       |
| `associe`   | `MBDV-associe-2026`| Associe         |

**Changez ces mots de passe des la premiere connexion** (menu "Mot de passe"
dans la barre laterale). Ils sont stockes haches (scrypt), jamais en clair.
Au premier demarrage, les identifiants et mots de passe peuvent etre definis
par les variables d'environnement `MBDV_ADMIN_USER`, `MBDV_ADMIN_PASSWORD`,
`MBDV_ASSOCIE_USER`, `MBDV_ASSOCIE_PASSWORD`.

## Fonctionnement

### Recherche

- Source : API publique **Recherche d'entreprises** du gouvernement
  (`recherche-entreprises.api.gouv.fr`, donnees INSEE / RNE, licence ouverte,
  sans cle d'API). Aucune donnee n'est inventee.
- Filtres : denomination / activite / SIREN, departement, code postal,
  grand secteur, code NAF precis, taille d'effectif, entreprises fermees ou non.
- **Detection automatique de site web** : pour chaque resultat, l'outil
  construit les domaines probables (raison sociale **et enseigne** — champs
  `nom_commercial` / `liste_enseignes` de la base SIRENE, en `.fr`, `.com`,
  `.net`) et tente une resolution DNS. Un domaine qui repond est un
  indice fort d'un site existant ; l'absence totale est un indice fort
  d'absence de site. La methode est heuristique :
  - un site peut exister sous un domaine "imprevisible" (marque differente,
    sous-domaine d'un tiers...) ;
  - un domaine peut resoudre sans heberger de site.
  Chaque fiche propose donc de **revérifier** et de **confirmer manuellement**
  l'absence de site. Les resultats sont conserves en cache local 30 jours.
- Le filtre "sans site detecte" analyse jusqu'a 6 pages de resultats et ne
  garde que les entreprises sans domaine verifiable.
- Export CSV de la page de resultats (format Excel francais, separateur `;`).
  La derniere colonne `Source des donnees` indique l'origine (base officielle ou
  jeu de demonstration) et le nom du fichier porte le suffixe `-DEMO` lorsque
  l'API officielle n'a pas pu etre jointe.

### Masquage et panel staff

Depuis n'importe quel resultat, le bouton **Masquer** demande obligatoirement
une raison (doublon, possede deja un site, hors cible, deja contacte...) et
accepte des precisions libres. L'entreprise disparait des resultats.

Le **panel staff** liste tout l'historique : entreprise, raison, precisions,
auteur, date de masquage, etat actuel, auteur et date d'eventuelle remise
d'affichage, avec un bouton **Reafficher**. La recherche par defaut exclut les
entreprises masquees ; l'option "inclure les entreprises masquees" les remontre.

### Portefeuille

Ajoutez des entreprises au portefeuille (icône marque-page) pour suivre le
cycle commercial : a contacter, contacte, en discussion, devis envoye, client,
sans suite.

## Donnees locales

Tout est stocke dans `data/` (base SQLite + cle de sessions), hors Git :

- `data/mbdv.sqlite3` : comptes, cache de detection, masquages, portefeuille ;
- `data/secret.key` : cle de signature des sessions (regenerable).

Supprimez le dossier pour repartir de zero (les comptes sont recrees).

## Deploiement

L'application ecoute sur la variable `PORT` des plateformes d'hebergement
(fallback `MBDV_PORT`, sinon 5050) et sur `0.0.0.0`.

### Render

- Build Command : *(vide)*
- Start Command : `pip install -r requirements.txt && python3 run.py`
- Ajouter un Disk (Settings > Disks), monte sur `/data`, puis la variable
  d'environnement `MBDV_DATA_DIR=/data` pour que la base survive aux deploiements.

### Railway / Heroku-like

Le `Procfile` fourni (`web: python3 run.py`) est detecte automatiquement ;
sinon Start Command : `pip install -r requirements.txt && python3 run.py`.

### alwaysdata (hebergeur francais)

Creer un site de type « Application Python », commande de demarrage :
`python3 run.py`, repertoire `/www`. Definir `MBDV_DATA_DIR` sur un chemin
persistant (le home du compte, ex. `/home/<compte>/data`). alwaysdata utilise
le port qu'il fournit via l'interface, fixer `MBDV_PORT` en consequence.

### Variables d'environnement utiles en production

`MBDV_ADMIN_PASSWORD`, `MBDV_ASSOCIE_PASSWORD` (mots de passe initiaux),
eventuellement `MBDV_ADMIN_USER` / `MBDV_ASSOCIE_USER`,
`MBDV_DATA_DIR` (dossier de la base, par defaut `./data`),
`MBDV_COOKIE_SECURE=1` (cookie de session uniquement en HTTPS : a activer des
que le site est servi en HTTPS), `MBDV_TRUST_PROXY=1` (faire confiance a
`X-Forwarded-For` / `X-Forwarded-Proto`, uniquement derriere un reverse proxy
de confiance).

Note : Netlify et Vercel ne conviennent pas tels quels (sites statiques /
serverless JS sans processus Python ni disque persistant).

## Note sur l'environnement d'apercu

L'environnement d'execution fourni avec ce projet filtre le reseau sortant et
ne peut joindre ni l'API officielle ni l'exterieur. Dans ce cas, l'outil bascule
sur un **jeu de donnees de demonstration** (entreprises fictives) et l'indique
clairement dans un bandeau. Sur une machine ou un serveur avec un reseau normal,
la recherche interroge la base INSEE en direct, sans aucune configuration.

## Structure

```
run.py                  point d'entree (waitress)
app/
  __init__.py           fabrique de l'application
  db.py                 SQLite : schema, comptes, hachage scrypt
  auth.py               sessions, CSRF, anti brute-force, filtres de rendu
  gov_api.py            client API officielle + normalisation des donnees
  detect.py             detection heuristique de site web + cache
  demo_data.py          jeu de demonstration (reseau filtre uniquement)
  icons.py              icones SVG inline
  views.py              routes recherche / fiche / portefeuille / staff / API
  templates/            Jinja2 (base, connexion, recherche, staff, portefeuille)
  static/               CSS, JS, polices auto-hebergees (Fraunces, Archivo, Plex Mono)
tests/                  tests pytest (application, securite, detection, export)
pyproject.toml          configuration ruff + pytest
.github/workflows/ci.yml  lint et tests a chaque push / pull request
```

## Qualite

- `ruff check .` : lint (pycodestyle, pyflakes, bandit, bugbear...) sans avertissement.
- `pytest` : tests hors ligne (API et DNS remplaces par des doubles), dont un test
  de non-regression sur l'attribut `data-snapshot` des tableaux.
- Les dependances sont figees dans `requirements.txt` (versions identiques en
  developpement et en production).

## Securite

- Mots de passe haches avec scrypt (sel aleatoire par compte).
- Sessions signees, cookie HttpOnly + SameSite=Lax, expiration 30 jours.
- Jeton CSRF sur toutes les ecritures (formulaires et appels JSON).
- Limitation a 12 tentatives par identifiant et 40 par adresse IP toutes les
  5 minutes (compteurs en memoire, remis a zero au redemarrage). L'adresse
  utilisee est celle vue par le serveur : `X-Forwarded-For` n'est pris en
  compte que si `MBDV_TRUST_PROXY=1` (application derriere un proxy de confiance).
- Redirection apres connexion limitee aux chemins internes (pas de redirection
  ouverte via `?next=`).
- Journal d'audit complet des masquages dans le panel staff.
- Au demarrage, un avertissement est journalise tant que les mots de passe par
  defaut sont utilises.
