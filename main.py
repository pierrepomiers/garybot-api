"""
GaryBot Backend - FastAPI v3
Proxy Odoo SaaS via XML-RPC + endpoints pour le frontend GaryBot
Déployer sur Render.com
"""

from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import xmlrpc.client
import os
import io
import base64
import html
import traceback
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

# Import défensif : si les libs système (pango/cairo/gdk-pixbuf) manquent, on veut
# que l'app démarre quand même — seul l'endpoint PDF renverra alors une erreur claire.
try:
    from weasyprint import HTML as _WeasyHTML
    _WEASYPRINT_IMPORT_ERROR: Optional[str] = None
except Exception as _e:  # ImportError, OSError (libs natives manquantes), etc.
    _WeasyHTML = None  # type: ignore[assignment]
    _WEASYPRINT_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"
    print(f"[PDF] ✗ import weasyprint échoué : {_WEASYPRINT_IMPORT_ERROR}", flush=True)

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
ODOO_URL      = os.environ.get("ODOO_URL", "")
ODOO_DB       = os.environ.get("ODOO_DB", "")
ODOO_USER     = os.environ.get("ODOO_USER", "")
ODOO_API_KEY  = os.environ.get("ODOO_API_KEY", "")
API_SECRET    = os.environ.get("API_SECRET", "garybot-secret")

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
                    "price_subtotal", "name", "qty_delivered", "qty_invoiced",
                    "discount", "price_tax"],
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


# ─── LOGO (encodé en base64 au démarrage pour éviter tout I/O par requête) ────
_LOGO_PATH = Path(__file__).parent / "NOTOX_VERT_BD.png"
try:
    LOGO_B64 = base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
except FileNotFoundError:
    LOGO_B64 = ""


def _fmt_money(x: float) -> str:
    """Format monétaire FR : 1 234,56 €"""
    try:
        s = f"{float(x):,.2f}"
    except (TypeError, ValueError):
        return "0,00 €"
    return s.replace(",", " ").replace(".", ",") + " €"


def _fmt_qty(x: float) -> str:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return "0"
    return (f"{f:.2f}".rstrip("0").rstrip(".")).replace(".", ",") or "0"


def _fmt_date(iso: str) -> str:
    if not iso:
        return ""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return iso[:10]


def _build_order_html(order: dict, lines: list, partner: dict, user_name: str) -> str:
    """Construit le HTML du devis/commande au format interne NOTOX."""
    order_name = html.escape(order.get("name") or "")
    date_order = _fmt_date(order.get("date_order") or "")
    vendeur = html.escape(user_name or "")

    partner_name = html.escape(partner.get("name") or "")
    partner_street = html.escape(partner.get("street") or "")
    partner_city = html.escape(partner.get("city") or "")
    partner_zip = html.escape(partner.get("zip") or "")
    country = partner.get("country_id")
    partner_country = html.escape(country[1] if isinstance(country, list) and len(country) > 1 else "")

    # Séparer les vraies lignes produit des notes (price_unit=0 et product_id=false)
    rows_html = []
    amount_untaxed = 0.0
    amount_tax = 0.0

    for line in lines:
        price_unit = float(line.get("price_unit") or 0)
        product_id = line.get("product_id")
        is_note = (not product_id) and price_unit == 0

        if is_note:
            # Note / détail — affichée en italique sous la ligne précédente
            note_text = html.escape(line.get("name") or "").replace("\n", "<br>")
            rows_html.append(
                f'<tr class="note-row"><td colspan="6"><em>{note_text}</em></td></tr>'
            )
            continue

        qty = float(line.get("product_uom_qty") or 0)
        discount = float(line.get("discount") or 0)
        price_subtotal = float(line.get("price_subtotal") or 0)
        price_tax = float(line.get("price_tax") or 0)
        amount_untaxed += price_subtotal
        amount_tax += price_tax

        desc = html.escape(line.get("name") or "").replace("\n", "<br>")
        rows_html.append(
            "<tr>"
            f'<td class="desc">{desc}</td>'
            f'<td class="num">{_fmt_qty(qty)}</td>'
            f'<td class="num">{_fmt_money(price_unit)}</td>'
            f'<td class="num">{_fmt_qty(discount)}%</td>'
            f'<td class="num">{_fmt_money(price_tax)}</td>'
            f'<td class="num">{_fmt_money(price_subtotal)}</td>'
            "</tr>"
        )

    # Totaux : préférer les valeurs Odoo si présentes
    amount_total_odoo = order.get("amount_total")
    amount_untaxed_odoo = order.get("amount_untaxed")
    total_ht = float(amount_untaxed_odoo) if amount_untaxed_odoo is not None else amount_untaxed
    total_ttc = float(amount_total_odoo) if amount_total_odoo is not None else (amount_untaxed + amount_tax)
    total_tax = total_ttc - total_ht

    logo_src = f"data:image/png;base64,{LOGO_B64}" if LOGO_B64 else ""

    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>Commande {order_name}</title>
