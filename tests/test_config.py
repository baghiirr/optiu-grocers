from __future__ import annotations

import pytest

from clover_connector.config import REGION_BASE_URLS, base_url_for, load_config


def test_not_configured_missing_both():
    config = load_config(env={})
    assert config.is_configured is False


def test_not_configured_missing_token_only():
    config = load_config(env={"CLOVER_MERCHANT_ID": "MID1"})
    assert config.is_configured is False


def test_not_configured_missing_mid_only():
    config = load_config(env={"CLOVER_API_TOKEN": "tok"})
    assert config.is_configured is False


def test_configured_when_both_present():
    config = load_config(env={"CLOVER_API_TOKEN": "tok", "CLOVER_MERCHANT_ID": "MID1"})
    assert config.is_configured is True


def test_blank_string_treated_as_missing():
    config = load_config(env={"CLOVER_API_TOKEN": "", "CLOVER_MERCHANT_ID": "MID1"})
    assert config.is_configured is False


def test_region_defaults_to_us():
    config = load_config(env={})
    assert config.region == "us"


def test_region_can_be_overridden():
    config = load_config(env={"CLOVER_REGION": "eu"})
    assert config.region == "eu"


@pytest.mark.parametrize("region", list(REGION_BASE_URLS.keys()))
def test_base_url_for_each_region(region):
    config = load_config(env={"CLOVER_REGION": region})
    assert base_url_for(config) == REGION_BASE_URLS[region]


def test_base_url_unknown_region_raises():
    config = load_config(env={"CLOVER_REGION": "mars"})
    with pytest.raises(ValueError):
        base_url_for(config)


def test_db_path_and_lookback_defaults():
    config = load_config(env={})
    assert config.db_path == "./clover_data.sqlite3"
    assert config.lookback_months == 15
