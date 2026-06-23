from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator

import requests

logger = logging.getLogger(__name__)


class CloverAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class CloverAuthError(CloverAPIError):
    """401 — bad, expired, or revoked token."""


class CloverPermissionError(CloverAPIError):
    """403 — token lacks the read permission for this resource."""


class CloverBadRequestError(CloverAPIError):
    """400 — bad filter/param."""


class CloverRetryExhaustedError(CloverAPIError):
    """429 retries exhausted."""


class CloverServerError(CloverAPIError):
    """5xx after retries exhausted."""


def _backoff_seconds(attempt: int) -> float:
    return float(2**attempt)


class CloverClient:
    def __init__(
        self,
        base_url: str,
        merchant_id: str,
        token: str,
        session: requests.Session | None = None,
        max_retries: int = 6,
        timeout: float = 30.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_backoff_seconds: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.merchant_id = merchant_id
        self.token = token
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.timeout = timeout
        self.sleep_fn = sleep_fn
        self.max_backoff_seconds = max_backoff_seconds

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else f"/{path}" if path else ""
        return f"{self.base_url}/v3/merchants/{self.merchant_id}{path}"

    def _retry_after_seconds(self, response: requests.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value >= 0 else None

    def get_object(self, path: str = "") -> dict:
        """Single-object GET (no pagination envelope) — e.g. the merchant root."""
        return self._request(path, params=[])

    def get_page(
        self,
        path: str,
        limit: int = 1000,
        offset: int = 0,
        filters: Iterable[str] | None = None,
        expand: Iterable[str] | None = None,
        order_by: str | None = None,
    ) -> tuple[list[dict], dict]:
        params: list[tuple[str, str]] = [("limit", str(limit)), ("offset", str(offset))]
        for f in filters or []:
            params.append(("filter", f))
        if expand:
            params.append(("expand", ",".join(expand)))
        if order_by:
            params.append(("orderBy", order_by))

        body = self._request(path, params=params)
        if "elements" not in body:
            logger.warning(
                "Clover response for %s missing 'elements' key; treating as empty page",
                path,
            )
        return body.get("elements", []), body

    def _request(self, path: str, params: list[tuple[str, str]]) -> dict:
        url = self._url(path)
        last_exc: Exception | None = None

        for attempt in range(self.max_retries):
            response = self.session.get(
                url, headers=self._headers(), params=params, timeout=self.timeout
            )

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    return {}

            if response.status_code == 401:
                raise CloverAuthError(
                    f"Clover token rejected (401) calling {path}",
                    status_code=401,
                    body=response.text,
                )
            if response.status_code == 403:
                raise CloverPermissionError(
                    f"Clover token lacks read permission (403) for resource '{path}'",
                    status_code=403,
                    body=response.text,
                )
            if response.status_code == 400:
                raise CloverBadRequestError(
                    f"Clover rejected request params (400) calling {path}",
                    status_code=400,
                    body=response.text,
                )

            if response.status_code == 429:
                wait = self._retry_after_seconds(response)
                if wait is None:
                    wait = min(_backoff_seconds(attempt), self.max_backoff_seconds)
                last_exc = CloverRetryExhaustedError(
                    f"Clover rate limit (429) retries exhausted calling {path}",
                    status_code=429,
                    body=response.text,
                )
                self.sleep_fn(wait)
                continue

            if response.status_code >= 500:
                wait = self._retry_after_seconds(response)
                if wait is None:
                    wait = min(_backoff_seconds(attempt), self.max_backoff_seconds)
                last_exc = CloverServerError(
                    f"Clover server error ({response.status_code}) retries exhausted calling {path}",
                    status_code=response.status_code,
                    body=response.text,
                )
                self.sleep_fn(wait)
                continue

            # Any other unexpected status: don't retry, surface immediately.
            raise CloverAPIError(
                f"Unexpected Clover response ({response.status_code}) calling {path}",
                status_code=response.status_code,
                body=response.text,
            )

        assert last_exc is not None
        raise last_exc

    def paginate(
        self,
        path: str,
        since_millis: int | None = None,
        ts_field: str = "modifiedTime",
        expand: Iterable[str] | None = None,
        order_by: str | None = "modifiedTime",
        limit: int = 1000,
        page_callback: Callable[[list[dict]], None] | None = None,
    ) -> Iterator[list[dict]]:
        offset = 0
        filters = [f"{ts_field}>={since_millis}"] if since_millis is not None else None

        while True:
            page, _ = self.get_page(
                path,
                limit=limit,
                offset=offset,
                filters=filters,
                expand=expand,
                order_by=order_by,
            )
            if page_callback is not None:
                page_callback(page)
            yield page

            if len(page) < limit:
                break
            offset += limit
