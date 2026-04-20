# Fix : Bulle bleue + bounce catchall sur emails Odoo XML-RPC

## Contexte
Le backend FastAPI envoie des emails au client via Odoo 19 SaaS (XML-RPC) en utilisant `mail.compose.message`. L'envoi fonctionne, mais deux bugs sont apparus :

1. **Bulle bleue au lieu de verte** dans le chatter du sale.order (Odoo traite le message comme un email externe au lieu d'un message interne)
2. **Bounce catchall** : quand le client répond, il reçoit un MAILER-DAEMON "The email sent to catchall@notoxsurf.com cannot be processed"

## Cause racine
Dans `composer_vals`, on passe `email_from='"NOTOX" <contact@notox.fr>'`. Ce domaine `notox.fr` :
- Ne matche aucun user Odoo interne → `author_id` non résolu → bulle bleue
- Est différent du domaine catchall `notoxsurf.com` → le reply-to token est cassé → bounce

## Solution
Modifier la fonction d'envoi d'email (probablement dans `services/odoo_mailer.py` ou équivalent) pour :

1. Récupérer le `partner_id` du user XML-RPC authentifié (Pierre) une fois au démarrage
2. Ajouter `author_id=PIERRE_PARTNER_ID` dans les vals du composer
3. Changer `email_from` pour utiliser le domaine `notoxsurf.com` (même domaine que le catchall)
4. Ajouter `reply_to_force_new=False` pour préserver le threading natif

## Changements précis

### Ajouter au démarrage du module (après la connexion XML-RPC) :

```python
def get_current_user_partner_id():
    """Récupère le res.partner lié au user XML-RPC connecté."""
    user_data = models.execute_kw(
        db, uid, password,
        'res.users', 'read',
        [[uid], ['partner_id']]
    )
    return user_data[0]['partner_id'][0]

PIERRE_PARTNER_ID = get_current_user_partner_id()
```

### Dans `composer_vals`, ajouter/modifier ces 3 clés :

```python
composer_vals = {
    # ... clés existantes inchangées ...
    'author_id': PIERRE_PARTNER_ID,                    # NOUVEAU : force l'auteur interne
    'email_from': '"NOTOX" ',    # MODIFIÉ : domaine notoxsurf.com (avant: contact@notox.fr)
    'reply_to_force_new': False,                       # NOUVEAU : threading natif préservé
}
```

## Contraintes
- **Ne PAS créer de nouveau user Odoo** (facturé sur SaaS)
- **Ne PAS configurer de serveur SMTP custom** pour cette itération
- L'user XML-RPC reste `pierre@notoxsurf.com`
- Le nom affiché côté client doit rester "NOTOX" (identité de marque)
- L'email réel utilisé sera `pierre@notoxsurf.com` mais affiché comme "NOTOX"

## Tests à faire après modification

1. **Envoi test** : déclencher un email depuis le backend sur un sale.order de staging
2. **Vérif chatter Odoo** : la bulle doit être verte avec l'avatar de Pierre Pomiers
3. **Vérif côté client** : le mail reçu doit afficher "NOTOX" comme expéditeur
4. **Vérif reply** : répondre au mail depuis une boîte externe (Gmail) → la réponse doit apparaître dans le chatter du sale.order, sans bounce MAILER-DAEMON

## À faire

- [ ] Identifier le fichier contenant `send_email_via_composer` (ou équivalent)
- [ ] Ajouter la fonction `get_current_user_partner_id()` et la constante `PIERRE_PARTNER_ID`
- [ ] Modifier `composer_vals` avec les 3 changements
- [ ] Mettre à jour les tests unitaires si existants
- [ ] Committer avec message : `fix(odoo-mailer): resolve chatter bubble color and catchall bounce on reply`
- [ ] Créer une PR avec description du problème et de la solution

## Note pour plus tard (pas dans cette PR)
Pour avoir vraiment `contact@notoxsurf.com` comme expéditeur sans bounce, il faudra configurer un alias DNS + un serveur SMTP sortant custom (Google Workspace / Mailgun / OVH) avec SPF/DKIM sur `notoxsurf.com`. À faire dans une itération future.
