from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Literal

import requests


class OptiAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class OptiAuthError(OptiAPIError):
    """401 — missing/expired opti_session cookie, or bad credentials."""


class OptiValidationError(OptiAPIError):
    """422 — structural validation failed on analyze/upload."""

    def __init__(self, message: str, status_code: int | None = None, body: str = "", validation_errors=None):
        super().__init__(message, status_code=status_code, body=body)
        self.validation_errors = validation_errors


class OptiServerError(OptiAPIError):
    """5xx after the one cold-start retry is exhausted."""


class OptiNotFoundError(OptiAPIError):
    """404 — e.g. an unknown tenant or job id."""


UploadMode = Literal["full_overwrite", "incremental", "closer_overwrite"]


class OptiClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        sector: str = "grocers",
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
        cold_start_timeout: float = 90.0,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.sector = sector
        self.session = session or requests.Session()
        self.sleep_fn = sleep_fn
        self.timeout = timeout
        self.cold_start_timeout = cold_start_timeout
        self.max_retries = max_retries
        self._tenant_id: str | None = None
        self._logged_in = False

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def login(self) -> dict:
        response = self.session.post(
            self._url("/api/auth/login"),
            json={"email": self.email, "password": self.password, "sector": self.sector},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise OptiAuthError(
                f"Opti login failed ({response.status_code})",
                status_code=response.status_code,
                body=response.text,
            )
        self._logged_in = True
        return response.json()

    def _ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    def discover_tenant(self, sector: str | None = None) -> str:
        sector = sector or self.sector
        body = self._request("get", "/api/tenants")
        tenants = body if isinstance(body, list) else body.get("tenants", [])
        match = next((t for t in tenants if t.get("sector") == sector), None) or (
            tenants[0] if tenants else None
        )
        if match is None:
            raise OptiAPIError("No Opti tenants found for this account")
        self._tenant_id = match["id"]
        return self._tenant_id

    @property
    def tenant_id(self) -> str:
        if self._tenant_id is None:
            raise OptiAPIError("tenant_id not set — call discover_tenant() first")
        return self._tenant_id

    def set_tenant_id(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id

    def get_excel_template(self) -> bytes:
        response = self._raw_request("get", f"/api/sectors/{self.sector}/excel-template")
        return response.content

    def upload_workbook(self, path: str | os.PathLike, mode: UploadMode = "incremental") -> dict:
        with open(path, "rb") as fh:
            response = self._raw_request(
                "post",
                f"/api/tenants/{self.tenant_id}/upload",
                params={"mode": mode},
                files={"file": fh},
            )
        return response.json()

    def push_direct(self, path: str | os.PathLike, api_key: str, mode: UploadMode = "incremental") -> dict:
        with open(path, "rb") as fh:
            response = self.session.post(
                self._url("/api/integrations/ingest"),
                headers={"X-API-Key": api_key},
                params={"mode": mode},
                files={"file": fh},
                timeout=self.timeout,
            )
        if response.status_code not in (200, 204):
            raise OptiAPIError(
                f"Direct push failed ({response.status_code})",
                status_code=response.status_code,
                body=response.text,
            )
        try:
            return response.json()
        except ValueError:
            return {}

    def poll_job(self, job_id: str, interval: float = 5.0, max_wait: float = 600.0) -> dict:
        waited = 0.0
        while True:
            job = self._request("get", f"/api/jobs/{job_id}")
            if job.get("status") in ("COMPLETE", "FAILED"):
                return job
            if waited >= max_wait:
                raise OptiAPIError(f"Timed out waiting for job {job_id} to complete")
            self.sleep_fn(interval)
            waited += interval

    def trigger_rethink(self) -> dict:
        return self._request("post", f"/api/tenants/{self.tenant_id}/brain/rethink", json={})

    def get_decisions(self, aom_id: str, site_id: str | None = None, limit: int = 500) -> list[dict]:
        params = {"aom_id": aom_id, "limit": limit}
        if site_id:
            params["site_id"] = site_id
        body = self._request("get", f"/api/tenants/{self.tenant_id}/decisions", params=params)
        return body if isinstance(body, list) else body.get("elements", [])

    def _request(self, method: str, path: str, _retried_auth: bool = False, _retried_server: bool = False, **kwargs) -> dict:
        response = self._raw_request(method, path, _retried_auth=_retried_auth, _retried_server=_retried_server, **kwargs)
        try:
            return response.json()
        except ValueError:
            return {}

    def _raw_request(
        self,
        method: str,
        path: str,
        _retried_auth: bool = False,
        _retried_server: bool = False,
        **kwargs,
    ) -> requests.Response:
        self._ensure_login()
        timeout = kwargs.pop("timeout", self.timeout)
        response = self.session.request(method, self._url(path), timeout=timeout, **kwargs)

        if response.status_code == 200 or response.status_code == 204:
            return response

        if response.status_code == 401:
            if not _retried_auth:
                self._logged_in = False
                self.login()
                return self._raw_request(method, path, _retried_auth=True, _retried_server=_retried_server, **kwargs)
            raise OptiAuthError("Opti session expired/invalid (401)", status_code=401, body=response.text)

        if response.status_code == 422:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            raise OptiValidationError(
                "Opti rejected the request (422 validation error)",
                status_code=422,
                body=response.text,
                validation_errors=payload.get("validation_errors"),
            )

        if response.status_code == 404:
            raise OptiNotFoundError(f"Opti resource not found (404) at {path}", status_code=404, body=response.text)

        if response.status_code >= 500:
            if not _retried_server:
                self.sleep_fn(0)
                return self._raw_request(
                    method,
                    path,
                    _retried_auth=_retried_auth,
                    _retried_server=True,
                    timeout=self.cold_start_timeout,
                    **kwargs,
                )
            raise OptiServerError(
                f"Opti server error ({response.status_code}) at {path}",
                status_code=response.status_code,
                body=response.text,
            )

        raise OptiAPIError(
            f"Unexpected Opti response ({response.status_code}) at {path}",
            status_code=response.status_code,
            body=response.text,
        )
