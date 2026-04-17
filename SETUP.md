# GaryBot · Guide de setup v3
## NOTOX / GREEN WAVE — Gestion commandes en cours

---

## Architecture

```
Navigateur (GitHub Pages)
        ↓ HTTPS
FastAPI (Render.com)  ──→  Odoo SaaS (XML-RPC)
        ↓
    Supabase (PostgreSQL)
        ↑
    UptimeRobot (keep-alive)
```

---

## Étape 1 — Supabase (base de données + auth)

1. Va sur https://supabase.com → **Start your project** → **New project**
2. Nom : `garybot` · Région : **West EU (Ireland)** · Note le mot de passe
3. Attends 2 min que le projet démarre
4. Menu gauche → **SQL Editor** → **New Query**
   - Colle le contenu de `schema.sql` en entier → clique **Run**
   - Tu dois voir "Success. No rows returned." → c'est normal et correct
5. Menu → **Authentication** → **Users** → **Add user** → **Create new user**
   - Saisis email + mot de passe directement (pas d'invitation)
   - Crée un compte pour toi et chaque collègue
   - **Ils n'ont pas besoin d'un compte supabase.com** — c'est toi l'admin, ils ont juste un login dans ta base
6. Menu → **Settings** → **API** → note ces deux valeurs :
   - `Project URL` → ex: `https://abcdefgh.supabase.co`
   - `anon public` key → la longue clé qui commence par `eyJ...` ⚠️ pas la "publishable key"

---

## Étape 2 — Render.com (backend FastAPI)

1. Crée un compte sur https://render.com
2. Sur GitHub, crée un repo public `garybot-api`
3. Dans ce repo, pousse deux fichiers :
   - `main.py`
   - `requirements.txt`
4. Sur Render → **New** → **Web Service** → connecte le repo `garybot-api`
5. Paramètres :
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type** : Free
6. Onglet **Environment** → ajoute ces variables :

```
ODOO_URL      = https://TON-INSTANCE.odoo.com   (sans slash final)
ODOO_DB       = nom-de-ta-base-odoo
ODOO_USER     = ton@email.com
ODOO_API_KEY  = ta-cle-api-odoo
API_SECRET    = un-mot-de-passe-que-tu-inventes (ex: notox2026)
```

7. Clique **Deploy** → attends 2-3 min → note l'URL : `https://garybot-api.onrender.com`
8. Teste : ouvre `https://garybot-api.onrender.com/health` → tu dois voir `{"status":"ok","service":"GaryBot API v3"}`

---

## Étape 3 — GitHub Pages (frontend)

1. Sur GitHub, crée un repo public `garybot`
2. Ouvre `index.html` et remplace les 4 valeurs en haut du fichier :

```javascript
const CONFIG = {
  supabaseUrl:  "https://XXXXXXXX.supabase.co",       // Étape 1, point 6
  supabaseKey:  "eyJhbGciOiJIUzI1NiIsInR5cCI6...",   // Étape 1, point 6 (commence par eyJ)
  backendUrl:   "https://garybot-api.onrender.com",    // Étape 2, point 7 (sans slash final)
  apiSecret:    "notox2026",                           // Étape 2, point 6 (même valeur)
};
```

3. Pousse `index.html` dans la branche `main` du repo `garybot`
4. Settings → **Pages** → Source : **Deploy from branch** → `main` / `/ (root)` → **Save**
5. Attends 2 min → l'URL est disponible : `https://pierrepomiers.github.io/garybot`

---

## Étape 4 — UptimeRobot (keep-alive Render)

Le free tier de Render endort le service après 15 min d'inactivité → premier sync lent (30s).
UptimeRobot ping l'API toutes les 5 min pour la garder éveillée — gratuit.

1. Crée un compte sur https://uptimerobot.com
2. **+ New Monitor**
3. Remplis :
   - **Monitor Type** : `HTTP(s)`
   - **Friendly Name** : `GaryBot Keep Alive`
   - **URL** : `https://garybot-api.onrender.com/health`
   - **Monitoring Interval** : `5 minutes`
4. **Create Monitor**
5. Le voyant passe au vert en 30-40 secondes (premier réveil)

En bonus : UptimeRobot envoie un email si l'API tombe en panne.

---

## Étape 5 — Première utilisation

1. Ouvre `https://pierrepomiers.github.io/garybot` sur mobile ou desktop
2. Connecte-toi avec l'email/mot de passe créé dans Supabase (Étape 1)
3. Va dans **⚙ Config** → renseigne les emails et contacts des fournisseurs → **Sauvegarder**
4. Clique **⟳ Odoo** → les commandes en cours apparaissent
5. Envoie l'URL à tes collègues avec leurs identifiants

---

## Fonctionnement de l'archivage

- Quand tu coches l'étape **Livraison** d'une commande, une bannière apparaît proposant l'archivage
- **Archivage automatique** : si la livraison reste cochée pendant 3 jours, la commande est archivée automatiquement au prochain sync Odoo
- Une commande archivée **disparaît du board** mais reste en base Supabase
- **Purge** : dans ⚙ Config → tu peux purger définitivement les commandes archivées (elles restent dans Odoo)

---

## Notes techniques

| Sujet | Détail |
|---|---|
| Sync Odoo | Manuel (bouton ⟳) — pas de polling automatique |
| Temps réel | Les coches sont visibles instantanément par toute l'équipe |
| Filtre Odoo | `state=sale` + non entièrement livrée + avec facture |
| Render free tier | Dort après 15 min sans UptimeRobot. Upgrade $7/mois pour s'en passer. |
| UptimeRobot | Ping toutes les 5 min → Render reste éveillé. Gratuit. |
| Données | 100% dans ton Supabase — exportable CSV depuis le dashboard |
| Capacité | Free tier Supabase = 50 000 lignes. Largement suffisant avec purge régulière. |

---

## En cas de problème

| Symptôme | Solution |
|---|---|
| Erreur sync Odoo | Vérifie les variables d'environnement Render + que l'API Odoo est active |
| Login impossible | Vérifie l'URL Supabase et que la clé commence par `eyJ` (pas `sb_publishable`) |
| Emails fournisseurs vides | ⚙ Config → renseigner + Sauvegarder |
| Commandes absentes | Vérifie que les commandes Odoo sont `state=sale` avec une facture |
| Render endormi | Attends 30s le temps du réveil, puis re-clique ⟳ (ou active UptimeRobot) |
| UptimeRobot rouge | Normal au démarrage — attend 1 min, le service se réveille |
