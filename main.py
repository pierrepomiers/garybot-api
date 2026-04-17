"""
GaryBot Backend - FastAPI v3
Proxy Odoo SaaS via XML-RPC + endpoints pour le frontend GaryBot
Déployer sur Render.com
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
import xmlrpc.client
import os

app = FastAPI(title="GaryBot API")

# ─── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
ODOO_URL     = os.environ.get("ODOO_URL", "")
ODOO_DB      = os.environ.get("ODOO_DB", "")
ODOO_USER    = os.environ.get("ODOO_USER", "")
ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "")
API_SECRET   = os.environ.get("API_SECRET", "garybot-secret")

# ─── AUTH GARYBOT ─────────────────────────────────────────────────────────────
def check_auth(x_api_secret: str = Header(...)):
    if x_api_secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Non autorisé")

# ─── ODOO XML-RPC ─────────────────────────────────────────────────────────────
def get_odoo_uid():
    """Authentification Odoo via XML-RPC (compatible SaaS)"""
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
        if not uid:
            raise HTTPException(status_code=502, detail="Authentification Odoo échouée — vérifier identifiants")
        return uid
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Connexion Odoo impossible : {str(e)}")

def odoo_search_read(uid, model, domain, fields, limit=200, order="date_order desc"):
    """Appel XML-RPC search_read"""
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
        return models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            model, "search_read",
            [domain],
            {"fields": fields, "limit": limit, "order": order}
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erreur Odoo ({model}) : {str(e)}")

# ─── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "GaryBot API v3"}


@app.get("/orders", dependencies=[Depends(check_auth)])
def get_orders():
    """
    Commandes de vente Odoo :
    - Validées (state = sale)
    - Non entièrement livrées
    - Avec au moins une facture (acompte)
    Enrichies avec lignes, client, adresse de livraison.
    """
    uid = get_odoo_uid()

    # 1. Commandes
    orders = odoo_search_read(uid, "sale.order",
        domain=[
            ["state", "=", "sale"],
            ["invoice_status", "in", ["invoiced", "to invoice"]],
            ["delivery_status", "!=", "full"],
        ],
        fields=[
            "id", "name", "date_order", "state",
            "partner_id", "partner_shipping_id",
            "amount_total", "amount_untaxed",
            "order_line", "invoice_ids",
            "invoice_status", "delivery_status",
            "commitment_date", "note",
            "team_id", "user_id",
        ]
    )

    enriched = []
    for order in orders:

        # 2. Lignes de commande
        line_ids = order.get("order_line", [])
        lines_data = []
        if line_ids:
            lines_data = odoo_search_read(uid, "sale.order.line",
                domain=[["id", "in", line_ids]],
                fields=["id", "product_id", "product_uom_qty", "price_unit",
                        "price_subtotal", "name", "qty_delivered", "qty_invoiced"],
                order="id asc"
            )

        # 3. Client
        partner_id = order["partner_id"][0] if order.get("partner_id") else None
        partner_data = {}
        if partner_id:
            partners = odoo_search_read(uid, "res.partner",
                domain=[["id", "=", partner_id]],
                fields=["id", "name", "email", "phone", "mobile",
                        "street", "city", "zip", "country_id"]
            )
            if partners:
                partner_data = partners[0]

        # 4. Adresse de livraison (si différente)
        shipping = order.get("partner_shipping_id", [])
        shipping_data = {}
        if shipping and shipping[0] != partner_id:
            shippings = odoo_search_read(uid, "res.partner",
                domain=[["id", "=", shipping[0]]],
                fields=["id", "name", "street", "city", "zip",
                        "country_id", "email", "phone"]
            )
            if shippings:
                shipping_data = shippings[0]

        enriched.append({
            **order,
            "lines_detail":   lines_data,
            "partner_detail": partner_data,
            "shipping_detail": shipping_data or partner_data,
        })

    return {"orders": enriched, "count": len(enriched)}


@app.get("/config", dependencies=[Depends(check_auth)])
def get_config():
    return {
        "odoo_url":  ODOO_URL,
        "odoo_db":   ODOO_DB,
        "api_ready": bool(ODOO_URL and ODOO_DB and ODOO_USER and ODOO_API_KEY)
    }


@app.get("/debug")
def debug():
    """Endpoint temporaire pour vérifier les variables d'environnement"""
    return {
        "odoo_url":    ODOO_URL  or "VIDE",
        "odoo_db":     ODOO_DB   or "VIDE",
        "odoo_user":   ODOO_USER or "VIDE",
        "api_key_set": bool(ODOO_API_KEY),
        "api_key_len": len(ODOO_API_KEY),
    }
