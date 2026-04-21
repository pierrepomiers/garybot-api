# NOTOX / GaryBot — Contexte projet

> Document de référence pour Claude (Code & Chat). Lire en priorité avant toute intervention.
> Mis à jour : 2026-04-20

---

## 1. Qui / Quoi

**NOTOX** = marque de planches de surf custom, portée par la société **GREEN WAVE SAS** (Anglet, France).
**GaryBot** = outil interne de suivi des commandes en cours (board + notifs fournisseurs + emails clients).

**Stack utilisateur** : Pierre Pomiers (fondateur, dev principal du projet).
**Contact email** : pierre@notoxsurf.com

---

## 2. Architecture

```
Navigateur ──HTTPS──▶ Frontend (GitHub Pages)
                        │
                        ├──▶ Supabase (auth + meta commandes)
                        │
                        └──▶ Backend FastAPI (Render.com)
                                 │
                                 └──▶ Odoo 19 SaaS (XML-RPC)
```

### Deux repos GitHub distincts

| Repo | Rôle | Déploiement |
|---|---|---|
| **`garybot`** | Frontend HTML/JS (monolithe `index.html`) | GitHub Pages |
| **`garybot-api`** | Backend FastAPI Python (monolithe `main.py`) | Render.com (free tier + UptimeRobot) |

### Services externes

| Service | Rôle | Plan |
|---|---|---|
| **Odoo SaaS** | ERP source de vérité (commandes, clients, produits, chatter) | Payant — `notoxsurf.odoo.com` |
| **Supabase** | Auth users + métadonnées GaryBot (coches étapes, archivage, config fournisseurs) | Free tier |
| **Render.com** | Hosting backend FastAPI | Free tier (dort après 15 min) |
| **GitHub Pages** | Hosting frontend statique | Gratuit |
| **UptimeRobot** | Keep-alive Render (ping `/health` toutes les 5 min) | Gratuit |

---

## 3. Contraintes fortes (à respecter absolument)

