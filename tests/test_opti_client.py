from __future__ import annotations

import responses

from opti_connector.client import (
    OptiAuthError,
    OptiClient,
    OptiServerError,
    OptiValidationError,
)

BASE_URL = "https://grocers-app.optiu.ai"
LOGIN_URL = f"{BASE_URL}/api/auth/login"
TENANTS_URL = f"{BASE_URL}/api/tenants"


def make_client(sleep_calls: list[float] | None = None) -> OptiClient:
    sleeps = sleep_calls if sleep_calls is not None else []
    return OptiClient(
        BASE_URL, "ops@acme.com", "pw", sector="grocers", sleep_fn=lambda s: sleeps.append(s)
    )


@responses.activate
def test_login_sends_correct_body_and_sets_cookie():
    responses.add(
        responses.POST,
        LOGIN_URL,
        json={"id": "user_1", "email": "ops@acme.com"},
        status=200,
        headers={"Set-Cookie": "opti_session=abc123; Path=/"},
    )
    client = make_client()
    client.login()
    sent = responses.calls[0].request
    import json as _json

    assert _json.loads(sent.body) == {"email": "ops@acme.com", "password": "pw", "sector": "grocers"}
    assert client.session.cookies.get("opti_session") == "abc123"


@responses.activate
def test_discover_tenant_picks_matching_sector():
    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    responses.add(
        responses.GET,
        TENANTS_URL,
        json=[{"id": "tenant_abc123", "sector": "grocers"}],
        status=200,
    )
    client = make_client()
    tenant_id = client.discover_tenant()
    assert tenant_id == "tenant_abc123"
    assert client.tenant_id == "tenant_abc123"


@responses.activate
def test_upload_workbook_sends_mode_param_and_file(tmp_path):
    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    upload_url = f"{BASE_URL}/api/tenants/tenant_abc123/upload"
    responses.add(responses.POST, upload_url, json={"id": "job_124", "status": "RUNNING"}, status=200)

    client = make_client()
    client.set_tenant_id("tenant_abc123")

    workbook_path = tmp_path / "wb.xlsx"
    workbook_path.write_bytes(b"fake-xlsx-bytes")

    result = client.upload_workbook(str(workbook_path), mode="incremental")
    assert result["id"] == "job_124"

    sent = responses.calls[-1].request
    assert "mode=incremental" in sent.url
    body = sent.body.read() if hasattr(sent.body, "read") else sent.body
    assert b"fake-xlsx-bytes" in body


@responses.activate
def test_poll_job_loops_until_complete():
    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    job_url = f"{BASE_URL}/api/jobs/job_124"
    responses.add(responses.GET, job_url, json={"id": "job_124", "status": "RUNNING"}, status=200)
    responses.add(responses.GET, job_url, json={"id": "job_124", "status": "RUNNING"}, status=200)
    responses.add(responses.GET, job_url, json={"id": "job_124", "status": "COMPLETE"}, status=200)

    sleeps: list[float] = []
    client = make_client(sleeps)
    result = client.poll_job("job_124", interval=2.0)
    assert result["status"] == "COMPLETE"
    assert sleeps == [2.0, 2.0]


@responses.activate
def test_trigger_rethink_posts_empty_body():
    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    client = make_client()
    client.set_tenant_id("tenant_abc123")
    rethink_url = f"{BASE_URL}/api/tenants/tenant_abc123/brain/rethink"
    responses.add(responses.POST, rethink_url, json={"status": "running", "decisions_count": 1250}, status=200)

    result = client.trigger_rethink()
    assert result["decisions_count"] == 1250


@responses.activate
def test_get_decisions_passes_query_params():
    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    client = make_client()
    client.set_tenant_id("tenant_abc123")
    decisions_url = f"{BASE_URL}/api/tenants/tenant_abc123/decisions"
    responses.add(
        responses.GET,
        decisions_url,
        json=[{"decision_id": "dec_1", "decision_type": "SET_MARKDOWN"}],
        status=200,
    )

    result = client.get_decisions("SHELF_MARKDOWN_01", site_id="ST_1", limit=200)
    assert result == [{"decision_id": "dec_1", "decision_type": "SET_MARKDOWN"}]
    sent_url = responses.calls[-1].request.url
    assert "aom_id=SHELF_MARKDOWN_01" in sent_url
    assert "site_id=ST_1" in sent_url
    assert "limit=200" in sent_url


@responses.activate
def test_one_self_healing_401_retry_then_success():
    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    responses.add(responses.GET, TENANTS_URL, status=401)
    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    responses.add(responses.GET, TENANTS_URL, json=[{"id": "tenant_abc123", "sector": "grocers"}], status=200)

    client = make_client()
    tenant_id = client.discover_tenant()
    assert tenant_id == "tenant_abc123"
    assert len(responses.calls) == 4


@responses.activate
def test_second_consecutive_401_raises_auth_error():
    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    responses.add(responses.GET, TENANTS_URL, status=401)
    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    responses.add(responses.GET, TENANTS_URL, status=401)

    client = make_client()
    try:
        client.discover_tenant()
        assert False, "expected OptiAuthError"
    except OptiAuthError:
        pass


@responses.activate
def test_cold_start_503_retried_once_then_succeeds():
    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    responses.add(responses.GET, TENANTS_URL, status=503)
    responses.add(responses.GET, TENANTS_URL, json=[{"id": "tenant_abc123", "sector": "grocers"}], status=200)

    client = make_client()
    tenant_id = client.discover_tenant()
    assert tenant_id == "tenant_abc123"
    assert len(responses.calls) == 3


@responses.activate
def test_cold_start_503_exhausted_raises_server_error():
    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    responses.add(responses.GET, TENANTS_URL, status=503)
    responses.add(responses.GET, TENANTS_URL, status=503)

    client = make_client()
    try:
        client.discover_tenant()
        assert False, "expected OptiServerError"
    except OptiServerError:
        pass


@responses.activate
def test_422_raises_validation_error_with_body():
    responses.add(responses.POST, LOGIN_URL, json={"id": "user_1"}, status=200)
    client = make_client()
    client.set_tenant_id("tenant_abc123")
    upload_url = f"{BASE_URL}/api/tenants/tenant_abc123/upload"
    responses.add(
        responses.POST,
        upload_url,
        json={"validation_errors": ["missing column: store_id"]},
        status=422,
    )

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
        tmp.write(b"x")
        tmp.flush()
        try:
            client.upload_workbook(tmp.name)
            assert False, "expected OptiValidationError"
        except OptiValidationError as exc:
            assert exc.validation_errors == ["missing column: store_id"]
