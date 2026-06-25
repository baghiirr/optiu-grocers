from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

from clover_connector import db as clover_db

from . import decisions_store, mapping, workbook
from .client import OptiAPIError, OptiClient
from .config import load_config

NOT_CONFIGURED_MESSAGE = (
    "Opti not connected — set OPTI_EMAIL, OPTI_PASSWORD, and OPTI_BASE_URL "
    "(e.g. in a .env file) to enable pushing data / pulling decisions."
)

ALL_AOM_IDS = [
    "PERISHABLE_01",
    "VENDOR_PO_01",
    "SHELF_MARKDOWN_01",
    "SHRINK_01",
    "GROCERS_RETENTION_01",
    "GROCERS_LABOR_01",
    "GROCERS_PLAN_01",
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _with_db_path(config, db_path: str | None):
    if not db_path:
        return config
    return type(config)(**{**config.__dict__, "db_path": db_path})


def _build_client(config) -> OptiClient:
    return OptiClient(
        config.base_url,
        config.email,
        config.password,
        sector=config.sector,
        timeout=config.request_timeout,
        cold_start_timeout=config.cold_start_timeout,
    )


def _resolve_tenant(client: OptiClient, config) -> str:
    if config.tenant_id:
        client.set_tenant_id(config.tenant_id)
        return config.tenant_id
    return client.discover_tenant()


def cmd_push(args: argparse.Namespace) -> int:
    config = _with_db_path(load_config(), args.db_path)
    if not config.is_configured:
        print(NOT_CONFIGURED_MESSAGE)
        return 0
    if not os.path.exists(config.db_path):
        print("No Clover data yet — run `clover-connector backfill` first.")
        return 0

    conn = clover_db.get_connection(config.db_path)
    sheets = mapping.build_all_sheets(conn)

    fd, workbook_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    workbook.build_workbook(sheets, workbook_path)

    mode = "full_overwrite" if args.full_overwrite else "incremental"
    client = _build_client(config)

    try:
        if config.use_api_key:
            print("Using direct integration (API key)...")
            result = client.push_direct(workbook_path, config.api_key, mode=mode)
            job_id = result.get("id") or result.get("job_id")
            if job_id:
                result = client.poll_job(job_id)
        else:
            client.login()
            _resolve_tenant(client, config)
            job = client.upload_workbook(workbook_path, mode=mode)
            result = client.poll_job(job["id"])
    except OptiAPIError as exc:
        print(f"Opti push failed: {exc}")
        return 1
    finally:
        os.unlink(workbook_path)

    rows_loaded = result.get("rows_loaded") or {}
    clover_db.set_high_water_mark(
        conn, "opti_push", _now_ms(), sum(rows_loaded.values()) or sum(len(v) for v in sheets.values()),
        status=result.get("status", "unknown"),
    )

    print(f"push status: {result.get('status')}")
    for table, count in rows_loaded.items():
        print(f"  {table}: {count}")
    if result.get("validation_errors"):
        print("validation_errors:")
        for err in result["validation_errors"]:
            print(f"  {err}")

    return 1 if result.get("status") == "FAILED" else 0


def cmd_export(args: argparse.Namespace) -> int:
    config = _with_db_path(load_config(), args.db_path)
    if not os.path.exists(config.db_path):
        print("No Clover data yet — run `clover-connector backfill` first.")
        return 1

    conn = clover_db.get_connection(config.db_path)
    sheets = mapping.build_all_sheets(conn)
    out = args.output or "opti_catalog.xlsx"
    workbook.build_workbook(sheets, out)
    print(f"Workbook saved to {out}")
    for sheet, rows in sheets.items():
        print(f"  {sheet}: {len(rows)} rows")
    return 0


def cmd_rethink(args: argparse.Namespace) -> int:
    config = load_config()
    if not config.is_configured:
        print(NOT_CONFIGURED_MESSAGE)
        return 0

    client = _build_client(config)
    try:
        client.login()
        _resolve_tenant(client, config)
        result = client.trigger_rethink()
    except OptiAPIError as exc:
        print(f"Opti rethink failed: {exc}")
        return 1
    print(result)
    return 0


def cmd_decisions_pull(args: argparse.Namespace) -> int:
    config = _with_db_path(load_config(), args.db_path)
    if not config.is_configured:
        print(NOT_CONFIGURED_MESSAGE)
        return 0

    conn = clover_db.get_connection(config.db_path)
    clover_db.init_db(conn)

    client = _build_client(config)
    aom_ids = args.aom_id or ALL_AOM_IDS
    total = 0
    try:
        client.login()
        _resolve_tenant(client, config)
        for aom_id in aom_ids:
            decisions = client.get_decisions(aom_id, site_id=args.site_id)
            count = decisions_store.upsert_decisions(conn, decisions, fetched_at=_now_ms())
            print(f"{aom_id}: {count} decisions")
            total += count
    except OptiAPIError as exc:
        print(f"Opti decisions pull failed: {exc}")
        return 1

    clover_db.set_high_water_mark(conn, "opti_pull", _now_ms(), total, status="ok")
    return 0


def cmd_decisions_list(args: argparse.Namespace) -> int:
    config = _with_db_path(load_config(), args.db_path)
    if not os.path.exists(config.db_path):
        print("No local database yet.")
        return 0

    conn = clover_db.get_connection(config.db_path)
    clover_db.init_db(conn)
    rows = decisions_store.list_decisions(conn, status=args.status, decision_type=args.type, site_id=args.site)

    if not rows:
        print("No decisions found.")
        return 0

    print(f"{'decision_id':<40} {'type':<22} {'site':<14} {'target':<14} {'conf':>5}  {'status':<10} why")
    for r in rows:
        why = (r["why_summary"] or "")[:60]
        confidence = r["confidence"] if r["confidence"] is not None else 0.0
        print(
            f"{r['decision_id']:<40} {r['decision_type'] or '':<22} {r['site_id'] or '':<14} "
            f"{r['target_id'] or '':<14} {confidence:>5.2f}  {r['status']:<10} {why}"
        )
    return 0


def cmd_decisions_mark(args: argparse.Namespace) -> int:
    config = _with_db_path(load_config(), args.db_path)
    conn = clover_db.get_connection(config.db_path)
    clover_db.init_db(conn)

    found = decisions_store.mark_decision(
        conn, args.decision_id, args.new_status, reviewed_by=args.reviewed_by, notes=args.notes
    )
    if not found:
        print(f"decision_id '{args.decision_id}' not found.")
        return 1
    print(f"Marked {args.decision_id} as {args.new_status}.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _with_db_path(load_config(), args.db_path)

    if not config.is_configured:
        print(NOT_CONFIGURED_MESSAGE)
    else:
        print(f"Opti connected — sector={config.sector}, base_url={config.base_url}, db={config.db_path}")

    if not os.path.exists(config.db_path):
        print("No local database yet.")
        return 0

    conn = clover_db.get_connection(config.db_path)
    rows = conn.execute(
        "SELECT resource, last_run_at, last_status, last_row_count FROM sync_state "
        "WHERE resource IN ('opti_push', 'opti_pull')"
    ).fetchall()
    if not rows:
        print("No Opti push/pull has run yet.")
        return 0

    for r in rows:
        last_run = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["last_run_at"] / 1000))
            if r["last_run_at"]
            else "-"
        )
        print(f"{r['resource']:<12} last_run={last_run} status={r['last_status']} rows={r['last_row_count']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opti-connector")
    parser.add_argument("--db-path", default=None, help="Override the shared SQLite db path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="Save Clover data as an Excel workbook for manual upload")
    p_export.add_argument("--output", "-o", default="opti_catalog.xlsx", help="Output file path")
    p_export.set_defaults(func=cmd_export)

    p_push = sub.add_parser("push", help="Map Clover data into the Opti catalog workbook and upload it")
    p_push.add_argument("--full-overwrite", action="store_true")
    p_push.set_defaults(func=cmd_push)

    p_rethink = sub.add_parser("rethink", help="Trigger an Opti Brain re-run")
    p_rethink.set_defaults(func=cmd_rethink)

    p_decisions = sub.add_parser("decisions", help="Pull/list/mark Opti decisions")
    decisions_sub = p_decisions.add_subparsers(dest="decisions_command", required=True)

    p_pull = decisions_sub.add_parser("pull")
    p_pull.add_argument("--aom-id", action="append", help="Repeatable; defaults to all AOMs")
    p_pull.add_argument("--site-id", default=None)
    p_pull.set_defaults(func=cmd_decisions_pull)

    p_list = decisions_sub.add_parser("list")
    p_list.add_argument("--status", default="pending", choices=["pending", "applied", "dismissed", "all"])
    p_list.add_argument("--type", default=None)
    p_list.add_argument("--site", default=None)
    p_list.set_defaults(func=cmd_decisions_list)

    p_mark = decisions_sub.add_parser("mark")
    p_mark.add_argument("decision_id")
    p_mark.add_argument("new_status", choices=["applied", "dismissed", "pending"])
    p_mark.add_argument("--reviewed-by", default=None)
    p_mark.add_argument("--notes", default=None)
    p_mark.set_defaults(func=cmd_decisions_mark)

    p_status = sub.add_parser("status", help="Show Opti connection and last push/pull status")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # pragma: no cover - safety net for truly unexpected failures
        print(f"opti-connector: unexpected error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
