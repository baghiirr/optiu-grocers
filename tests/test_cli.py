from __future__ import annotations

import os

import responses

from clover_connector import cli, db, sync

from .conftest import load_fixture

BASE_URL = "https://api.clover.com"
MID = "TESTMID"


def _url(path: str) -> str:
    return f"{BASE_URL}/v3/merchants/{MID}{path}"


def _clear_clover_env(monkeypatch):
    for key in ("CLOVER_API_TOKEN", "CLOVER_MERCHANT_ID", "CLOVER_REGION", "CLOVER_DB_PATH"):
        monkeypatch.delenv(key, raising=False)


def test_backfill_not_configured_prints_friendly_message_exit_0(monkeypatch, capsys, tmp_db_path):
    _clear_clover_env(monkeypatch)
    code = cli.main(["--db-path", tmp_db_path, "backfill"])
    out = capsys.readouterr().out
    assert sync.NOT_CONFIGURED_MESSAGE in out
    assert code == 0


def test_sync_not_configured_prints_friendly_message_exit_0(monkeypatch, capsys, tmp_db_path):
    _clear_clover_env(monkeypatch)
    code = cli.main(["--db-path", tmp_db_path, "sync"])
    out = capsys.readouterr().out
    assert sync.NOT_CONFIGURED_MESSAGE in out
    assert code == 0


def test_status_not_configured_shows_message(monkeypatch, capsys, tmp_db_path):
    _clear_clover_env(monkeypatch)
    assert not os.path.exists(tmp_db_path)
    code = cli.main(["--db-path", tmp_db_path, "status"])
    out = capsys.readouterr().out
    assert sync.NOT_CONFIGURED_MESSAGE in out
    assert code == 0


def test_status_configured_with_prior_run_shows_table(monkeypatch, capsys, tmp_db_path):
    monkeypatch.setenv("CLOVER_API_TOKEN", "tok")
    monkeypatch.setenv("CLOVER_MERCHANT_ID", MID)

    conn = db.get_connection(tmp_db_path)
    db.init_db(conn)
    db.set_high_water_mark(conn, "items", 1718050000000, 3, status="ok")
    conn.close()

    code = cli.main(["--db-path", tmp_db_path, "status"])
    out = capsys.readouterr().out
    assert "items" in out
    assert code == 0


@responses.activate
def test_backfill_configured_happy_path_exit_0(monkeypatch, capsys, tmp_db_path):
    monkeypatch.setenv("CLOVER_API_TOKEN", "tok")
    monkeypatch.setenv("CLOVER_MERCHANT_ID", MID)
    monkeypatch.setenv("CLOVER_REGION", "us")

    responses.add(responses.GET, _url(""), json=load_fixture("merchant_root.json"), status=200)
    empty = load_fixture("items_empty.json")
    for resource, spec in sync.RESOURCE_SPECS.items():
        responses.add(responses.GET, _url(spec.path), json=empty, status=200)

    code = cli.main(["--db-path", tmp_db_path, "backfill"])
    out = capsys.readouterr().out
    assert "overall: ok" in out
    assert code == 0


def test_main_unexpected_exception_returns_exit_2_not_traceback(monkeypatch, capsys):
    def boom(args):
        raise ValueError("boom")

    monkeypatch.setattr(cli, "cmd_status", boom)

    code = cli.main(["status"])
    err = capsys.readouterr().err
    assert "unexpected error" in err
    assert code == 2
