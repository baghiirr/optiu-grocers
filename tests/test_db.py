from __future__ import annotations

from clover_connector import db

from .conftest import load_fixture


def test_init_db_creates_all_tables(conn):
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    expected = {
        "merchant", "categories", "tags", "modifier_groups", "employees", "devices",
        "order_types", "tenders", "customers", "cash_events", "items", "item_stocks",
        "orders", "line_items", "payments", "refunds", "credits", "sync_state",
    }
    assert expected <= tables


def test_init_db_idempotent(conn):
    db.init_db(conn)
    db.init_db(conn)


def test_upsert_items_insert_then_update_dedupes_on_id(conn):
    db.upsert_items(conn, [{"id": "I1", "name": "Item", "price": 100}], synced_at=1)
    db.upsert_items(conn, [{"id": "I1", "name": "Item", "price": 200}], synced_at=2)
    rows = conn.execute("SELECT * FROM items WHERE id = 'I1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["price"] == 200


def test_upsert_items_from_manual_sample(conn):
    sample = load_fixture("items_page.json")["elements"][0]
    db.upsert_items(conn, [sample], synced_at=123)
    row = conn.execute("SELECT * FROM items WHERE id = ?", (sample["id"],)).fetchone()
    assert row["price"] == 499
    assert row["cost"] == 312
    assert row["sku"] == "MILK-WH-1G"
    assert row["name"] == "Organic Whole Milk 1gal"
    import json

    raw = json.loads(row["raw_json"])
    assert raw["sku"] == "MILK-WH-1G"

    stock_row = conn.execute("SELECT * FROM item_stocks WHERE item_id = ?", (sample["id"],)).fetchone()
    assert stock_row["quantity"] == 24


def test_upsert_orders_explodes_line_items_and_payments(conn):
    order = load_fixture("orders_page.json")["elements"][0]
    db.upsert_orders(conn, [order], synced_at=123)

    order_row = conn.execute("SELECT * FROM orders WHERE id = 'ORD1'").fetchone()
    assert order_row["total"] == 1299

    li_rows = conn.execute("SELECT * FROM line_items WHERE order_id = 'ORD1'").fetchall()
    assert len(li_rows) == 1
    assert li_rows[0]["item_id"] == "0X1Y2Z"

    pay_rows = conn.execute("SELECT * FROM payments WHERE order_id = 'ORD1'").fetchall()
    assert len(pay_rows) == 1
    assert pay_rows[0]["amount"] == 1299


def test_high_water_mark_defaults_to_zero(conn):
    assert db.get_high_water_mark(conn, "items") == 0


def test_high_water_mark_set_and_get_roundtrip(conn):
    db.set_high_water_mark(conn, "items", 1718050000000, 10, status="ok")
    assert db.get_high_water_mark(conn, "items") == 1718050000000


def test_item_stocks_keyed_by_item_id(conn):
    db.upsert_item_stocks(conn, [{"item": {"id": "I1"}, "quantity": 5}], synced_at=1)
    db.upsert_item_stocks(conn, [{"item": {"id": "I1"}, "quantity": 9}], synced_at=2)
    rows = conn.execute("SELECT * FROM item_stocks WHERE item_id = 'I1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["quantity"] == 9
