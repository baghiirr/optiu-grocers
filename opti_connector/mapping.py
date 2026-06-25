from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone


def _get_merchant(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM merchant LIMIT 1").fetchone()


def _active_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM items WHERE deleted = 0 AND hidden = 0").fetchall()


def _category_name(raw_json: str) -> str | None:
    try:
        item = json.loads(raw_json)
    except (TypeError, ValueError):
        return None
    categories = (item.get("categories") or {}).get("elements") or []
    return categories[0].get("name") if categories else None


def _ms_to_iso(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ms_to_date(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).date().isoformat()


def build_sku_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = []
    for item in _active_items(conn):
        rows.append(
            {
                "sku_id": item["id"],
                "sku_name": item["name"],
                "upc": item["code"],
                "brand": None,
                "category": _category_name(item["raw_json"]),
                "subcategory": None,
                "pack_size": None,
                "case_qty": None,
                "is_age_restricted": False,
                "is_lottery": False,
                "is_food": True,
            }
        )
    return rows


def build_supplier_rows(conn: sqlite3.Connection) -> list[dict]:
    return []


def build_vendor_term_rows(conn: sqlite3.Connection) -> list[dict]:
    return []


def build_inventory_snapshot_rows(conn: sqlite3.Connection) -> list[dict]:
    merchant = _get_merchant(conn)
    site_id = merchant["id"] if merchant else None
    snapshot_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    price_by_item: dict[str, float | None] = {}
    for item in _active_items(conn):
        price_by_item[item["id"]] = item["price"] / 100.0 if item["price"] is not None else None

    rows = []
    for stock in conn.execute("SELECT * FROM item_stocks").fetchall():
        if stock["item_id"] not in price_by_item:
            continue
        rows.append(
            {
                "site_id": site_id,
                "sku_id": stock["item_id"],
                "snapshot_at": snapshot_at,
                "on_hand_units": stock["quantity"],
                "shelf_price": price_by_item[stock["item_id"]],
            }
        )
    return rows


def build_invoice_rows(conn: sqlite3.Connection) -> list[dict]:
    merchant = _get_merchant(conn)
    site_id = merchant["id"] if merchant else None

    rows = []
    for order in conn.execute(
        "SELECT id, total, created_time FROM orders WHERE state = 'locked'"
    ).fetchall():
        rows.append(
            {
                "invoice_id": order["id"],
                "site_id": site_id,
                "vendor_id": None,
                "invoice_date": _ms_to_date(order["created_time"]),
                "total_amount": order["total"] / 100.0 if order["total"] is not None else None,
                "source_channel": "clover_pos",
                "confidence_score": 1.0,
                "received_at": _ms_to_iso(order["created_time"]),
            }
        )
    return rows


def build_invoice_line_rows(conn: sqlite3.Connection) -> list[dict]:
    query = """
        SELECT li.order_id, li.item_id, li.name, li.price, li.quantity
        FROM line_items li
        JOIN orders o ON o.id = li.order_id
        WHERE o.state = 'locked'
        ORDER BY li.order_id
    """
    line_nums: dict[str, int] = defaultdict(int)
    rows = []
    for row in conn.execute(query).fetchall():
        line_nums[row["order_id"]] += 1
        qty = (row["quantity"] or 0) / 1000.0
        price_per_unit = (row["price"] or 0) / 100.0
        rows.append(
            {
                "invoice_id": row["order_id"],
                "line_num": line_nums[row["order_id"]],
                "sku_id": row["item_id"],
                "description": row["name"],
                "units": qty,
                "case_cost": price_per_unit,
                "line_total": round(price_per_unit * qty, 4),
                "confidence_score": 1.0,
            }
        )
    return rows


def build_store_rows(conn: sqlite3.Connection) -> list[dict]:
    merchant = _get_merchant(conn)
    if merchant is None:
        return []
    return [
        {
            "site_id": merchant["id"],
            "site_name": merchant["name"],
            "street_address": None,
            "phone": None,
            "city": None,
            "state": None,
            "zip": None,
            "format": None,
            "pump_count": 0,
            "store_sqft": None,
            "has_carwash": False,
            "has_food_program": True,
            "has_lottery": False,
            "has_alcohol": False,
            "open_24h": False,
            "open_date": None,
            "is_active": True,
            "timezone": merchant["timezone"],
            "latitude": None,
            "longitude": None,
        }
    ]


def build_employee_rows(conn: sqlite3.Connection) -> list[dict]:
    merchant = _get_merchant(conn)
    site_id = merchant["id"] if merchant else None

    rows = []
    for emp in conn.execute("SELECT id, name, raw_json FROM employees").fetchall():
        try:
            raw = json.loads(emp["raw_json"])
        except (TypeError, ValueError):
            raw = {}
        role = raw.get("role")
        rows.append(
            {
                "employee_id": emp["id"],
                "full_name": emp["name"],
                "site_id": site_id,
                "role": role,
                "hourly_rate": None,
                "hired_on": None,
                "is_active": True,
            }
        )
    return rows


def build_labor_rule_rows() -> list[dict]:
    return []


def build_all_sheets(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    return {
        "SKUs": build_sku_rows(conn),
        "Suppliers": build_supplier_rows(conn),
        "Vendor terms": build_vendor_term_rows(conn),
        "Inventory snapshots": build_inventory_snapshot_rows(conn),
        "Invoices": build_invoice_rows(conn),
        "Invoice lines": build_invoice_line_rows(conn),
        "Stores": build_store_rows(conn),
        "Employees": build_employee_rows(conn),
        "Labor rules": build_labor_rule_rows(),
    }
