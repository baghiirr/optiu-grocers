from __future__ import annotations

from clover_connector import db as clover_db
from opti_connector import mapping

from .conftest import load_fixture


def test_build_store_rows_from_merchant(conn):
    clover_db.upsert_merchant(conn, load_fixture("merchant_root.json"), fetched_at=1)
    rows = mapping.build_store_rows(conn)
    assert rows == [
        {
            "store_id": "TESTMID",
            "store_name": "Optiu Afghan Grocer",
            "format": None,
            "region": None,
            "selling_area_sqft": None,
            "weekly_footfall": None,
            "currency": "USD",
        }
    ]


def test_build_store_rows_empty_when_no_merchant(conn):
    assert mapping.build_store_rows(conn) == []


def test_build_product_rows_converts_cents_and_parses_category(conn):
    sample = load_fixture("items_page.json")["elements"][0]
    clover_db.upsert_items(conn, [sample], synced_at=1)

    rows = mapping.build_product_rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["product_id"] == "0X1Y2Z"
    assert row["product_name"] == "Organic Whole Milk 1gal"
    assert row["list_price"] == 4.99
    assert row["unit_cost"] == 3.12
    assert row["department"] == "Dairy"
    assert row["is_perishable"] is None
    assert row["shelf_life_days"] is None
    assert row["unit_of_measure"] == "ea"


def test_build_product_rows_skips_deleted_and_hidden(conn):
    clover_db.upsert_items(
        conn,
        [
            {"id": "I1", "name": "Visible", "price": 100},
            {"id": "I2", "name": "Hidden", "price": 200, "hidden": True},
            {"id": "I3", "name": "Deleted", "price": 300, "deleted": True},
        ],
        synced_at=1,
    )
    rows = mapping.build_product_rows(conn)
    assert [r["product_id"] for r in rows] == ["I1"]


def test_build_inventory_rows_carries_quantity_and_leaves_gaps_blank(conn):
    sample = load_fixture("items_page.json")["elements"][0]
    clover_db.upsert_merchant(conn, load_fixture("merchant_root.json"), fetched_at=1)
    clover_db.upsert_items(conn, [sample], synced_at=1)

    rows = mapping.build_inventory_rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["product_id"] == "0X1Y2Z"
    assert row["store_id"] == "TESTMID"
    assert row["on_hand_units"] == 24
    assert row["age_days"] is None
    assert row["received_date"] is None
    assert row["as_of_date"] is not None


def test_build_inventory_rows_skips_hidden_item_stock(conn):
    clover_db.upsert_items(conn, [{"id": "I1", "name": "Hidden", "hidden": True}], synced_at=1)
    clover_db.upsert_item_stocks(conn, [{"item": {"id": "I1"}, "quantity": 5}], synced_at=1)
    assert mapping.build_inventory_rows(conn) == []


def test_build_price_rows_uses_cost_as_floor(conn):
    sample = load_fixture("items_page.json")["elements"][0]
    clover_db.upsert_merchant(conn, load_fixture("merchant_root.json"), fetched_at=1)
    clover_db.upsert_items(conn, [sample], synced_at=1)

    rows = mapping.build_price_rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["current_price"] == 4.99
    assert row["floor_price"] == 3.12
    assert row["ceiling_price"] is None
    assert row["currency"] == "USD"


def test_build_sales_history_excludes_open_orders_and_aggregates(conn):
    clover_db.upsert_merchant(conn, load_fixture("merchant_root.json"), fetched_at=1)
    locked_order = {
        "id": "ORD-L1",
        "state": "locked",
        "total": 1000,
        "createdTime": 1718040000000,  # 2024-06-10T...
        "lineItems": {"elements": [{"id": "LI1", "price": 500, "unitQty": 2000, "item": {"id": "ITEM-A"}}]},
    }
    open_order = {
        "id": "ORD-O1",
        "state": "open",
        "total": 999,
        "createdTime": 1718040000000,
        "lineItems": {"elements": [{"id": "LI2", "price": 999, "unitQty": 1000, "item": {"id": "ITEM-A"}}]},
    }
    clover_db.upsert_orders(conn, [locked_order, open_order], synced_at=1)

    rows = mapping.build_sales_history_rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["product_id"] == "ITEM-A"
    assert row["store_id"] == "TESTMID"
    assert row["units_sold"] == 2.0
    assert row["revenue"] == 10.0
    assert row["avg_price"] == 5.0
    assert row["on_markdown"] is False


def test_build_sales_history_groups_by_day_and_item(conn):
    clover_db.upsert_merchant(conn, load_fixture("merchant_root.json"), fetched_at=1)
    same_day_order_1 = {
        "id": "ORD-1",
        "state": "locked",
        "total": 500,
        "createdTime": 1718040000000,
        "lineItems": {"elements": [{"id": "LI1", "price": 500, "unitQty": 1000, "item": {"id": "ITEM-A"}}]},
    }
    same_day_order_2 = {
        "id": "ORD-2",
        "state": "locked",
        "total": 500,
        "createdTime": 1718041000000,  # same UTC day
        "lineItems": {"elements": [{"id": "LI2", "price": 500, "unitQty": 1000, "item": {"id": "ITEM-A"}}]},
    }
    clover_db.upsert_orders(conn, [same_day_order_1, same_day_order_2], synced_at=1)

    rows = mapping.build_sales_history_rows(conn)
    assert len(rows) == 1
    assert rows[0]["units_sold"] == 2.0
    assert rows[0]["revenue"] == 10.0


def test_build_policy_rows_returns_known_keys():
    rows = mapping.build_policy_rows()
    keys = {r["policy_key"] for r in rows}
    assert keys == set(mapping.DEFAULT_POLICY_VALUES)
    for row in rows:
        assert row["policy_value"] == mapping.DEFAULT_POLICY_VALUES[row["policy_key"]]
        assert row["scope"] == "global"


def test_vendor_and_shrink_builders_return_empty(conn):
    assert mapping.build_vendor_rows(conn) == []
    assert mapping.build_vendor_product_rows(conn) == []
    assert mapping.build_shrink_history_rows(conn) == []


def test_build_all_sheets_has_all_nine_keys(conn):
    sheets = mapping.build_all_sheets(conn)
    assert set(sheets) == {
        "INP_STORE", "INP_PRODUCT", "INP_VENDOR", "INP_VENDOR_PRODUCT",
        "INP_INVENTORY", "INP_PRICE", "INP_SALES_HISTORY", "INP_SHRINK_HISTORY", "INP_POLICY",
    }
