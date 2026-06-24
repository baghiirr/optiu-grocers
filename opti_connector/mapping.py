from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone

DEFAULT_POLICY_VALUES: dict[str, str] = {
    "min_margin_pct": "0.18",
    "target_service_level": "0.95",
    "spoil_risk_days": "4",
    "markdown_trigger_days": "4",
    "max_markdown_depth_pct": "0.50",
    "max_days_of_supply": "7.0",
    "perishable_overbuy_guard": "true",
    "allow_donation": "true",
    "min_redistribute_units": "4",
}


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


def build_store_rows(conn: sqlite3.Connection) -> list[dict]:
    merchant = _get_merchant(conn)
    if merchant is None:
        return []
    return [
        {
            "store_id": merchant["id"],
            "store_name": merchant["name"],
            "format": None,
            "region": None,
            "selling_area_sqft": None,
            "weekly_footfall": None,
            "currency": merchant["currency"] or "USD",
        }
    ]


def build_product_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = []
    for item in _active_items(conn):
        rows.append(
            {
                "product_id": item["id"],
                "product_name": item["name"],
                "department": _category_name(item["raw_json"]),
                "subcategory": None,
                "is_perishable": None,
                "shelf_life_days": None,
                "temp_zone": None,
                "unit_cost": item["cost"] / 100.0 if item["cost"] is not None else None,
                "list_price": item["price"] / 100.0 if item["price"] is not None else None,
                "unit_of_measure": "ea",
                "default_vendor_id": None,
            }
        )
    return rows


def build_inventory_rows(conn: sqlite3.Connection) -> list[dict]:
    merchant = _get_merchant(conn)
    store_id = merchant["id"] if merchant else None
    active_ids = {item["id"] for item in _active_items(conn)}
    today = date.today().isoformat()

    rows = []
    for stock in conn.execute("SELECT * FROM item_stocks").fetchall():
        if stock["item_id"] not in active_ids:
            continue
        rows.append(
            {
                "product_id": stock["item_id"],
                "store_id": store_id,
                "on_hand_units": stock["quantity"],
                "on_order_units": None,
                "received_date": None,
                "age_days": None,
                "days_of_supply": None,
                "as_of_date": today,
            }
        )
    return rows


def build_price_rows(conn: sqlite3.Connection) -> list[dict]:
    merchant = _get_merchant(conn)
    store_id = merchant["id"] if merchant else None
    currency = (merchant["currency"] if merchant else None) or "USD"

    rows = []
    for item in _active_items(conn):
        rows.append(
            {
                "product_id": item["id"],
                "store_id": store_id,
                "current_price": item["price"] / 100.0 if item["price"] is not None else None,
                "floor_price": item["cost"] / 100.0 if item["cost"] is not None else None,
                "ceiling_price": None,
                "currency": currency,
            }
        )
    return rows


def build_sales_history_rows(conn: sqlite3.Connection) -> list[dict]:
    merchant = _get_merchant(conn)
    store_id = merchant["id"] if merchant else None

    query = """
        SELECT li.item_id AS item_id, li.price AS price, li.quantity AS quantity, o.created_time AS created_time
        FROM line_items li
        JOIN orders o ON o.id = li.order_id
        WHERE o.state = 'locked' AND li.item_id IS NOT NULL
    """
    groups: dict[tuple[str, str], dict] = defaultdict(lambda: {"units": 0.0, "revenue": 0.0})
    for row in conn.execute(query).fetchall():
        sale_date = (
            datetime.fromtimestamp(row["created_time"] / 1000, tz=timezone.utc).date().isoformat()
        )
        key = (row["item_id"], sale_date)
        units = (row["quantity"] or 0) / 1000.0
        groups[key]["units"] += units
        groups[key]["revenue"] += (row["price"] or 0) * units / 100.0

    rows = []
    for (item_id, sale_date), agg in groups.items():
        units_sold = agg["units"]
        revenue = agg["revenue"]
        rows.append(
            {
                "product_id": item_id,
                "store_id": store_id,
                "sale_date": sale_date,
                "units_sold": units_sold,
                "revenue": revenue,
                "avg_price": (revenue / units_sold) if units_sold > 0 else None,
                "on_markdown": False,
            }
        )
    return rows


def build_policy_rows() -> list[dict]:
    return [
        {
            "policy_key": key,
            "policy_value": value,
            "scope": "global",
            "department": None,
            "description": f"v1 engineering default for {key} — confirm with the business.",
        }
        for key, value in DEFAULT_POLICY_VALUES.items()
    ]


def build_vendor_rows(conn: sqlite3.Connection) -> list[dict]:
    """INP_VENDOR — Clover has no native vendor/PO model (spec §5.1). Returns
    [] in v1. Pending: supervisor to provide vendor master data before this
    can be implemented."""
    return []


def build_vendor_product_rows(conn: sqlite3.Connection) -> list[dict]:
    """INP_VENDOR_PRODUCT — same gap as build_vendor_rows. Returns []."""
    return []


def build_shrink_history_rows(conn: sqlite3.Connection) -> list[dict]:
    """INP_SHRINK_HISTORY — clover_connector doesn't currently sync Clover's
    inventory-adjustment resource. Returns []. Pending: either add that sync
    or a shrink log from the business."""
    return []


def build_all_sheets(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    return {
        "INP_STORE": build_store_rows(conn),
        "INP_PRODUCT": build_product_rows(conn),
        "INP_VENDOR": build_vendor_rows(conn),
        "INP_VENDOR_PRODUCT": build_vendor_product_rows(conn),
        "INP_INVENTORY": build_inventory_rows(conn),
        "INP_PRICE": build_price_rows(conn),
        "INP_SALES_HISTORY": build_sales_history_rows(conn),
        "INP_SHRINK_HISTORY": build_shrink_history_rows(conn),
        "INP_POLICY": build_policy_rows(),
        "INP_DEMAND_FORECAST": [],
        "INP_ELASTICITY": [],
    }