<style>
  @page {{ size: A4; margin: 18mm 16mm 18mm 16mm; }}
  body {{ font-family: "Helvetica", "Arial", sans-serif; font-size: 10pt; color: #222; }}
  .header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 24px; }}
  .header img {{ max-height: 70px; }}
  .company {{ text-align: right; font-size: 9pt; line-height: 1.4; }}
  .company b {{ color: #006633; font-size: 10pt; }}
  .client {{ margin: 20px 0 24px 0; padding: 10px 0; }}
  .client .label {{ font-size: 8pt; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }}
  .client .name {{ font-weight: bold; font-size: 11pt; }}
  h1.title {{ color: #006633; font-size: 20pt; margin: 16px 0 12px 0; font-weight: bold; }}
  .infos {{ display: flex; gap: 40px; margin-bottom: 20px; font-size: 10pt; }}
  .infos .col {{ flex: 1; }}
  .infos .label {{ font-size: 8pt; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 2px; }}
  table.lines {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  table.lines thead th {{ background: #006633; color: #fff; font-weight: bold; padding: 8px 6px; text-align: left; font-size: 9pt; }}
  table.lines thead th.num {{ text-align: right; }}
  table.lines tbody td {{ padding: 7px 6px; border-bottom: 1px solid #e0e0e0; vertical-align: top; }}
  table.lines tbody td.num {{ text-align: right; white-space: nowrap; }}
  table.lines tbody td.desc {{ width: 42%; }}
  tr.note-row td {{ border-bottom: 1px solid #e0e0e0; padding-top: 0; padding-left: 14px; color: #555; font-size: 9pt; }}
  .totals {{ margin-top: 18px; width: 45%; margin-left: auto; }}
  .totals tr td {{ padding: 5px 6px; }}
  .totals tr td.label {{ text-align: right; color: #555; }}
  .totals tr td.value {{ text-align: right; white-space: nowrap; }}
  .totals tr.grand td {{ font-weight: bold; color: #006633; font-size: 12pt; border-top: 2px solid #006633; padding-top: 8px; }}
</style></head>
<body>
  <div class="header">
    {'<img src="' + logo_src + '" alt="NOTOX">' if logo_src else '<div></div>'}
    <div class="company">
      <b>GREEN WAVE SAS</b><br>
      6 RUE DU LAZARET<br>
      64600 ANGLET<br>
      France
    </div>
  </div>

  <div class="client">
    <div class="label">Client</div>
    <div class="name">{partner_name}</div>
    <div>{partner_street}</div>
    <div>{partner_zip} {partner_city}</div>
    <div>{partner_country}</div>
  </div>

  <h1 class="title">Commande # {order_name}</h1>

  <div class="infos">
    <div class="col">
      <div class="label">Date de commande</div>
      <div>{date_order}</div>
    </div>
    <div class="col">
      <div class="label">Vendeur</div>
      <div>{vendeur}</div>
    </div>
  </div>

  <table class="lines">
    <thead>
      <tr>
        <th>Description</th>
        <th class="num">Quantité</th>
        <th class="num">Prix unitaire</th>
        <th class="num">Rem.%</th>
        <th class="num">TVA</th>
        <th class="num">Montant</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>

  <table class="totals">
    <tr><td class="label">Montant HT</td><td class="value">{_fmt_money(total_ht)}</td></tr>
    <tr><td class="label">TVA</td><td class="value">{_fmt_money(total_tax)}</td></tr>
    <tr class="grand"><td class="label">Total</td><td class="value">{_fmt_money(total_ttc)}</td></tr>
  </table>
</body></html>"""


@app.get("/orders/{order_id}/pdf", dependencies=[Depends(check_auth)])
def get_order_pdf(order_id: int):
    """
    Génère un PDF au format interne NOTOX à partir des données Odoo (XML-RPC).
    Rendu via weasyprint, logo embarqué en base64.
    """
    uid = get_odoo_uid()

    orders = odoo_search_read(uid, "sale.order",
        domain=[["id", "=", order_id]],
        fields=[
            "id", "name", "date_order", "state",
            "partner_id", "amount_total", "amount_untaxed",
            "order_line", "user_id",
        ],
        limit=1,
    )
    if not orders:
        raise HTTPException(status_code=404, detail=f"Commande {order_id} introuvable")
    order = orders[0]

    line_ids = order.get("order_line") or []
    lines = []
    if line_ids:
        lines = odoo_search_read(uid, "sale.order.line",
            domain=[["id", "in", line_ids]],
            fields=["id", "product_id", "product_uom_qty", "price_unit",
                    "price_subtotal", "price_tax", "discount", "name"],
            limit=500,
            order="id asc",
        )

    partner = {}
    if order.get("partner_id"):
        partner_res = odoo_search_read(uid, "res.partner",
            domain=[["id", "=", order["partner_id"][0]]],
            fields=["id", "name", "street", "city", "zip", "country_id"],
            limit=1,
        )
        if partner_res:
            partner = partner_res[0]

    user_name = ""
    if order.get("user_id") and isinstance(order["user_id"], list) and len(order["user_id"]) > 1:
        user_name = order["user_id"][1]

    html_doc = _build_order_html(order, lines, partner, user_name)

    if _WeasyHTML is None:
        # Libs natives manquantes au démarrage — message déjà loggué à l'import.
        print(f"[PDF] ✗ weasyprint indisponible (import échoué) : {_WEASYPRINT_IMPORT_ERROR}", flush=True)
        raise HTTPException(
            status_code=500,
            detail=f"weasyprint indisponible — {_WEASYPRINT_IMPORT_ERROR}",
        )

    print(f"[PDF] ▶ génération commande_id={order_id} name={order.get('name')!r} lignes={len(lines)} html={len(html_doc)}o", flush=True)
    try:
        pdf_bytes = _WeasyHTML(string=html_doc).write_pdf()
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[PDF] ✗ weasyprint.write_pdf() : {type(e).__name__}: {e}\n{tb}", flush=True)
        raise HTTPException(
            status_code=500,
            detail=f"Erreur génération PDF ({type(e).__name__}) : {str(e)}",
        )
    print(f"[PDF] ✓ commande_id={order_id} rendue ({len(pdf_bytes)} octets)", flush=True)

    filename = f"commande-{order.get('name') or order_id}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─── MESSAGE ODOO (chatter + email client) ────────────────────────────────────
class AttachmentIn(BaseModel):
    name: str
    data: str  # base64 (sans préfixe data:)


class MessageIn(BaseModel):
    body: str = Field(..., description="Corps HTML du message")
    subject: str = ""
    partner_id: int
    attachments: list[AttachmentIn] = []


@app.post("/orders/{order_id}/message", dependencies=[Depends(check_auth)])
def post_order_message(order_id: int, payload: MessageIn):
    """
    Poste un message sur la commande Odoo (chatter) et notifie le partenaire
    indiqué par email via `mail.mt_comment`. Les pièces jointes sont créées
    comme `ir.attachment` puis liées au message.
    """
    if not payload.body.strip():
        raise HTTPException(status_code=400, detail="body vide")

    uid = get_odoo_uid()
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        attachment_ids: list[int] = []
        for att in payload.attachments:
            if not att.data:
                continue
            att_id = models.execute_kw(
                ODOO_DB, uid, ODOO_API_KEY,
                "ir.attachment", "create",
                [{
                    "name": att.name or "attachment",
                    "datas": att.data,
                    "res_model": "sale.order",
                    "res_id": order_id,
                    "type": "binary",
                }],
            )
            attachment_ids.append(att_id)

        kwargs = {
            "body": payload.body,
            "subject": payload.subject or False,
            "message_type": "comment",
            "subtype_xmlid": "mail.mt_comment",
            "partner_ids": [int(payload.partner_id)],
        }
        if attachment_ids:
            kwargs["attachment_ids"] = attachment_ids

        message_id = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "sale.order", "message_post",
            [[order_id]],
            kwargs,
        )

        print(f"[MSG] ✓ commande_id={order_id} message_id={message_id} attachments={len(attachment_ids)}", flush=True)
        return {"success": True, "message_id": message_id, "attachment_ids": attachment_ids}
    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[MSG] ✗ commande_id={order_id} : {type(e).__name__}: {e}\n{tb}", flush=True)
        raise HTTPException(status_code=502, detail=f"Erreur Odoo message_post : {str(e)}")


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
