from __future__ import annotations

import responses

from clover_connector.client import (
    CloverAuthError,
    CloverBadRequestError,
    CloverClient,
    CloverPermissionError,
    CloverRetryExhaustedError,
    CloverServerError,
)

from .conftest import load_fixture, make_n_items

BASE_URL = "https://apisandbox.dev.clover.com"
MID = "TESTMID"
ITEMS_URL = f"{BASE_URL}/v3/merchants/{MID}/items"


def make_client(sleep_calls: list[float] | None = None, max_retries: int = 6) -> CloverClient:
    sleeps = sleep_calls if sleep_calls is not None else []
    return CloverClient(
        BASE_URL,
        MID,
        "test-token",
        sleep_fn=lambda s: sleeps.append(s),
        max_retries=max_retries,
    )


@responses.activate
def test_auth_header_sent():
    responses.add(responses.GET, ITEMS_URL, json=load_fixture("items_page.json"), status=200)
    client = make_client()
    client.get_page("/items")
    sent = responses.calls[0].request
    assert sent.headers["Authorization"] == "Bearer test-token"
    assert sent.headers["Accept"] == "application/json"


@responses.activate
def test_401_raises_auth_error_no_retry():
    responses.add(responses.GET, ITEMS_URL, status=401, body="unauthorized")
    client = make_client()
    try:
        client.get_page("/items")
        assert False, "expected CloverAuthError"
    except CloverAuthError:
        pass
    assert len(responses.calls) == 1


@responses.activate
def test_403_raises_permission_error_with_resource_name():
    responses.add(responses.GET, ITEMS_URL, status=403, body="forbidden")
    client = make_client()
    try:
        client.get_page("/items")
        assert False, "expected CloverPermissionError"
    except CloverPermissionError as exc:
        assert "items" in str(exc)
    assert len(responses.calls) == 1


@responses.activate
def test_400_raises_bad_request_no_retry():
    responses.add(responses.GET, ITEMS_URL, status=400, body="bad filter")
    client = make_client()
    try:
        client.get_page("/items")
        assert False, "expected CloverBadRequestError"
    except CloverBadRequestError:
        pass
    assert len(responses.calls) == 1


@responses.activate
def test_429_with_retry_after_honored():
    responses.add(responses.GET, ITEMS_URL, status=429, headers={"Retry-After": "5"})
    responses.add(responses.GET, ITEMS_URL, json=load_fixture("items_page.json"), status=200)
    sleeps: list[float] = []
    client = make_client(sleeps)
    elements, _ = client.get_page("/items")
    assert len(elements) == 1
    assert sleeps == [5.0]
    assert len(responses.calls) == 2


@responses.activate
def test_429_without_retry_after_exponential_backoff():
    responses.add(responses.GET, ITEMS_URL, status=429)
    responses.add(responses.GET, ITEMS_URL, status=429)
    responses.add(responses.GET, ITEMS_URL, json=load_fixture("items_page.json"), status=200)
    sleeps: list[float] = []
    client = make_client(sleeps)
    elements, _ = client.get_page("/items")
    assert len(elements) == 1
    assert sleeps == [1.0, 2.0]
    assert len(responses.calls) == 3


@responses.activate
def test_429_retry_exhaustion_raises():
    for _ in range(6):
        responses.add(responses.GET, ITEMS_URL, status=429)
    client = make_client(max_retries=6)
    try:
        client.get_page("/items")
        assert False, "expected CloverRetryExhaustedError"
    except CloverRetryExhaustedError:
        pass
    assert len(responses.calls) == 6


@responses.activate
def test_5xx_retry_then_succeed():
    responses.add(responses.GET, ITEMS_URL, status=503)
    responses.add(responses.GET, ITEMS_URL, json=load_fixture("items_page.json"), status=200)
    client = make_client()
    elements, _ = client.get_page("/items")
    assert len(elements) == 1
    assert len(responses.calls) == 2


@responses.activate
def test_5xx_retry_exhaustion_raises():
    for _ in range(6):
        responses.add(responses.GET, ITEMS_URL, status=503)
    client = make_client(max_retries=6)
    try:
        client.get_page("/items")
        assert False, "expected CloverServerError"
    except CloverServerError:
        pass
    assert len(responses.calls) == 6


@responses.activate
def test_pagination_full_page_continues():
    responses.add(responses.GET, ITEMS_URL, json=make_n_items(1000), status=200)
    responses.add(responses.GET, ITEMS_URL, json=make_n_items(3), status=200)
    client = make_client()
    pages = list(client.paginate("/items"))
    assert len(pages) == 2
    assert len(pages[0]) == 1000
    assert len(pages[1]) == 3
    assert len(responses.calls) == 2


@responses.activate
def test_pagination_short_page_stops():
    responses.add(responses.GET, ITEMS_URL, json=load_fixture("items_page_short.json"), status=200)
    client = make_client()
    pages = list(client.paginate("/items"))
    assert len(pages) == 1
    assert len(responses.calls) == 1


@responses.activate
def test_pagination_empty_elements_stops_immediately():
    responses.add(responses.GET, ITEMS_URL, json=load_fixture("items_empty.json"), status=200)
    client = make_client()
    pages = list(client.paginate("/items"))
    assert len(pages) == 1
    assert pages[0] == []
    assert len(responses.calls) == 1


@responses.activate
def test_pagination_malformed_missing_elements_key(caplog):
    responses.add(responses.GET, ITEMS_URL, json=load_fixture("items_malformed.json"), status=200)
    client = make_client()
    pages = list(client.paginate("/items"))
    assert len(pages) == 1
    assert pages[0] == []
    assert len(responses.calls) == 1


@responses.activate
def test_filter_param_built_with_epoch_ms():
    responses.add(responses.GET, ITEMS_URL, json=load_fixture("items_empty.json"), status=200)
    client = make_client()
    client.get_page("/items", filters=["modifiedTime>=1718000000000"])
    sent_url = responses.calls[0].request.url
    assert "filter=modifiedTime%3E%3D1718000000000" in sent_url


@responses.activate
def test_expand_param_comma_joined():
    responses.add(responses.GET, ITEMS_URL, json=load_fixture("items_empty.json"), status=200)
    client = make_client()
    client.get_page("/items", expand=["categories", "itemStock", "tags"])
    sent_url = responses.calls[0].request.url
    assert "expand=categories%2CitemStock%2Ctags" in sent_url


@responses.activate
def test_order_by_param():
    responses.add(responses.GET, ITEMS_URL, json=load_fixture("items_empty.json"), status=200)
    client = make_client()
    client.get_page("/items", order_by="modifiedTime")
    sent_url = responses.calls[0].request.url
    assert "orderBy=modifiedTime" in sent_url


@responses.activate
def test_base_url_region_selection():
    eu_url = "https://api.eu.clover.com/v3/merchants/TESTMID/items"
    responses.add(responses.GET, eu_url, json=load_fixture("items_empty.json"), status=200)
    client = CloverClient("https://api.eu.clover.com", MID, "test-token")
    client.get_page("/items")
    assert len(responses.calls) == 1
