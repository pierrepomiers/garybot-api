"""
GaryBot Backend - FastAPI v3
Proxy Odoo SaaS via XML-RPC + endpoints pour le frontend GaryBot
Déployer sur Render.com
"""

from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
import xmlrpc.client
import os
from datetime import datetime, timedelta
from typing import Optional

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

def odoo_search_read(uid, model, domain, fields, limit=200, order=None):
    """Appel XML-RPC search_read"""
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
        return models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            model, "search_read",
            [domain],
            {"fields": fields, "limit": limit, **({"order": order} if order else {})}
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erreur Odoo ({model}) : {str(e)}")

# ─── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "service": "GaryBot API v3"}


@app.get("/orders", dependencies=[Depends(check_auth)])
def get_orders(since: Optional[str] = Query(None, description="ISO timestamp pour delta sync (ex: 2025-01-01T00:00:00)")):
    """
    Commandes de vente Odoo :
    - Validées (state = sale)
    - Non entièrement livrées
    - Avec au moins une facture (acompte)
    Enrichies avec lignes, client, adresse de livraison.

    Delta sync : passer ?since=TIMESTAMP pour ne récupérer que les commandes
    modifiées après ce timestamp.
    """
    uid = get_odoo_uid()

    # 1. Commandes (avec filtre delta sync optionnel)
    domain = [
        ["state", "=", "sale"],
        ["invoice_status", "in", ["invoiced", "to invoice"]],
        ["delivery_status", "!=", "full"],
    ]
    if since:
        domain.append(["write_date", ">", since])

    orders = odoo_search_read(uid, "sale.order",
        domain=domain,
        fields=[
            "id", "name", "date_order", "state",
            "partner_id", "partner_shipping_id",
            "amount_total", "amount_untaxed",
            "order_line", "invoice_ids",
            "invoice_status", "delivery_status",
            "commitment_date", "note",
            "team_id", "user_id",
        ],
        order="date_order desc"
    )

    if not orders:
        return {"orders": [], "count": 0, "sync_timestamp": datetime.utcnow().isoformat()}

    # 2. Batch : collecter tous les IDs à récupérer
    all_line_ids = []
    all_partner_ids = set()
    for order in orders:
        all_line_ids.extend(order.get("order_line", []))
        if order.get("partner_id"):
            all_partner_ids.add(order["partner_id"][0])
        shipping = order.get("partner_shipping_id", [])
        if shipping:
            all_partner_ids.add(shipping[0])

    # 3. Batch : un seul appel pour toutes les lignes
    lines_by_order = {}
    if all_line_ids:
        all_lines = odoo_search_read(uid, "sale.order.line",
            domain=[["id", "in", all_line_ids]],
            fields=["id", "order_id", "product_id", "product_uom_qty", "price_unit",
                    "price_subtotal", "name", "qty_delivered", "qty_invoiced"],
            limit=5000
        )
        for line in all_lines:
            oid = line["order_id"][0] if line.get("order_id") else None
            if oid:
                lines_by_order.setdefault(oid, []).append(line)

    # 4. Batch : un seul appel pour tous les partenaires (clients + adresses)
    partners_by_id = {}
    if all_partner_ids:
        all_partners = odoo_search_read(uid, "res.partner",
            domain=[["id", "in", list(all_partner_ids)]],
            fields=["id", "name", "email", "phone",
                    "street", "city", "zip", "country_id"],
            limit=5000
        )
        for p in all_partners:
            partners_by_id[p["id"]] = p

    # 5. Assembler les données côté Python
    enriched = []
    for order in orders:
        partner_id = order["partner_id"][0] if order.get("partner_id") else None
        shipping_id = order["partner_shipping_id"][0] if order.get("partner_shipping_id") else None

        partner_data = partners_by_id.get(partner_id, {})
        shipping_data = {}
        if shipping_id and shipping_id != partner_id:
            shipping_data = partners_by_id.get(shipping_id, {})

        enriched.append({
            **order,
            "lines_detail":   lines_by_order.get(order["id"], []),
            "partner_detail": partner_data,
            "shipping_detail": shipping_data or partner_data,
        })

    return {"orders": enriched, "count": len(enriched), "sync_timestamp": datetime.utcnow().isoformat()}


@app.get("/stats", dependencies=[Depends(check_auth)])
def get_stats():
    """
    Statistiques de livraison : nombre de commandes livrées (delivery_status = full)
    groupé par mois sur les 12 derniers mois.
    """
    uid = get_odoo_uid()

    twelve_months_ago = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d 00:00:00")

    delivered = odoo_search_read(uid, "sale.order",
        domain=[
            ["delivery_status", "=", "full"],
            ["date_order", ">=", twelve_months_ago],
        ],
        fields=["id", "date_order"],
        limit=5000,
        order="date_order asc"
    )

    # Grouper par mois (YYYY-MM)
    by_month: dict[str, int] = {}
    for order in delivered:
        date_str = order.get("date_order", "")
        if date_str:
            month_key = date_str[:7]  # "2025-03"
            by_month[month_key] = by_month.get(month_key, 0) + 1

    return {
        "total_delivered": len(delivered),
        "by_month": by_month,
        "period_start": twelve_months_ago[:10],
        "period_end": datetime.utcnow().strftime("%Y-%m-%d"),
    }


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
