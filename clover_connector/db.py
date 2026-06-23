from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS merchant (
    id TEXT PRIMARY KEY,
    name TEXT,
    currency TEXT,
    timezone TEXT,
    raw_json TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    name TEXT,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS modifier_groups (
    id TEXT PRIMARY KEY,
    name TEXT,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    id TEXT PRIMARY KEY,
    name TEXT,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS order_types (
    id TEXT PRIMARY KEY,
    label TEXT,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tenders (
    id TEXT PRIMARY KEY,
    label TEXT,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    raw_json TEXT NOT NULL,
    modified_time INTEGER,
    synced_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_events (
    id TEXT PRIMARY KEY,
    event_type TEXT,
    created_time INTEGER,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    name TEXT,
    price INTEGER,
    price_type TEXT,
    cost INTEGER,
    sku TEXT,
    code TEXT,
    hidden INTEGER,
    deleted INTEGER DEFAULT 0,
    modified_time INTEGER,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_modified_time ON items(modified_time);

CREATE TABLE IF NOT EXISTS item_stocks (
    item_id TEXT PRIMARY KEY,
    quantity INTEGER,
    modified_time INTEGER,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    state TEXT,
    total INTEGER,
    created_time INTEGER,
    modified_time INTEGER,
    employee_id TEXT,
    device_id TEXT,
    order_type_id TEXT,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_modified_time ON orders(modified_time);

CREATE TABLE IF NOT EXISTS line_items (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    item_id TEXT,
    name TEXT,
    price INTEGER,
    quantity INTEGER,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_line_items_order_id ON line_items(order_id);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    order_id TEXT,
    amount INTEGER,
    tip_amount INTEGER,
    tax_amount INTEGER,
    tender_id TEXT,
    result TEXT,
    created_time INTEGER,
    modified_time INTEGER,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_created_time ON payments(created_time);
CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id);

CREATE TABLE IF NOT EXISTS refunds (
    id TEXT PRIMARY KEY,
    payment_id TEXT,
    amount INTEGER,
    created_time INTEGER,
    modified_time INTEGER,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refunds_created_time ON refunds(created_time);

CREATE TABLE IF NOT EXISTS credits (
    id TEXT PRIMARY KEY,
    amount INTEGER,
    created_time INTEGER,
    raw_json TEXT NOT NULL,
    synced_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    resource TEXT PRIMARY KEY,
    last_modified_millis INTEGER NOT NULL DEFAULT 0,
    last_run_at INTEGER,
    last_status TEXT,
    last_row_count INTEGER,
    last_error TEXT
);

-- Opti (OptiGrocer) decision exchange — shares this db with the Clover sync
-- tables above so target_id values can reference Clover item ids directly.
CREATE TABLE IF NOT EXISTS opti_decisions (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT,
    site_id TEXT,
    decision_type TEXT,
    target_id TEXT,
    recommended_value TEXT NOT NULL,
    confidence REAL,
    created_at TEXT,
    why_summary TEXT,
    raw_json TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opti_decisions_site ON opti_decisions(site_id);
CREATE INDEX IF NOT EXISTS idx_opti_decisions_type ON opti_decisions(decision_type);

CREATE TABLE IF NOT EXISTS opti_decision_status (
    decision_id TEXT PRIMARY KEY REFERENCES opti_decisions(decision_id),
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at INTEGER,
    notes TEXT
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    with conn:
        conn.executescript(SCHEMA)


def _collection(obj: dict, key: str) -> list[dict]:
    value = obj.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return value.get("elements", [])
    return []


def _upsert(
    conn: sqlite3.Connection,
    table: str,
    columns: list[str],
    rows: Iterable[tuple],
) -> int:
    rows = list(rows)
    if not rows:
        return 0
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    update_clause = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "id" and c != "item_id")
    key_col = "item_id" if "item_id" in columns and "id" not in columns else "id"
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT({key_col}) DO UPDATE SET {update_clause}"
    )
    with conn:
        conn.executemany(sql, rows)
    return len(rows)


def upsert_merchant(conn: sqlite3.Connection, merchant: dict, fetched_at: int) -> None:
    columns = ["id", "name", "currency", "timezone", "raw_json", "fetched_at"]
    row = (
        merchant["id"],
        merchant.get("name"),
        merchant.get("currency"),
        merchant.get("timeZone") or merchant.get("timezone"),
        json.dumps(merchant),
        fetched_at,
    )
    _upsert(conn, "merchant", columns, [row])


def upsert_categories(conn: sqlite3.Connection, categories: list[dict], synced_at: int) -> int:
    columns = ["id", "name", "raw_json", "synced_at"]
    rows = ((c["id"], c.get("name"), json.dumps(c), synced_at) for c in categories)
    return _upsert(conn, "categories", columns, rows)


def upsert_tags(conn: sqlite3.Connection, tags: list[dict], synced_at: int) -> int:
    columns = ["id", "name", "raw_json", "synced_at"]
    rows = ((t["id"], t.get("name"), json.dumps(t), synced_at) for t in tags)
    return _upsert(conn, "tags", columns, rows)


def upsert_modifier_groups(conn: sqlite3.Connection, groups: list[dict], synced_at: int) -> int:
    columns = ["id", "name", "raw_json", "synced_at"]
    rows = ((g["id"], g.get("name"), json.dumps(g), synced_at) for g in groups)
    return _upsert(conn, "modifier_groups", columns, rows)


def upsert_employees(conn: sqlite3.Connection, employees: list[dict], synced_at: int) -> int:
    columns = ["id", "name", "raw_json", "synced_at"]
    rows = ((e["id"], e.get("name"), json.dumps(e), synced_at) for e in employees)
    return _upsert(conn, "employees", columns, rows)


def upsert_devices(conn: sqlite3.Connection, devices: list[dict], synced_at: int) -> int:
    columns = ["id", "name", "raw_json", "synced_at"]
    rows = ((d["id"], d.get("name"), json.dumps(d), synced_at) for d in devices)
    return _upsert(conn, "devices", columns, rows)


def upsert_order_types(conn: sqlite3.Connection, order_types: list[dict], synced_at: int) -> int:
    columns = ["id", "label", "raw_json", "synced_at"]
    rows = ((o["id"], o.get("label"), json.dumps(o), synced_at) for o in order_types)
    return _upsert(conn, "order_types", columns, rows)


def upsert_tenders(conn: sqlite3.Connection, tenders: list[dict], synced_at: int) -> int:
    columns = ["id", "label", "raw_json", "synced_at"]
    rows = ((t["id"], t.get("label"), json.dumps(t), synced_at) for t in tenders)
    return _upsert(conn, "tenders", columns, rows)


def upsert_customers(conn: sqlite3.Connection, customers: list[dict], synced_at: int) -> int:
    columns = ["id", "raw_json", "modified_time", "synced_at"]
    rows = (
        (c["id"], json.dumps(c), c.get("modifiedTime"), synced_at) for c in customers
    )
    return _upsert(conn, "customers", columns, rows)


def upsert_cash_events(conn: sqlite3.Connection, events: list[dict], synced_at: int) -> int:
    columns = ["id", "event_type", "created_time", "raw_json", "synced_at"]
    rows = (
        (e["id"], e.get("type"), e.get("createdTime"), json.dumps(e), synced_at)
        for e in events
    )
    return _upsert(conn, "cash_events", columns, rows)


def upsert_credits(conn: sqlite3.Connection, credits_: list[dict], synced_at: int) -> int:
    columns = ["id", "amount", "created_time", "raw_json", "synced_at"]
    rows = (
        (c["id"], c.get("amount"), c.get("createdTime"), json.dumps(c), synced_at)
        for c in credits_
    )
    return _upsert(conn, "credits", columns, rows)


def upsert_items(conn: sqlite3.Connection, items: list[dict], synced_at: int) -> int:
    columns = [
        "id", "name", "price", "price_type", "cost", "sku", "code",
        "hidden", "deleted", "modified_time", "raw_json", "synced_at",
    ]
    rows = (
        (
            i["id"],
            i.get("name"),
            i.get("price"),
            i.get("priceType"),
            i.get("cost"),
            i.get("sku"),
            i.get("code"),
            int(bool(i.get("hidden"))),
            int(bool(i.get("deleted"))),
            i.get("modifiedTime"),
            json.dumps(i),
            synced_at,
        )
        for i in items
    )
    rows = list(rows)
    inserted = _upsert(conn, "items", columns, rows)

    # Items returned with expand=itemStock carry stock inline; persist it too.
    stocks = []
    for i in items:
        stock = i.get("itemStock")
        if stock:
            stocks.append({"item": {"id": i["id"]}, **stock})
    if stocks:
        upsert_item_stocks(conn, stocks, synced_at)

    return inserted


def upsert_item_stocks(conn: sqlite3.Connection, stocks: list[dict], synced_at: int) -> int:
    columns = ["item_id", "quantity", "modified_time", "raw_json", "synced_at"]
    rows = (
        (
            s.get("item", {}).get("id") or s.get("itemId"),
            s.get("quantity"),
            s.get("modifiedTime"),
            json.dumps(s),
            synced_at,
        )
        for s in stocks
    )
    return _upsert(conn, "item_stocks", columns, rows)


def upsert_orders(conn: sqlite3.Connection, orders: list[dict], synced_at: int) -> int:
    columns = [
        "id", "state", "total", "created_time", "modified_time",
        "employee_id", "device_id", "order_type_id", "raw_json", "synced_at",
    ]
    rows = []
    line_items: list[dict] = []
    payments: list[dict] = []
    for o in orders:
        rows.append(
            (
                o["id"],
                o.get("state"),
                o.get("total"),
                o.get("createdTime"),
                o.get("modifiedTime"),
                (o.get("employee") or {}).get("id"),
                (o.get("device") or {}).get("id"),
                (o.get("orderType") or {}).get("id"),
                json.dumps(o),
                synced_at,
            )
        )
        for li in _collection(o, "lineItems"):
            li_with_order = {**li, "_orderId": o["id"]}
            line_items.append(li_with_order)
        for p in _collection(o, "payments"):
            p_with_order = {**p, "_orderId": o["id"]}
            payments.append(p_with_order)

    inserted = _upsert(conn, "orders", columns, rows)
    if line_items:
        upsert_line_items(conn, line_items, synced_at)
    if payments:
        upsert_payments(conn, payments, synced_at)
    return inserted


def upsert_line_items(conn: sqlite3.Connection, line_items: list[dict], synced_at: int) -> int:
    columns = ["id", "order_id", "item_id", "name", "price", "quantity", "raw_json", "synced_at"]
    rows = (
        (
            li["id"],
            li.get("_orderId") or (li.get("order") or {}).get("id"),
            (li.get("item") or {}).get("id"),
            li.get("name"),
            li.get("price"),
            li.get("unitQty") or li.get("quantity"),
            json.dumps({k: v for k, v in li.items() if k != "_orderId"}),
            synced_at,
        )
        for li in line_items
    )
    return _upsert(conn, "line_items", columns, rows)


def upsert_payments(conn: sqlite3.Connection, payments: list[dict], synced_at: int) -> int:
    columns = [
        "id", "order_id", "amount", "tip_amount", "tax_amount",
        "tender_id", "result", "created_time", "modified_time", "raw_json", "synced_at",
    ]
    rows = (
        (
            p["id"],
            p.get("_orderId") or (p.get("order") or {}).get("id"),
            p.get("amount"),
            p.get("tipAmount"),
            p.get("taxAmount"),
            (p.get("tender") or {}).get("id"),
            p.get("result"),
            p.get("createdTime"),
            p.get("modifiedTime"),
            json.dumps({k: v for k, v in p.items() if k != "_orderId"}),
            synced_at,
        )
        for p in payments
    )
    return _upsert(conn, "payments", columns, rows)


def upsert_refunds(conn: sqlite3.Connection, refunds: list[dict], synced_at: int) -> int:
    columns = [
        "id", "payment_id", "amount", "created_time", "modified_time", "raw_json", "synced_at",
    ]
    rows = (
        (
            r["id"],
            (r.get("payment") or {}).get("id"),
            r.get("amount"),
            r.get("createdTime"),
            r.get("modifiedTime"),
            json.dumps(r),
            synced_at,
        )
        for r in refunds
    )
    return _upsert(conn, "refunds", columns, rows)


def get_high_water_mark(conn: sqlite3.Connection, resource: str) -> int:
    row = conn.execute(
        "SELECT last_modified_millis FROM sync_state WHERE resource = ?", (resource,)
    ).fetchone()
    return row["last_modified_millis"] if row else 0


def set_high_water_mark(
    conn: sqlite3.Connection,
    resource: str,
    millis: int,
    row_count: int,
    status: str = "ok",
    error: str | None = None,
    run_at: int | None = None,
) -> None:
    import time as _time

    run_at = run_at if run_at is not None else int(_time.time() * 1000)
    with conn:
        conn.execute(
            """
            INSERT INTO sync_state (resource, last_modified_millis, last_run_at, last_status, last_row_count, last_error)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(resource) DO UPDATE SET
                last_modified_millis = excluded.last_modified_millis,
                last_run_at = excluded.last_run_at,
                last_status = excluded.last_status,
                last_row_count = excluded.last_row_count,
                last_error = excluded.last_error
            """,
            (resource, millis, run_at, status, row_count, error),
        )
