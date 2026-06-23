from __future__ import annotations

import os

import responses

from clover_connector import db as clover_db
from opti_connector import cli, decisions_store

BASE_URL = "https://grocers-app.optiu.ai"
LOGIN_URL = f"{BASE_URL}/api/auth/login"
TENANTS_URL = f"{BASE_URL}/api/tenants"


def _clear_opti_env(monkeypatch):
    for key in ("OPTI_EMAIL", "OPTI_PASSWORD", "OPTI_BASE_URL", "OPTI_SECTOR", "OPTI_TENANT_ID"):
        monkeypatch.delenv(key, raising=False)


def _set_opti_env(monkeypatch):
    monkeypatch.setenv("OPTI_EMAIL", "ops@acme.com")
    monkeypatch.setenv("OPTI_PASSWORD", "pw")
    monkeypatch.setenv("OPTI_BASE_URL", BASE_URL)


def test_push_not_configured_prints_friendly_message_exit_0(monkeypatch, capsys, tmp_db_path):
    _clear_opti_env(monkeypatch)
    code = cli.main(["--db-path", tmp_db_path, "push"])
    out = capsys.readouterr().out
    assert cli.NOT_CONFIGURED_MESSAGE in out
    assert code == 0


def test_rethink_not_configured_prints_friendly_message_exit_0(monkeypatch, capsys):
    _clear_opti_env(monkeypatch)
    code = cli.main(["rethink"])
    out = capsys.readouterr().out
    assert cli.NOT_CONFIGURED_MESSAGE in out
    assert code == 0


@responses.activate
def test_decisions_pull_not_configured_makes_zero_http_calls(monkeypatch, capsys, tmp_db_path):
    _clear_opti_env(monkeypatch)
    code = cli.main(["--db-path", tmp_db_path, "decisions", "pull"])
    out = capsys.readouterr().out
    assert cli.NOT_CONFIGURED_MESSAGE in out
    assert code == 0
    assert len(responses.calls) == 0


def test_push_with_no_clover_db_yet(monkeypatch, capsys, tmp_db_path):
    _set_opti_env(monkeypatch)
    assert not os.path.exists(tmp_db_path)
    code = cli.main(["--db-path", tmp_db_path, "push"])
    out = capsys.readouterr().out
    assert "run `clover-connector backfill` first" in out
    assert code == 0


def test_decisions_list_and_mark_happy_path_no_network(monkeypatch, capsys, tmp_db_path):
    conn = clover_db.get_connection(tmp_db_path)
    clover_db.init_db(conn)
    decisions_store.upsert_decisions(
        conn,
        [
            {
                "decision_id": "dec_1",
                "run_id": "run_1",
                "site_id": "ST_1",
                "decision_type": "SET_MARKDOWN",
                "target_id": "ITM_1",
                "recommended_value": {"to_price": 2.09},
                "confidence": 0.76,
                "created_at": "2026-06-22T10:16:00Z",
                "why_summary": "Mark down 30%.",
            }
        ],
        fetched_at=1,
    )
    conn.close()

    code = cli.main(["--db-path", tmp_db_path, "decisions", "list"])
    out = capsys.readouterr().out
    assert "dec_1" in out
    assert code == 0

    code = cli.main(["--db-path", tmp_db_path, "decisions", "mark", "dec_1", "applied", "--reviewed-by", "baqir"])
    out = capsys.readouterr().out
    assert "Marked dec_1 as applied" in out
    assert code == 0

    code = cli.main(["--db-path", tmp_db_path, "decisions", "list", "--status", "pending"])
    out = capsys.readouterr().out
    assert "No decisions found" in out
    assert code == 0


def test_decisions_mark_unknown_id(monkeypatch, capsys, tmp_db_path):
    conn = clover_db.get_connection(tmp_db_path)
    clover_db.init_db(conn)
    conn.close()

    code = cli.main(["--db-path", tmp_db_path, "decisions", "mark", "nope", "applied"])
    out = capsys.readouterr().out
    assert "not found" in out
    assert code == 1


@responses.activate
def test_push_full_happy_path_exit_0(monkeypatch, capsys, tmp_db_path):
    _set_opti_env(monkeypatch)
    conn = clover_db.get_connection(tmp_db_path)
    clover_db.init_db(conn)
    conn.close()

    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    responses.add(responses.GET, TENANTS_URL, json=[{"id": "tenant_abc123", "sector": "grocers"}], status=200)
    upload_url = f"{BASE_URL}/api/tenants/tenant_abc123/upload"
    responses.add(responses.POST, upload_url, json={"id": "job_124", "status": "RUNNING"}, status=200)
    job_url = f"{BASE_URL}/api/jobs/job_124"
    responses.add(
        responses.GET,
        job_url,
        json={"id": "job_124", "status": "COMPLETE", "rows_loaded": {"INP_STORE": 0}},
        status=200,
    )

    code = cli.main(["--db-path", tmp_db_path, "push"])
    out = capsys.readouterr().out
    assert "push status: COMPLETE" in out
    assert code == 0


@responses.activate
def test_push_failed_job_returns_nonzero_exit(monkeypatch, capsys, tmp_db_path):
    _set_opti_env(monkeypatch)
    conn = clover_db.get_connection(tmp_db_path)
    clover_db.init_db(conn)
    conn.close()

    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    responses.add(responses.GET, TENANTS_URL, json=[{"id": "tenant_abc123", "sector": "grocers"}], status=200)
    upload_url = f"{BASE_URL}/api/tenants/tenant_abc123/upload"
    responses.add(responses.POST, upload_url, json={"id": "job_125", "status": "RUNNING"}, status=200)
    job_url = f"{BASE_URL}/api/jobs/job_125"
    responses.add(
        responses.GET,
        job_url,
        json={"id": "job_125", "status": "FAILED", "validation_errors": ["bad data"]},
        status=200,
    )

    code = cli.main(["--db-path", tmp_db_path, "push"])
    out = capsys.readouterr().out
    assert "push status: FAILED" in out
    assert "bad data" in out
    assert code == 1
