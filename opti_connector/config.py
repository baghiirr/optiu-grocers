from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from dotenv import dotenv_values

DEFAULT_SECTOR = "grocers"
DEFAULT_DB_PATH = "./clover_data.sqlite3"
DEFAULT_REQUEST_TIMEOUT = 30.0
DEFAULT_COLD_START_TIMEOUT = 90.0


@dataclass(frozen=True)
class OptiConfig:
    email: str | None
    password: str | None
    base_url: str | None
    api_key: str | None = None
    sector: str = DEFAULT_SECTOR
    tenant_id: str | None = None
    db_path: str = DEFAULT_DB_PATH
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT
    cold_start_timeout: float = DEFAULT_COLD_START_TIMEOUT

    @property
    def is_configured(self) -> bool:
        if not self.base_url:
            return False
        return bool(self.api_key) or (bool(self.email) and bool(self.password))

    @property
    def use_api_key(self) -> bool:
        return bool(self.api_key)


def _get(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None or value.strip() == "":
        return None
    return value


def load_config(env: Mapping[str, str] | None = None) -> OptiConfig:
    """Loads Opti connector config. Never raises for missing credentials —
    callers must check `is_configured` instead of relying on exceptions.

    Reads, in order: a `.env` file in the cwd (if present), then `env`
    (defaults to `os.environ`). Values from `env`/os.environ take precedence
    over `.env`.
    """
    merged: dict[str, str] = dict(dotenv_values(".env"))
    merged.update(env if env is not None else os.environ)

    email = _get(merged, "OPTI_EMAIL")
    password = _get(merged, "OPTI_PASSWORD")
    base_url = _get(merged, "OPTI_BASE_URL")
    api_key = _get(merged, "OPTI_API_KEY")
    sector = _get(merged, "OPTI_SECTOR") or DEFAULT_SECTOR
    tenant_id = _get(merged, "OPTI_TENANT_ID")
    db_path = _get(merged, "OPTI_DB_PATH") or DEFAULT_DB_PATH

    return OptiConfig(
        email=email,
        password=password,
        base_url=base_url,
        api_key=api_key,
        sector=sector,
        tenant_id=tenant_id,
        db_path=db_path,
    )
