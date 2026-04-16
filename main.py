"""
MichelBot Backend - FastAPI v3
Proxy Odoo SaaS + endpoints archivage/purge
Déployer sur Render.com
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import os
from typing import Optional

app = FastAPI(title="MichelBot API")

# ─── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restreindre à ton domaine GitHub Pages en prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── CONFIG (variables d'environnement sur Render) ────────────────────────────
ODOO_URL     = os.environ.get("ODOO_URL", "")       # ex: https://notox.odoo.com
ODOO_DB      = os.environ.get("ODOO_DB", "")        # ex: notox
ODOO_USER    = os.environ.get("ODOO_USER", "")      # email admin Odoo
ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "")   # clé API Odoo
API_SECRET   = os.environ.get("API_SECRET", "michelbot-secret")

# ─── AUTH ─────────────────────────────────────────────────────────────────────
def check_auth(x_api_secret: str = Header(...)):
    if x_api_secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Non autorisé")

# ─── ODOO SESSION ─────────────────────────────────────────────────────────────
async def get_odoo_session(client: httpx.AsyncClient):
    resp = await client.post(
        f"{ODOO_URL}/web/session/authenticate",
        json={
            "jsonrpc": "2.0", "method": "call",
            "params": {"db": ODOO_DB, "login": ODOO_USER, "password": ODOO_API_KEY}
        }
    )
    data = resp.json()
    if not data.get("result", {}).get("uid"):
        raise HTTPException(status_code=502, detail="Authentification Odoo échouée")
    return resp.cookies

async def odoo_search_read(client, cookies, model, domain, fields, limit=200, order="date_order desc"):
    resp = await client.post(
        f"{ODOO_URL}/web/dataset/call_kw",
        cookies=cookies,
        json={
            "jsonrpc": "2.0", "method": "call",
            "params": {
                "model": model, "method": "search_read",
                "args": [domain],
                "kwargs": {"fields": fields, "limit": limit, "order": order}
            }
        }
    )
    data = resp.json()
    if "error" in data:
        raise HTTPException(status_code=502, detail=str(data["error"]))
    return data.get("result", [])

# ─── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "MichelBot API v3"}


@app.get("/orders", dependencies=[Depends(check_auth)])
async def get_orders():
    """
    Commandes de vente Odoo :
    - Validées (state = sale)
    - Non entièrement livrées
    - Avec au moins une facture (acompte)
    Enrichies avec lignes, client, adresse de livraison.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        cookies = await get_odoo_session(client)

        # 1. Commandes
        orders = await odoo_search_read(client, cookies, "sale.order",
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
                lines_data = await odoo_search_read(client, cookies, "sale.order.line",
                    domain=[["id", "in", line_ids]],
                    fields=["id", "product_id", "product_uom_qty", "price_unit",
                            "price_subtotal", "name", "qty_delivered", "qty_invoiced"],
                    order="id asc"
                )

            # 3. Client
            partner_id = order["partner_id"][0] if order.get("partner_id") else None
            partner_data = {}
            if partner_id:
                partners = await odoo_search_read(client, cookies, "res.partner",
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
                shippings = await odoo_search_read(client, cookies, "res.partner",
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
