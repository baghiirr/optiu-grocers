from __future__ import annotations

import argparse
import sys
import time

from . import db, sync
from .config import load_config


def _print_summary(summary: sync.RunSummary) -> None:
    if summary.status == "not_configured":
        print(summary.message)
        return

    print(f"{'resource':<16} {'status':<10} {'rows':>6}  message")
    for r in summary.results:
        print(f"{r.resource or '':<16} {r.status:<10} {r.rows_synced:>6}  {r.message}")
    print(f"\noverall: {summary.status}")


def cmd_backfill(args: argparse.Namespace) -> int:
    config = load_config()
    if args.db_path:
        config = type(config)(**{**config.__dict__, "db_path": args.db_path})

    if not config.is_configured:
        print(sync.NOT_CONFIGURED_MESSAGE)
        return 0

    conn = db.get_connection(config.db_path)
    db.init_db(conn)
    summary = sync.run_backfill(config, conn)
    _print_summary(summary)
    return 1 if summary.status == "error" else 0


def cmd_sync(args: argparse.Namespace) -> int:
    config = load_config()
    if args.db_path:
        config = type(config)(**{**config.__dict__, "db_path": args.db_path})

    if not config.is_configured:
        print(sync.NOT_CONFIGURED_MESSAGE)
        return 0

    conn = db.get_connection(config.db_path)
    db.init_db(conn)
    resources = args.resource or None
    summary = sync.run_incremental(config, conn, resources=resources)
    _print_summary(summary)
    return 1 if summary.status == "error" else 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config()
    if args.db_path:
        config = type(config)(**{**config.__dict__, "db_path": args.db_path})

    if not config.is_configured:
        print(sync.NOT_CONFIGURED_MESSAGE)
    else:
        print(f"Clover connected — region={config.region}, db={config.db_path}")

    import os

    if not os.path.exists(config.db_path):
        print("No local database yet (run 'backfill' first).")
        return 0

    conn = db.get_connection(config.db_path)
    rows = conn.execute(
        "SELECT resource, last_modified_millis, last_run_at, last_status, last_row_count FROM sync_state ORDER BY resource"
    ).fetchall()
    if not rows:
        print("Database exists but no sync has run yet.")
        return 0

    print(f"\n{'resource':<16} {'last_modified':<24} {'last_run':<24} {'status':<10} {'rows':>6}")
    for row in rows:
        last_modified = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["last_modified_millis"] / 1000))
            if row["last_modified_millis"]
            else "-"
        )
        last_run = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["last_run_at"] / 1000))
            if row["last_run_at"]
            else "-"
        )
        print(
            f"{row['resource']:<16} {last_modified:<24} {last_run:<24} "
            f"{row['last_status'] or '-':<10} {row['last_row_count'] or 0:>6}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clover-connector")
    parser.add_argument("--db-path", default=None, help="Override the SQLite db path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_backfill = sub.add_parser("backfill", help="Run the Phase 1 full backfill")
    p_backfill.set_defaults(func=cmd_backfill)

    p_sync = sub.add_parser("sync", help="Run the Phase 2 incremental sync")
    p_sync.add_argument("--resource", action="append", help="Restrict sync to this resource (repeatable)")
    p_sync.set_defaults(func=cmd_sync)

    p_status = sub.add_parser("status", help="Show connection and sync status")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # pragma: no cover - safety net for truly unexpected failures
        print(f"clover-connector: unexpected error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
