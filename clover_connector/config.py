from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from dotenv import dotenv_values

REGION_BASE_URLS = {
    "us": "https://api.clover.com",
    "eu": "https://api.eu.clover.com",
    "la": "https://api.la.clover.com",
    "sandbox": "https://apisandbox.dev.clover.com",
}

DEFAULT_REGION = "us"
DEFAULT_DB_PATH = "./clover_data.sqlite3"
DEFAULT_LOOKBACK_MONTHS = 15


@dataclass(frozen=True)
class CloverConfig:
    token: str | None
    merchant_id: str | None
    region: str = DEFAULT_REGION
    db_path: str = DEFAULT_DB_PATH
    lookback_months: int = DEFAULT_LOOKBACK_MONTHS

    @property
    def is_configured(self) -> bool:
        return bool(self.token) and bool(self.merchant_id)


def _get(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None or value.strip() == "":
        return None
    return value


def load_config(env: Mapping[str, str] | None = None) -> CloverConfig:
    """Loads Clover connector config. Never raises for missing credentials —
    callers must check `is_configured` instead of relying on exceptions.

    Reads, in order: a `.env` file in the cwd (if present), then `env`
    (defaults to `os.environ`). Values from `env`/os.environ take precedence
    over `.env` so a real shell export can override a stale `.env` value.
    """
    merged: dict[str, str] = dict(dotenv_values(".env"))
    merged.update(env if env is not None else os.environ)

    token = _get(merged, "CLOVER_API_TOKEN")
    merchant_id = _get(merged, "CLOVER_MERCHANT_ID")
    region = _get(merged, "CLOVER_REGION") or DEFAULT_REGION
    db_path = _get(merged, "CLOVER_DB_PATH") or DEFAULT_DB_PATH
    lookback_raw = _get(merged, "CLOVER_LOOKBACK_MONTHS")
    lookback_months = int(lookback_raw) if lookback_raw else DEFAULT_LOOKBACK_MONTHS

    return CloverConfig(
        token=token,
        merchant_id=merchant_id,
        region=region,
        db_path=db_path,
        lookback_months=lookback_months,
    )


def base_url_for(config: CloverConfig) -> str:
    try:
        return REGION_BASE_URLS[config.region]
    except KeyError:
        raise ValueError(
            f"Unrecognized Clover region '{config.region}'. "
            f"Expected one of: {', '.join(REGION_BASE_URLS)}"
        )
