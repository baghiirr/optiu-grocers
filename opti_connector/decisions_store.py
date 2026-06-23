from __future__ import annotations

import json
import sqlite3
from typing import Literal


def upsert_decisions(conn: sqlite3.Connection, decisions: list[dict], fetched_at: int) -> int:
    if not decisions:
        return 0
    with conn:
        for d in decisions:
            conn.execute(
                """
                INSERT INTO opti_decisions
                    (decision_id, run_id, site_id, decision_type, target_id,
                     recommended_value, confidence, created_at, why_summary, raw_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    run_id = excluded.run_id,
                    site_id = excluded.site_id,
                    decision_type = excluded.decision_type,
                    target_id = excluded.target_id,
                    recommended_value = excluded.recommended_value,
                    confidence = excluded.confidence,
                    created_at = excluded.created_at,
                    why_summary = excluded.why_summary,
                    raw_json = excluded.raw_json,
                    fetched_at = excluded.fetched_at
                """,
                (
                    d["decision_id"],
                    d.get("run_id"),
                    d.get("site_id"),
                    d.get("decision_type"),
                    d.get("target_id"),
                    json.dumps(d.get("recommended_value")),
                    d.get("confidence"),
                    d.get("created_at"),
                    d.get("why_summary"),
                    json.dumps(d),
                    fetched_at,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO opti_decision_status (decision_id, status) VALUES (?, 'pending')",
                (d["decision_id"],),
            )
    return len(decisions)


def list_decisions(
    conn: sqlite3.Connection,
    status: str | None = "pending",
    decision_type: str | None = None,
    site_id: str | None = None,
) -> list[sqlite3.Row]:
    query = """
        SELECT d.decision_id, d.run_id, d.site_id, d.decision_type, d.target_id,
               d.recommended_value, d.confidence, d.created_at, d.why_summary,
               s.status, s.reviewed_by, s.reviewed_at, s.notes
        FROM opti_decisions d
        JOIN opti_decision_status s ON s.decision_id = d.decision_id
        WHERE 1=1
    """
    params: list = []
    if status and status != "all":
        query += " AND s.status = ?"
        params.append(status)
    if decision_type:
        query += " AND d.decision_type = ?"
        params.append(decision_type)
    if site_id:
        query += " AND d.site_id = ?"
        params.append(site_id)
    query += " ORDER BY d.created_at DESC"
    return conn.execute(query, params).fetchall()


def mark_decision(
    conn: sqlite3.Connection,
    decision_id: str,
    status: Literal["applied", "dismissed", "pending"],
    reviewed_by: str | None = None,
    notes: str | None = None,
    reviewed_at: int | None = None,
) -> bool:
    import time as _time

    reviewed_at = reviewed_at if reviewed_at is not None else int(_time.time() * 1000)
    with conn:
        cursor = conn.execute(
            """
            UPDATE opti_decision_status
            SET status = ?, reviewed_by = ?, reviewed_at = ?, notes = ?
            WHERE decision_id = ?
            """,
            (status, reviewed_by, reviewed_at, notes, decision_id),
        )
    return cursor.rowcount > 0