### 3.1 Odoo SaaS — accès très restreint
- ✅ **XML-RPC** fonctionne (seule voie d'accès programmatique)
- ❌ **API REST** → 404 (non disponible sur SaaS)
- ❌ **Accès session web** → bloqué par Odoo SaaS
- ❌ **`mail.message.create` direct** → AccessError
- ❌ **`message_post` avec body HTML** → HTML échappé en clair
- ✅ **`mail.compose.message` en mode `comment`** → fonctionne (c'est la voie retenue)

### 3.2 Pas de nouvel utilisateur Odoo
Chaque user interne Odoo est **facturé** sur SaaS. L'user XML-RPC est et doit rester **pierre@notoxsurf.com** (admin).

### 3.3 Pas de SMTP custom (pour l'instant)
Pas de serveur SMTP sortant custom, pas de config DNS. On utilise le SMTP natif d'Odoo SaaS (catchall = `@notoxsurf.odoo.com` par défaut, ou `@notoxsurf.com` si configuré).

### 3.4 Identité de marque
Côté client, les emails doivent afficher **"NOTOX"** comme nom d'expéditeur, jamais "Pierre Pomiers".

---

## 4. Structure des fichiers

### Backend (`garybot-api/`)

```
garybot-api/
├── main.py              ← monolithe 610 lignes, tout le backend
├── requirements.txt     ← fastapi, uvicorn, weasyprint, pydyf
├── apt.txt              ← libs système pour weasyprint (pango, cairo, gdk-pixbuf)
├── NOTOX_VERT_BD.png    ← logo embarqué en base64 au démarrage pour les PDF
├── README.md            ← vide / à compléter
├── SETUP.md             ← guide d'install complet (Supabase + Render + GitHub Pages + UptimeRobot)
├── CLAUDE.md            ← CE DOCUMENT
└── docs/
    └── fixes/
        └── odoo-mailer-chatter-bubble.md   ← spec du fix email (non appliqué encore)
```

### Frontend (`garybot/`)

```
garybot/
├── index.html           ← monolithe 1345 lignes, toute l'UI + logique
├── messages.js          ← templates des messages clients (texte brut)
├── garybot_logo.png     ← favicon + logo header
├── build.sh             ← cache-busting : injecte un timestamp dans <script src="messages.js?v=...">
└── README.md
```

---

## 5. Endpoints backend (`main.py`)

| Route | Méthode | Auth | Rôle |
|---|---|---|---|
| `/health` | GET/HEAD | ❌ | Keep-alive UptimeRobot |
| `/orders` | GET | `x-api-secret` | Liste commandes (state=sale, non livrées, facturées). Delta sync via `?since=ISO_TS`. Retourne commandes enrichies (lignes + partenaire + shipping). |
| `/stats` | GET | `x-api-secret` | Livraisons sur 12 mois glissants groupées par mois |
| `/config` | GET | `x-api-secret` | Check env vars Odoo |
| `/orders/{id}/pdf` | GET | `x-api-secret` | Génère PDF commande (weasyprint, format interne NOTOX, logo embarqué) |
| `/orders/{id}/message` | POST | `x-api-secret` | **Envoie email client via `mail.compose.message` en mode `comment`. Archive dans chatter Odoo.** |
| `/debug` | GET | ❌ | Diagnostic env vars (sensible, à retirer en prod) |

### Auth backend
Header `x-api-secret: notox2026` (ou valeur de la var `API_SECRET`).

### Variables d'environnement Render
```
ODOO_URL      = https://notoxsurf.odoo.com
ODOO_DB       = notoxsurf (ou nom réel de la base)
ODOO_USER     = pierre@notoxsurf.com
ODOO_API_KEY  = <clé API Odoo, PAS le mot de passe>
API_SECRET    = notox2026
```

---

## 6. Logique métier : étapes de commande

Défini dans `garybot/index.html` (ligne ~402). 11 étapes dans l'ordre :

| # | ID | Label | Mail fournisseur | Mail client |
|---|---|---|---|---|
| 1 | `appro_blank` | Appro. Blank | Viral / Atua / Ben | — |
| 2 | `cmd_preshape` | Cmd. Preshape | Viral / Atua / Ben | — |
| 3 | `cmd_access` | Cmd. Access. | Viral / FCS / Surf System | — |
| 4 | `shape` | Shape | — | ✅ |
| 5 | `deco` | Déco | — | ✅ |
| 6 | `strat` | Strat | — | ✅ |
| 7 | `pose_plugs` | Pose Plugs | — | — |
| 8 | `poncage` | Ponçage | — | ✅ |
| 9 | `finition` | Finition | — | ✅ |
| 10 | `emballage` | Emballage | — | ✅ |
| 11 | `livraison` | Livraison | — | ✅ (avec infos livraison/retrait) |

### Templates messages clients (`messages.js`)

- `MESSAGES.default(prenom, ref, stepLabel, progress)` → message d'avancement générique
- `MESSAGES.livraison(prenom, ref)` → message spécifique livraison prête
- Lookup : `MESSAGES[stepId] || MESSAGES.default`
- Texte brut, `\n` converti en `<br>` côté frontend via `plainTextToHtml()` avant envoi

### Flow d'envoi d'un message client
1. User coche une étape avec `clientMail:true` → modal s'ouvre (`openClientMessageModal`)
2. Modal pré-rempli avec sujet + body depuis `MESSAGES`
3. User peut ajouter jusqu'à 4 photos (redimensionnées à 1280px max, base64)
4. `POST /orders/{id}/message` → backend crée les `ir.attachment` puis `mail.compose.message` → envoi via `action_send_mail`
5. Email reçu par le client + archivé dans chatter du `sale.order`

### Archivage commandes
- Étape "Livraison" cochée → bannière d'archivage
- **Auto-archive** après `ARCHIVE_DELAY_DAYS` = 15 jours
- **Purge définitive** après `PURGE_DELAY_DAYS` = 30 jours (supprime de Supabase, reste dans Odoo)

---

## 7. 🐛 Bugs connus / en cours

### Bug actif : `email_from` incorrect (non résolu au 2026-04-20)

**Symptômes** :
1. Bulle bleue dans chatter Odoo au lieu de verte (mails apparaissent comme "externes")
2. Quand client répond → bounce MAILER-DAEMON "The email sent to catchall@notoxsurf.com cannot be processed"

**Localisation** : `main.py` ligne 497 :
```python
EMAIL_FROM_DEFAULT = '"NOTOX" <contact@notox.fr>'
```

**Cause** : le domaine `notox.fr` ne matche aucun user Odoo + est différent du domaine catchall (`notoxsurf.com`).

**Fix à appliquer** : voir `docs/fixes/odoo-mailer-chatter-bubble.md`.
Spec créée, mais **code pas encore modifié**. Trois changements requis dans `post_order_message` :

1. Ajouter une fonction `get_current_user_partner_id(uid)` qui lit `res.users.partner_id`
2. Ajouter `"author_id": <partner_id de l'user XML-RPC>` dans `composer_vals`
3. Remplacer `EMAIL_FROM_DEFAULT` par `'"NOTOX" <pierre@notoxsurf.com>'` (même domaine que le catchall)

Le `reply_to_force_new: False` est déjà présent (ligne 556). ✅

---

## 8. Conventions de code

### Backend Python
- **Monolithe assumé** : tout dans `main.py`. Pas de refacto en modules tant que ça reste sous 1000 lignes.
- **Logs** : `print(..., flush=True)` avec préfixes `[PDF]`, `[MSG]`, `✓` / `✗` pour succès/échec. Pas de `logging` module pour l'instant.
- **Gestion d'erreurs** : `HTTPException` avec `status_code=502` pour les erreurs Odoo, `400` pour payload invalide, `500` pour bug interne.
- **Typage** : Pydantic pour les payloads (`MessageIn`, `AttachmentIn`). Type hints sur les fonctions publiques.
- **Imports défensifs** : weasyprint wrappé dans try/except pour que l'app démarre même si les libs natives manquent (cas au cold start Render).

### Frontend JS
- **Pas de framework** : vanilla JS, rendering via template strings + `render()` global.
- **State unique** : objet `S` global mutable. `render()` re-rend tout.
- **Supabase** : client CDN (pas de bundler).
- **Cache-busting** : `messages.js?v=TIMESTAMP` via `build.sh` à chaque deploy.

### Git / PR
- Commit style : `fix(scope): description courte` ou `feat(scope): ...`
- Scopes fréquents : `odoo-mailer`, `pdf`, `orders-sync`, `frontend`, `archive`

---

## 9. Comment tester localement

### Backend
```bash
cd garybot-api
pip install -r requirements.txt
export ODOO_URL=https://notoxsurf.odoo.com
export ODOO_DB=<db>
export ODOO_USER=pierre@notoxsurf.com
export ODOO_API_KEY=<clé>
export API_SECRET=notox2026
uvicorn main:app --reload
# → http://localhost:8000/health
```

### Frontend
Pas de build, ouvrir `index.html` directement. Mais attention CORS : modifier `CONFIG.backendUrl` pour pointer vers localhost si backend local.

### Tester le bug email
1. Lancer le backend
2. Trouver un `sale.order` de test dans Odoo (state=sale, avec un partner qui a un email)
3. `POST /orders/<id>/message` avec un body JSON simple, voir si l'email arrive + bulle verte dans chatter Odoo

---

## 10. Points d'attention pour Claude

Quand on intervient sur ce projet :

1. **Ne PAS créer de user Odoo** (coût SaaS).
2. **Ne PAS tenter l'API REST Odoo** (404, pas disponible).
3. **Ne PAS utiliser `message_post` avec body HTML** (escape), utiliser `mail.compose.message` en mode `comment`.
4. **Toujours tester avec un `sale.order` de staging** avant d'appliquer sur prod.
5. **Vérifier l'impact des changements sur les deux repos** : certains fixes nécessitent de modifier `main.py` ET `index.html` (ex : nouveau champ dans le payload).
6. **Les env vars Render doivent être mises à jour manuellement** sur le dashboard Render, pas en code.
7. **Le frontend est en cache agressif** : après modif de `messages.js`, lancer `./build.sh` pour bump le `?v=`.
8. **Render free tier dort** : premier appel après 15 min d'inactivité = 30s de délai. Vérifier UptimeRobot si ça traîne.

---

## 11. Roadmap / idées futures

- [ ] Appliquer le fix `email_from` + `author_id` (urgent, cf §7)
- [ ] Configurer DNS + SMTP custom pour utiliser `contact@notoxsurf.com` comme vrai expéditeur
- [ ] Retirer ou protéger l'endpoint `/debug`
- [ ] Ajouter des tests unitaires sur le backend (pytest)
- [ ] Sortir la config dans un fichier `.env.example` versionné
- [ ] Sync Odoo automatique (webhook ou polling côté frontend) au lieu du bouton manuel
