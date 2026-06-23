from __future__ import annotations

import responses

from clover_connector import db, sync
from clover_connector.config import base_url_for

from .conftest import load_fixture

BASE_URL = "https://apisandbox.dev.clover.com"
MID = "TESTMID"


def _url(path: str) -> str:
    return f"{BASE_URL}/v3/merchants/{MID}{path}"


def register_all_resources_empty():
    responses.add(responses.GET, _url(""), json=load_fixture("merchant_root.json"), status=200)
    empty = load_fixture("items_empty.json")
    for resource, spec in sync.RESOURCE_SPECS.items():
        responses.add(responses.GET, _url(spec.path), json=empty, status=200)


@responses.activate
def test_backfill_not_configured_short_circuits(unconfigured_config, conn):
    summary = sync.run_backfill(unconfigured_config, conn)
    assert summary.status == "not_configured"
    assert len(responses.calls) == 0


@responses.activate
def test_incremental_not_configured_short_circuits(unconfigured_config, conn):
    summary = sync.run_incremental(unconfigured_config, conn)
    assert summary.status == "not_configured"
    assert len(responses.calls) == 0


@responses.activate
def test_backfill_order_reference_before_items_before_orders(configured_config, conn):
    register_all_resources_empty()
    sync.run_backfill(configured_config, conn)

    called_paths = [call.request.url for call in responses.calls]
    idx_categories = next(i for i, u in enumerate(called_paths) if "/categories" in u)
    idx_items = next(i for i, u in enumerate(called_paths) if "/items" in u and "item_stocks" not in u)
    idx_orders = next(i for i, u in enumerate(called_paths) if "/orders" in u)
    assert idx_categories < idx_items < idx_orders


@responses.activate
def test_backfill_persists_each_resource(configured_config, conn):
    responses.add(responses.GET, _url(""), json=load_fixture("merchant_root.json"), status=200)
    empty = load_fixture("items_empty.json")
    for resource, spec in sync.RESOURCE_SPECS.items():
        if resource == "items":
            responses.add(responses.GET, _url(spec.path), json=load_fixture("items_page.json"), status=200)
        elif resource == "orders":
            responses.add(responses.GET, _url(spec.path), json=load_fixture("orders_page.json"), status=200)
        elif resource == "categories":
            responses.add(responses.GET, _url(spec.path), json=load_fixture("categories_page.json"), status=200)
        else:
            responses.add(responses.GET, _url(spec.path), json=empty, status=200)

    summary = sync.run_backfill(configured_config, conn)
    assert summary.status == "ok"

    assert conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) AS c FROM orders").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) AS c FROM categories").fetchone()["c"] == 2


@responses.activate
def test_backfill_sets_high_water_mark_per_resource(configured_config, conn):
    register_all_resources_empty()
    sync.run_backfill(configured_config, conn)
    rows = {r["resource"] for r in conn.execute("SELECT resource FROM sync_state").fetchall()}
    assert "items" in rows
    assert "orders" in rows
    assert "merchant" in rows


@responses.activate
def test_backfill_one_resource_403_does_not_abort_others(configured_config, conn):
    responses.add(responses.GET, _url(""), json=load_fixture("merchant_root.json"), status=200)
    empty = load_fixture("items_empty.json")
    for resource, spec in sync.RESOURCE_SPECS.items():
        if resource == "customers":
            responses.add(responses.GET, _url(spec.path), status=403, body="forbidden")
        elif resource == "items":
            responses.add(responses.GET, _url(spec.path), json=load_fixture("items_page.json"), status=200)
        else:
            responses.add(responses.GET, _url(spec.path), json=empty, status=200)

    summary = sync.run_backfill(configured_config, conn)
    assert summary.status == "partial"

    customer_results = [r for r in summary.results if r.resource == "customers"]
    assert customer_results[0].status == "error"

    assert conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"] == 1


@responses.activate
def test_incremental_uses_stored_high_water_mark(configured_config, conn):
    db.set_high_water_mark(conn, "items", 1718050000000, 1, status="ok")
    responses.add(responses.GET, _url("/items"), json=load_fixture("items_empty.json"), status=200)

    sync.run_incremental(configured_config, conn, resources=["items"])

    sent_url = responses.calls[0].request.url
    expected_since = 1718050000000 - sync.SAFETY_OVERLAP_MS
    assert f"modifiedTime%3E%3D{expected_since}" in sent_url


@responses.activate
def test_incremental_applies_safety_overlap(configured_config, conn):
    db.set_high_water_mark(conn, "items", 100_000, 1, status="ok")
    responses.add(responses.GET, _url("/items"), json=load_fixture("items_empty.json"), status=200)

    sync.run_incremental(configured_config, conn, resources=["items"])

    sent_url = responses.calls[0].request.url
    expected_since = max(0, 100_000 - sync.SAFETY_OVERLAP_MS)
    assert f"modifiedTime%3E%3D{expected_since}" in sent_url


@responses.activate
def test_incremental_high_water_mark_advances_to_max_seen(configured_config, conn):
    db.set_high_water_mark(conn, "items", 1, 1, status="ok")
    page = {
        "elements": [
            {"id": "A", "modifiedTime": 500},
            {"id": "B", "modifiedTime": 2000},
            {"id": "C", "modifiedTime": 1000},
        ],
        "href": "x",
    }
    responses.add(responses.GET, _url("/items"), json=page, status=200)

    sync.run_incremental(configured_config, conn, resources=["items"])

    assert db.get_high_water_mark(conn, "items") == 2000


@responses.activate
def test_incremental_skips_resource_with_no_prior_backfill(configured_config, conn):
    summary = sync.run_incremental(configured_config, conn, resources=["items"])
    assert len(responses.calls) == 0
    assert summary.results[0].status == "skipped"


@responses.activate
def test_incremental_resource_filter_param_selects_subset(configured_config, conn):
    db.set_high_water_mark(conn, "items", 1718050000000, 1, status="ok")
    responses.add(responses.GET, _url("/items"), json=load_fixture("items_empty.json"), status=200)

    summary = sync.run_incremental(configured_config, conn, resources=["items"])

    assert len(responses.calls) == 1
    assert [r.resource for r in summary.results] == ["items"]
