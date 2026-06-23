"""A local stand-in for the Clover REST API, for testing the connector
without a real Clover account.

Mimics the subset of the v3 API this connector calls: the merchant root,
the collection envelope (`elements` + `href`), `limit`/`offset` pagination,
`filter=<field>>=<value>` (epoch ms), and `expand=` inlining. Backed by an
in-memory Afghan-grocer sample catalog (items, categories, orders, line
items, payments...) generated fresh — with recent timestamps — every time
the server starts.

Usage:
    python dev/fake_clover_server.py [--port 8765] [--merchant-id FAKEMID1] [--token fake-token]

Then point the connector at it via .env:
    CLOVER_API_TOKEN=fake-token
    CLOVER_MERCHANT_ID=FAKEMID1
    CLOVER_BASE_URL=http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import time

from flask import Flask, jsonify, request

NOW_MS = int(time.time() * 1000)
DAY_MS = 24 * 60 * 60 * 1000


def days_ago(n: float) -> int:
    return NOW_MS - int(n * DAY_MS)


CATEGORIES = [
    {"id": "CAT-RICE", "name": "Rice & Grains"},
    {"id": "CAT-MEAT", "name": "Meat & Poultry"},
    {"id": "CAT-PRODUCE", "name": "Produce & Dried Fruit"},
    {"id": "CAT-BAKERY", "name": "Bakery & Bread"},
    {"id": "CAT-SPICE", "name": "Spices & Seasonings"},
    {"id": "CAT-DAIRY", "name": "Dairy"},
    {"id": "CAT-BEV", "name": "Beverages"},
    {"id": "CAT-CANNED", "name": "Canned & Packaged"},
]

TAGS = [
    {"id": "TAG-HALAL", "name": "Halal"},
    {"id": "TAG-LOCAL", "name": "Local Supplier"},
]

EMPLOYEES = [
    {"id": "EMP-AHMAD", "name": "Ahmad K."},
    {"id": "EMP-FATIMA", "name": "Fatima S."},
]

DEVICES = [
    {"id": "DEV-REG1", "name": "Register 1"},
    {"id": "DEV-REG2", "name": "Register 2"},
]

ORDER_TYPES = [
    {"id": "OT-INSTORE", "label": "In Store"},
    {"id": "OT-PHONE", "label": "Phone Order"},
]

TENDERS = [
    {"id": "TEN-CARD", "label": "Credit Card"},
    {"id": "TEN-CASH", "label": "Cash"},
]

MODIFIER_GROUPS: list[dict] = []

CUSTOMERS = [
    {"id": "CUST-1", "modifiedTime": days_ago(40), "firstName": "Test", "lastName": "Customer One"},
    {"id": "CUST-2", "modifiedTime": days_ago(12), "firstName": "Test", "lastName": "Customer Two"},
]

CASH_EVENTS = [
    {"id": "CASH-1", "type": "PAID_IN", "createdTime": days_ago(5), "amount": 5000},
]

CREDITS = [
    {"id": "CREDIT-1", "amount": 300, "createdTime": days_ago(20)},
]

# (id, name, category_id, price_cents, cost_cents, sku, stock_qty, modified_days_ago)
_ITEM_ROWS = [
    ("ITEM-RICE-BAS20", "Basmati Rice 20lb", "CAT-RICE", 2999, 2200, "RICE-BAS-20", 40, 2),
    ("ITEM-LAMB-SHO", "Halal Lamb Shoulder (per lb)", "CAT-MEAT", 899, 650, "LAMB-SHO-LB", 25, 1),
    ("ITEM-NAAN-PK4", "Fresh Naan Bread (pack of 4)", "CAT-BAKERY", 399, 180, "NAAN-PK4", 60, 0.5),
    ("ITEM-APRI-DR", "Dried Apricots 1lb", "CAT-PRODUCE", 549, 320, "APRI-DR-1LB", 35, 3),
    ("ITEM-CARD-POD", "Cardamom Pods 4oz", "CAT-SPICE", 699, 410, "CARD-POD-4OZ", 50, 6),
    ("ITEM-SAFF-1G", "Saffron 1g", "CAT-SPICE", 1299, 900, "SAFF-1G", 15, 6),
    ("ITEM-TEA-GRN", "Afghan Green Tea 16oz", "CAT-BEV", 649, 380, "TEA-GRN-16OZ", 45, 4),
    ("ITEM-YOG-WH", "Whole Milk Yogurt 32oz", "CAT-DAIRY", 449, 260, "YOG-WH-32OZ", 30, 0.2),
    ("ITEM-CHKP-CAN", "Chickpeas Canned 15oz", "CAT-CANNED", 199, 110, "CHKP-CAN-15OZ", 80, 10),
    ("ITEM-BEEF-GR", "Halal Ground Beef (per lb)", "CAT-MEAT", 699, 480, "BEEF-GR-1LB", 20, 1),
    ("ITEM-POM-MOL", "Pomegranate Molasses 12oz", "CAT-CANNED", 799, 520, "POM-MOL-12OZ", 22, 9),
    ("ITEM-SUMAC", "Sumac Spice 3oz", "CAT-SPICE", 599, 340, "SUMAC-3OZ", 38, 6),
    ("ITEM-SPIN-BUN", "Fresh Spinach Bunch", "CAT-PRODUCE", 299, 150, "SPIN-BUN", 18, 0.3),
    ("ITEM-ROSE-16OZ", "Rosewater 16oz", "CAT-BEV", 549, 310, "ROSE-16OZ", 27, 8),
    ("ITEM-WALN-1LB", "Walnuts 1lb", "CAT-PRODUCE", 899, 620, "WALN-1LB", 33, 3),
]

ITEMS = [
    {
        "id": row[0],
        "name": row[1],
        "categoryId": row[2],
        "price": row[3],
        "cost": row[4],
        "priceType": "FIXED",
        "sku": row[5],
        "code": row[5],
        "hidden": False,
        "modifiedTime": days_ago(row[7]),
    }
    for row in _ITEM_ROWS
]

ITEM_STOCKS = [
    {"itemId": row[0], "quantity": row[6], "modifiedTime": days_ago(row[7])} for row in _ITEM_ROWS
]

# (order_id, [(item_id, qty, price)], employee_id, device_id, order_type_id,
#  tender_id, days_ago_created, state)
_ORDER_ROWS = [
    ("ORD-1001", [("ITEM-RICE-BAS20", 1, 2999), ("ITEM-LAMB-SHO", 3, 899)], "EMP-AHMAD", "DEV-REG1", "OT-INSTORE", "TEN-CARD", 9, "locked"),
    ("ORD-1002", [("ITEM-NAAN-PK4", 2, 399), ("ITEM-YOG-WH", 1, 449)], "EMP-FATIMA", "DEV-REG2", "OT-INSTORE", "TEN-CASH", 7, "locked"),
    ("ORD-1003", [("ITEM-SAFF-1G", 1, 1299)], "EMP-AHMAD", "DEV-REG1", "OT-PHONE", "TEN-CARD", 5, "locked"),
    ("ORD-1004", [("ITEM-CHKP-CAN", 4, 199), ("ITEM-POM-MOL", 1, 799)], "EMP-FATIMA", "DEV-REG1", "OT-INSTORE", "TEN-CARD", 3, "locked"),
    ("ORD-1005", [("ITEM-BEEF-GR", 2, 699), ("ITEM-SPIN-BUN", 1, 299)], "EMP-AHMAD", "DEV-REG2", "OT-INSTORE", "TEN-CASH", 2, "locked"),
    ("ORD-1006", [("ITEM-WALN-1LB", 1, 899), ("ITEM-ROSE-16OZ", 1, 549)], "EMP-FATIMA", "DEV-REG1", "OT-INSTORE", "TEN-CARD", 0.5, "open"),
]

ORDERS = []
LINE_ITEMS = []
PAYMENTS = []
REFUNDS = []

for order_id, lines, emp_id, dev_id, ot_id, tender_id, age_days, state in _ORDER_ROWS:
    created = days_ago(age_days)
    modified = created
    total = sum(qty * price for _, qty, price in lines)

    ORDERS.append(
        {
            "id": order_id,
            "state": state,
            "total": total,
            "createdTime": created,
            "modifiedTime": modified,
            "employee": {"id": emp_id},
            "device": {"id": dev_id},
            "orderType": {"id": ot_id},
        }
    )

    for i, (item_id, qty, price) in enumerate(lines):
        item = next(it for it in ITEMS if it["id"] == item_id)
        LINE_ITEMS.append(
            {
                "id": f"{order_id}-LI{i}",
                "orderId": order_id,
                "name": item["name"],
                "price": price,
                "unitQty": qty * 1000,  # Clover unitQty is in thousandths
                "item": {"id": item_id},
            }
        )

    payment_id = f"{order_id}-PAY"
    PAYMENTS.append(
        {
            "id": payment_id,
            "order": {"id": order_id},
            "orderId": order_id,
            "amount": total,
            "tipAmount": 0,
            "taxAmount": 0,
            "result": "SUCCESS",
            "tender": {"id": tender_id},
            "createdTime": created,
            "modifiedTime": modified,
        }
    )

# One refund on the oldest order, for variety.
REFUNDS.append(
    {
        "id": "REF-1",
        "payment": {"id": "ORD-1001-PAY"},
        "amount": 899,
        "createdTime": days_ago(8),
        "modifiedTime": days_ago(8),
    }
)

MERCHANT_TEMPLATE = {
    "name": "Optiu Afghan Grocer (Sandbox)",
    "currency": "USD",
    "timeZone": "America/New_York",
}


def create_app(merchant_id: str, expected_token: str) -> Flask:
    app = Flask(__name__)

    @app.before_request
    def check_auth():
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[len("Bearer "):] != expected_token:
            return jsonify({"message": "Unauthorized"}), 401

    def _collection_field(value):
        return {"elements": value}

    def enrich_item(item: dict, expand: set[str]) -> dict:
        out = {k: v for k, v in item.items() if k != "categoryId"}
        if "categories" in expand:
            cat = next((c for c in CATEGORIES if c["id"] == item["categoryId"]), None)
            out["categories"] = _collection_field([cat] if cat else [])
        if "itemStock" in expand:
            stock = next((s for s in ITEM_STOCKS if s["itemId"] == item["id"]), None)
            out["itemStock"] = {"quantity": stock["quantity"]} if stock else {"quantity": 0}
        if "tags" in expand:
            out["tags"] = _collection_field([])
        if "modifierGroups" in expand:
            out["modifierGroups"] = _collection_field([])
        return out

    def enrich_order(order: dict, expand: set[str]) -> dict:
        out = dict(order)
        if "lineItems" in expand:
            lis = [li for li in LINE_ITEMS if li["orderId"] == order["id"]]
            out["lineItems"] = _collection_field([{k: v for k, v in li.items() if k != "orderId"} for li in lis])
        if "payments" in expand:
            pays = [p for p in PAYMENTS if p["orderId"] == order["id"]]
            out["payments"] = _collection_field([{k: v for k, v in p.items() if k != "orderId"} for p in pays])
        return out

    ENRICHERS = {"items": enrich_item, "orders": enrich_order}

    def apply_filters(rows: list[dict]) -> list[dict]:
        filters = request.args.getlist("filter")
        for f in filters:
            if ">=" not in f:
                continue
            field, _, raw_value = f.partition(">=")
            try:
                value = int(raw_value)
            except ValueError:
                continue
            rows = [r for r in rows if (r.get(field) or 0) >= value]
        return rows

    def list_endpoint(resource: str, rows: list[dict]):
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
        order_by = request.args.get("orderBy")
        expand = set((request.args.get("expand") or "").split(",")) - {""}

        filtered = apply_filters(rows)
        if order_by:
            filtered = sorted(filtered, key=lambda r: r.get(order_by) or 0)

        page = filtered[offset : offset + limit]
        enrich = ENRICHERS.get(resource)
        if enrich:
            page = [enrich(r, expand) for r in page]

        return jsonify({"elements": page, "href": request.url})

    @app.get(f"/v3/merchants/{merchant_id}")
    def merchant_root():
        return jsonify({"id": merchant_id, **MERCHANT_TEMPLATE})

    @app.get(f"/v3/merchants/{merchant_id}/categories")
    def categories():
        return list_endpoint("categories", CATEGORIES)

    @app.get(f"/v3/merchants/{merchant_id}/tags")
    def tags():
        return list_endpoint("tags", TAGS)

    @app.get(f"/v3/merchants/{merchant_id}/modifier_groups")
    def modifier_groups():
        return list_endpoint("modifier_groups", MODIFIER_GROUPS)

    @app.get(f"/v3/merchants/{merchant_id}/order_types")
    def order_types():
        return list_endpoint("order_types", ORDER_TYPES)

    @app.get(f"/v3/merchants/{merchant_id}/tenders")
    def tenders():
        return list_endpoint("tenders", TENDERS)

    @app.get(f"/v3/merchants/{merchant_id}/employees")
    def employees():
        return list_endpoint("employees", EMPLOYEES)

    @app.get(f"/v3/merchants/{merchant_id}/devices")
    def devices():
        return list_endpoint("devices", DEVICES)

    @app.get(f"/v3/merchants/{merchant_id}/items")
    def items():
        return list_endpoint("items", ITEMS)

    @app.get(f"/v3/merchants/{merchant_id}/item_stocks")
    def item_stocks():
        return list_endpoint("item_stocks", ITEM_STOCKS)

    @app.get(f"/v3/merchants/{merchant_id}/orders")
    def orders():
        return list_endpoint("orders", ORDERS)

    @app.get(f"/v3/merchants/{merchant_id}/payments")
    def payments():
        return list_endpoint("payments", [{k: v for k, v in p.items() if k != "orderId"} for p in PAYMENTS])

    @app.get(f"/v3/merchants/{merchant_id}/refunds")
    def refunds():
        return list_endpoint("refunds", REFUNDS)

    @app.get(f"/v3/merchants/{merchant_id}/customers")
    def customers():
        return list_endpoint("customers", CUSTOMERS)

    @app.get(f"/v3/merchants/{merchant_id}/cash_events")
    def cash_events():
        return list_endpoint("cash_events", CASH_EVENTS)

    @app.get(f"/v3/merchants/{merchant_id}/credits")
    def credits():
        return list_endpoint("credits", CREDITS)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--merchant-id", default="FAKEMID1")
    parser.add_argument("--token", default="fake-token")
    args = parser.parse_args()

    app = create_app(args.merchant_id, args.token)
    print(f"Fake Clover server for merchant '{args.merchant_id}' on http://{args.host}:{args.port}")
    print(f"Expecting Authorization: Bearer {args.token}")
    print("Point your .env at it with:")
    print(f"  CLOVER_API_TOKEN={args.token}")
    print(f"  CLOVER_MERCHANT_ID={args.merchant_id}")
    print(f"  CLOVER_BASE_URL=http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
