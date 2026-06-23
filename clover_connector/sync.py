from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from . import db
from .client import CloverAPIError, CloverClient
from .config import CloverConfig, base_url_for

NOT_CONFIGURED_MESSAGE = (
    "Clover not connected — no data available. "
    "Set CLOVER_API_TOKEN and CLOVER_MERCHANT_ID (e.g. in a .env file) to enable syncing."
)

SAFETY_OVERLAP_MS = 60_000

REFERENCE_RESOURCES = [
    "categories", "tags", "modifier_groups", "order_types", "tenders", "employees", "devices",
]
INVENTORY_RESOURCES = ["items", "item_stocks"]
SALES_RESOURCES = ["orders", "payments", "refunds"]
LOWER_PRIORITY_RESOURCES = ["customers", "cash_events", "credits"]

ALL_BACKFILL_RESOURCES = (
    REFERENCE_RESOURCES + INVENTORY_RESOURCES + SALES_RESOURCES + LOWER_PRIORITY_RESOURCES
)
DEFAULT_INCREMENTAL_RESOURCES = REFERENCE_RESOURCES + INVENTORY_RESOURCES + SALES_RESOURCES


@dataclass(frozen=True)
class ResourceSpec:
    path: str
    expand: list[str] | None
    ts_field: str | None  # None => not time-filterable; pulled in full each backfill
    upsert: Callable[[sqlite3.Connection, list[dict], int], int]


RESOURCE_SPECS: dict[str, ResourceSpec] = {
    "categories": ResourceSpec("/categories", None, None, db.upsert_categories),
    "tags": ResourceSpec("/tags", None, None, db.upsert_tags),
    "modifier_groups": ResourceSpec("/modifier_groups", ["modifiers"], None, db.upsert_modifier_groups),
    "order_types": ResourceSpec("/order_types", None, None, db.upsert_order_types),
    "tenders": ResourceSpec("/tenders", None, None, db.upsert_tenders),
    "employees": ResourceSpec("/employees", None, None, db.upsert_employees),
    "devices": ResourceSpec("/devices", None, None, db.upsert_devices),
    "items": ResourceSpec(
        "/items", ["categories", "itemStock", "tags", "modifierGroups"], "modifiedTime", db.upsert_items
    ),
    "item_stocks": ResourceSpec("/item_stocks", None, "modifiedTime", db.upsert_item_stocks),
    "orders": ResourceSpec("/orders", ["lineItems", "payments"], "modifiedTime", db.upsert_orders),
    "payments": ResourceSpec("/payments", None, "createdTime", db.upsert_payments),
    "refunds": ResourceSpec("/refunds", None, "createdTime", db.upsert_refunds),
    "customers": ResourceSpec("/customers", None, "modifiedTime", db.upsert_customers),
    "cash_events": ResourceSpec("/cash_events", None, "createdTime", db.upsert_cash_events),
    "credits": ResourceSpec("/credits", None, "createdTime", db.upsert_credits),
}


@dataclass
class SyncResult:
    status: Literal["ok", "not_configured", "error", "skipped"]
    resource: str | None = None
    rows_synced: int = 0
    message: str = ""


@dataclass
class RunSummary:
    status: Literal["ok", "not_configured", "partial", "error"]
    results: list[SyncResult] = field(default_factory=list)
    message: str = ""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _max_ts(page: list[dict], ts_field: str, current_max: int) -> int:
    for row in page:
        value = row.get(ts_field)
        if isinstance(value, (int, float)) and int(value) > current_max:
            current_max = int(value)
    return current_max


def _sync_merchant(client: CloverClient, conn: sqlite3.Connection, synced_at: int) -> SyncResult:
    try:
        merchant = client.get_object("")
        db.upsert_merchant(conn, merchant, synced_at)
        db.set_high_water_mark(conn, "merchant", synced_at, 1, status="ok")
        return SyncResult(status="ok", resource="merchant", rows_synced=1)
    except CloverAPIError as exc:
        db.set_high_water_mark(conn, "merchant", 0, 0, status="error", error=str(exc))
        return SyncResult(status="error", resource="merchant", message=str(exc))


def _backfill_resource(
    client: CloverClient, conn: sqlite3.Connection, resource: str, spec: ResourceSpec, since_millis: int | None
) -> SyncResult:
    synced_at = _now_ms()
    total_rows = 0
    max_seen = since_millis or 0
    try:
        for page in client.paginate(
            spec.path,
            since_millis=since_millis,
            ts_field=spec.ts_field or "modifiedTime",
            expand=spec.expand,
            order_by=spec.ts_field,
        ):
            if not page:
                continue
            total_rows += spec.upsert(conn, page, synced_at)
            if spec.ts_field:
                max_seen = _max_ts(page, spec.ts_field, max_seen)
        new_mark = max_seen if spec.ts_field else synced_at
        db.set_high_water_mark(conn, resource, new_mark, total_rows, status="ok")
        return SyncResult(status="ok", resource=resource, rows_synced=total_rows)
    except CloverAPIError as exc:
        db.set_high_water_mark(
            conn, resource, since_millis or 0, total_rows, status="error", error=str(exc)
        )
        return SyncResult(status="error", resource=resource, rows_synced=total_rows, message=str(exc))


def run_backfill(config: CloverConfig, conn: sqlite3.Connection) -> RunSummary:
    if not config.is_configured:
        return RunSummary(status="not_configured", message=NOT_CONFIGURED_MESSAGE)

    client = CloverClient(base_url_for(config), config.merchant_id, config.token)
    results: list[SyncResult] = [_sync_merchant(client, conn, _now_ms())]

    lookback_ms = config.lookback_months * 30 * 24 * 60 * 60 * 1000
    since_for_time_filtered = _now_ms() - lookback_ms

    for resource in ALL_BACKFILL_RESOURCES:
        spec = RESOURCE_SPECS[resource]
        since = since_for_time_filtered if spec.ts_field else None
        results.append(_backfill_resource(client, conn, resource, spec, since))

    return _summarize(results)


def run_incremental(
    config: CloverConfig, conn: sqlite3.Connection, resources: list[str] | None = None
) -> RunSummary:
    if not config.is_configured:
        return RunSummary(status="not_configured", message=NOT_CONFIGURED_MESSAGE)

    client = CloverClient(base_url_for(config), config.merchant_id, config.token)
    target_resources = resources if resources is not None else DEFAULT_INCREMENTAL_RESOURCES
    results: list[SyncResult] = []

    for resource in target_resources:
        spec = RESOURCE_SPECS[resource]
        stored_mark = db.get_high_water_mark(conn, resource)
        if stored_mark == 0:
            results.append(
                SyncResult(
                    status="skipped",
                    resource=resource,
                    message=f"No prior backfill for '{resource}' — run backfill first.",
                )
            )
            continue
        effective_since = max(0, stored_mark - SAFETY_OVERLAP_MS) if spec.ts_field else None
        results.append(_backfill_resource(client, conn, resource, spec, effective_since))

    return _summarize(results)


def _summarize(results: list[SyncResult]) -> RunSummary:
    statuses = {r.status for r in results}
    if statuses == {"ok"} or statuses <= {"ok", "skipped"}:
        overall = "ok"
    elif "ok" in statuses or "skipped" in statuses:
        overall = "partial"
    else:
        overall = "error"
    return RunSummary(status=overall, results=results)
