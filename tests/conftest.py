from __future__ import annotations

import json
from pathlib import Path

import pytest

from clover_connector import db
from clover_connector.config import CloverConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


def make_n_items(n: int, base_modified: int = 1718050000000) -> dict:
    return {
        "elements": [
            {"id": f"GEN{i}", "name": f"Generated Item {i}", "price": 100 + i, "modifiedTime": base_modified + i}
            for i in range(n)
        ],
        "href": "https://api.clover.com/v3/merchants/$MID/items",
    }


@pytest.fixture
def tmp_db_path(tmp_path) -> str:
    return str(tmp_path / "clover_test.sqlite3")


@pytest.fixture
def configured_config(tmp_db_path) -> CloverConfig:
    return CloverConfig(
        token="test-token",
        merchant_id="TESTMID",
        region="sandbox",
        db_path=tmp_db_path,
        lookback_months=15,
    )


@pytest.fixture
def unconfigured_config(tmp_db_path) -> CloverConfig:
    return CloverConfig(token=None, merchant_id=None, region="sandbox", db_path=tmp_db_path)


@pytest.fixture
def conn(tmp_db_path):
    connection = db.get_connection(tmp_db_path)
    db.init_db(connection)
    yield connection
    connection.close()
