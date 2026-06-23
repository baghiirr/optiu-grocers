from __future__ import annotations

from opti_connector.config import load_config


def test_not_configured_missing_all():
    config = load_config(env={})
    assert config.is_configured is False


def test_not_configured_missing_password_only():
    config = load_config(env={"OPTI_EMAIL": "a@b.com", "OPTI_BASE_URL": "https://x.optiu.ai"})
    assert config.is_configured is False


def test_configured_when_all_present():
    config = load_config(
        env={"OPTI_EMAIL": "a@b.com", "OPTI_PASSWORD": "pw", "OPTI_BASE_URL": "https://x.optiu.ai"}
    )
    assert config.is_configured is True


def test_blank_string_treated_as_missing():
    config = load_config(
        env={"OPTI_EMAIL": "", "OPTI_PASSWORD": "pw", "OPTI_BASE_URL": "https://x.optiu.ai"}
    )
    assert config.is_configured is False


def test_sector_defaults_to_grocers():
    config = load_config(env={})
    assert config.sector == "grocers"


def test_tenant_id_optional_does_not_affect_is_configured():
    config = load_config(
        env={"OPTI_EMAIL": "a@b.com", "OPTI_PASSWORD": "pw", "OPTI_BASE_URL": "https://x.optiu.ai"}
    )
    assert config.tenant_id is None
    assert config.is_configured is True


def test_db_path_default_matches_clover_default():
    config = load_config(env={})
    assert config.db_path == "./clover_data.sqlite3"
