from __future__ import annotations

from opti_connector import decisions_store


def make_decision(decision_id="dec_1", **overrides):
    base = {
        "decision_id": decision_id,
        "run_id": "run_1",
        "site_id": "ST_1",
        "decision_type": "SET_MARKDOWN",
        "target_id": "ITM_1",
        "recommended_value": {"to_price": 2.09},
        "confidence": 0.76,
        "created_at": "2026-06-22T10:16:00Z",
        "why_summary": "Mark down 30%.",
    }
    base.update(overrides)
    return base


def test_upsert_creates_decision_and_pending_status(conn):
    count = decisions_store.upsert_decisions(conn, [make_decision()], fetched_at=1)
    assert count == 1

    rows = decisions_store.list_decisions(conn, status="all")
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["decision_type"] == "SET_MARKDOWN"


def test_reupsert_does_not_reset_reviewed_status(conn):
    decisions_store.upsert_decisions(conn, [make_decision()], fetched_at=1)
    decisions_store.mark_decision(conn, "dec_1", "applied", reviewed_by="baqir")

    decisions_store.upsert_decisions(conn, [make_decision(confidence=0.9)], fetched_at=2)

    rows = decisions_store.list_decisions(conn, status="all")
    assert rows[0]["status"] == "applied"
    assert rows[0]["confidence"] == 0.9


def test_mark_decision_unknown_id_returns_false(conn):
    assert decisions_store.mark_decision(conn, "does-not-exist", "applied") is False


def test_mark_decision_happy_path(conn):
    decisions_store.upsert_decisions(conn, [make_decision()], fetched_at=1)
    found = decisions_store.mark_decision(conn, "dec_1", "dismissed", reviewed_by="baqir", notes="not relevant")
    assert found is True

    rows = decisions_store.list_decisions(conn, status="dismissed")
    assert len(rows) == 1
    assert rows[0]["reviewed_by"] == "baqir"
    assert rows[0]["notes"] == "not relevant"


def test_list_decisions_filters_by_status(conn):
    decisions_store.upsert_decisions(
        conn, [make_decision("dec_1"), make_decision("dec_2")], fetched_at=1
    )
    decisions_store.mark_decision(conn, "dec_1", "applied")

    pending = decisions_store.list_decisions(conn, status="pending")
    assert [r["decision_id"] for r in pending] == ["dec_2"]

    all_rows = decisions_store.list_decisions(conn, status="all")
    assert len(all_rows) == 2


def test_list_decisions_filters_by_type_and_site(conn):
    decisions_store.upsert_decisions(
        conn,
        [
            make_decision("dec_1", decision_type="SET_MARKDOWN", site_id="ST_1"),
            make_decision("dec_2", decision_type="PLACE_PO", site_id="ST_2"),
        ],
        fetched_at=1,
    )
    rows = decisions_store.list_decisions(conn, status="all", decision_type="PLACE_PO")
    assert [r["decision_id"] for r in rows] == ["dec_2"]

    rows = decisions_store.list_decisions(conn, status="all", site_id="ST_1")
    assert [r["decision_id"] for r in rows] == ["dec_1"]
