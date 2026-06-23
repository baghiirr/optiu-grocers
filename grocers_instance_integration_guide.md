# OptiGrocer — Instance Integration Guide (Two-Way Communication)

**Companion to** [`grocers_data_integration_spec.md`](./grocers_data_integration_spec.md) (which
defines *what* data goes in and *what* decisions come out). **This** document is the *how*: the
exact HTTP flow to load data into a freshly-created, **empty** customer instance running on Azure,
trigger the engine, and pull decisions back — i.e. how to establish **two-way communication** with
OptiGrocer.

> **The whole loop:** `authenticate → discover the instance → download the template → upload &
> review data → trigger the Brain → poll → pull decisions/KPIs/plan → (apply back to your POS) →
> repeat on a schedule.`

---

## 0. Prerequisites

| You need | Example | Notes |
|---|---|---|
| **Instance URL** | `https://grocers-app.optiu.ai` | The per-sector Azure app. The pattern is `https://<sector>-app.optiu.ai`. **All `/api/*` calls go to this same host** (UI + API are same-origin on the Azure container — no separate API host, no CORS). |
| **Credentials** | `ops@acme-grocers.com` / `••••••` | The login provisioned for your instance (the OptiU team or your admin sets these). |
| **`tenant_id`** | `tenant_abc123` | Identifies your instance; discovered via `GET /api/tenants` after login. |
| **The filled workbook** | `acme_grocers.xlsx` | The `INP_*` template (one sheet per table) populated from your POS per the spec. |

> **Routing note:** `https://grocers-app.optiu.ai/api/...` → Cloudflare reverse-proxy → the
> `opti-grocers` Azure Container App (server: `uvicorn`). There is no Cloudflare Pages / monolith in
> the path. Always integrate against the `-app` host.

---

## 1. Authenticate (session cookie)

Auth is **cookie-based**. Log in once; reuse the `opti_session` cookie on every subsequent call.

**`POST /api/auth/login`**

```bash
BASE=https://grocers-app.optiu.ai

curl -sS -c cookies.txt -X POST "$BASE/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"ops@acme-grocers.com","password":"••••••","sector":"grocers"}'
```

Response `200`:
```json
{"id":"user_xyz","email":"ops@acme-grocers.com","business_name":"Acme Grocers",
 "sector":"grocers","account_type":"PAID_PENDING","is_admin":false}
```

The response sets:
```
Set-Cookie: opti_session=<token>; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=2592000
```
**Every later request must send `Cookie: opti_session=<token>`** (browsers do this automatically;
`curl -b cookies.txt`, or Python `requests.Session()`, manage it for you).

- Check a session: **`GET /api/auth/session`** → `200` (user) or `401`.
- Log out: **`POST /api/logout`** → `204`.

---

## 2. Discover the instance (`tenant_id`)

**`GET /api/tenants`** → the instances your login can see:

```bash
curl -sS -b cookies.txt "$BASE/api/tenants"
```
```json
[{"id":"tenant_abc123","name":"Acme Grocers","sector":"grocers","status":"ACTIVE",
  "data_upload_pending":true,"created_at":"2026-06-01T10:00:00Z"}]
```

Save `id` → this is your **`tenant_id`**. A freshly-created instance shows
`data_upload_pending: true` until the first successful load.

- One instance: `GET /api/tenants/{tenant_id}`.
- Locations (optional master): `GET /api/tenants/{tenant_id}/locations`; add one with
  `POST /api/tenants/{tenant_id}/locations` (body: `{site_id, site_name, city, state, ...}`).
  (Store rows in `INP_STORE` also seed locations on load.)

> **Path convention:** generic instance operations (ingest, jobs, brain, decisions, kpis) are
> **path-scoped**: `/api/tenants/{tenant_id}/...`. (Some enterprise sector-specific endpoints
> instead take `?tenant_id=`; for grocers use the `/api/tenants/{tenant_id}/...` form below.)

---

## 3. Get the empty data template

**`GET /api/sectors/grocers/excel-template`** → the multi-sheet `INP_*` workbook to fill.

```bash
curl -sS -b cookies.txt "$BASE/api/sectors/grocers/excel-template" -o grocers_template.xlsx
```
- Content-Type `…spreadsheetml.sheet`; one sheet per `INP_*` table.
- **2-row header** (names + description) — the validator skips both; start data on row 3.
- Fill from your POS using the field map in the spec (§3 inputs, §5 Clover mapping). Keep sheet
  names unchanged.

---

## 4. Upload the data

Three options, from most-guided to most-automated. **Option A** (review flow) is recommended for a
human-in-the-loop first load; **Option B** for simple programmatic loads; **Option C** for atomic
back-end bulk loads.

### Option A — Analyze → Review → Commit (recommended)

**4A.1 Analyze** (`mode` = `full_overwrite` | `incremental` | `closer_overwrite`):

**`POST /api/tenants/{tenant_id}/ingest/analyze?mode=full_overwrite`** (multipart, field `file`)
```bash
curl -sS -b cookies.txt -X POST \
  "$BASE/api/tenants/tenant_abc123/ingest/analyze?mode=full_overwrite" \
  -F "file=@grocers_template.xlsx"
```
Response `200` (or `422` with `validation_errors` if structure is wrong):
```json
{"session_id":"sess_xyz","mode":"full_overwrite","status":"reviewing","sector":"grocers",
 "validation_errors":[], "total_findings":45,
 "entity_order":["INP_STORE","INP_PRODUCT", "..."],
 "entities":[{"entity":"INP_STORE","n_rows":12,"n_findings":5,
   "findings":[{"finding_id":"INP_STORE:2:selling_area_sqft:outlier:1","issue_type":"outlier",
     "severity":"medium","row_index":2,"row_key":"ST_PRESTON_ROYAL","column":"selling_area_sqft",
     "value":250000,"suggested_value":12000,"recommendation":"change_value","confidence":"high"}]}],
 "stats":{"total_rows":50,"flagged_rows":20,"clean_rows":30},
 "overwrite_fee_notice":"Replacing your entire catalog is a full rebuild …"}
```

**4A.2 Review** (page through findings, optional): `GET /api/tenants/{tenant_id}/ingest/sessions/{session_id}`
and `GET …/sessions/{session_id}/next?cursor=0`.

**4A.3 Decide** per finding (`keep` | `remove` | `smooth`):

**`POST /api/tenants/{tenant_id}/ingest/sessions/{session_id}/decisions`**
```json
{"decisions":[
  {"finding_id":"INP_STORE:2:selling_area_sqft:outlier:1","action":"smooth","smoothed_value":12000},
  {"finding_id":"INP_PRODUCT:5:list_price:negative:2","action":"remove"},
  {"finding_id":"INP_STORE:3:region:typo:3","action":"keep"}
]}
```

**4A.4 Commit** (loads data + kicks off the first Brain cycle in the background):

**`POST /api/tenants/{tenant_id}/ingest/sessions/{session_id}/commit`** (body `{}`) → returns a
`JobOut` (`{id, status:"RUNNING", progress_pct, current_step, ...}`).

### Option B — One-shot upload (simple/programmatic)

**`POST /api/tenants/{tenant_id}/upload`** (multipart, field `file`) → runs validate → load →
Brain, all in the background; returns a `JobOut` immediately. Use this when you trust the data and
don't need the review UI.
```bash
curl -sS -b cookies.txt -X POST "$BASE/api/tenants/tenant_abc123/upload" \
  -F "file=@grocers_template.xlsx"
```

### Option C — Atomic back-end import (bulk / pre-built DB)

For large/automated loads, the OptiU back end can import a pre-built per-tenant DuckDB atomically
via the internal Fabric endpoint **`POST /fabric/import-tenant-db`** (body includes the
`tenant_db_path`; **requires the `OPTI_INTERNAL_TOKEN` header**, server-side only — not for public
clients). Use this for migrations / very large catalogs that would time out a multipart upload.
(Do **not** use `/fabric/import-sandbox-db` for an instance — that targets the shared sandbox, not a
tenant.)

### Upload modes
- **`full_overwrite`** — truncate + reload all `INP_*` (master + history). First load and catalog
  rebuilds. (May incur a one-time rebuild fee on paid accounts; admins exempt.)
- **`incremental`** — upsert by primary key (daily history append, inventory/price snapshots).
- **`closer_overwrite`** — replace only the tables a given closer reads.

---

## 5. Poll the load/Brain job

**`GET /api/jobs/{job_id}`** (or `GET /api/tenants/{tenant_id}/ingest/latest`):
```bash
curl -sS -b cookies.txt "$BASE/api/jobs/job_124"
```
```json
{"id":"job_124","tenant_id":"tenant_abc123","status":"RUNNING","progress_pct":60,
 "current_step":"ingestion_complete_running_aoms",
 "rows_loaded":{"INP_STORE":12,"INP_PRODUCT":245,"INP_SALES_HISTORY":89000},
 "validation_errors":null,"started_at":"...","completed_at":null}
```
Poll until `status` is `COMPLETE` (or `FAILED` — inspect `validation_errors`). Steps progress
through `validating → loading_* → ingestion_complete_running_aoms → complete`.

---

## 6. Trigger / re-trigger the Brain

The first cycle runs automatically after commit/upload. To re-run on demand (e.g. after a fresh
inventory snapshot):

**`POST /api/tenants/{tenant_id}/brain/rethink`** (body `{}`)
```json
{"thought_at":"2026-06-22T10:15:00Z","decisions_count":1250,"job_id":"job_125",
 "status":"running","message":"The Brain is re-thinking across shelf, orders, perishables and staffing."}
```
- Status: **`GET /api/tenants/{tenant_id}/brain/status`** → `{last_thought_at, is_thinking, decisions_count, runs_today, next_scheduled_at}`.
- History: **`GET /api/tenants/{tenant_id}/brain/runs`** → per-cycle `{modules, n_decisions, avg_confidence, kpi_impact_usd, decomposition[...]}`.

> The Brain also runs on its own schedule (e.g. every morning + after deliveries); you don't have to
> trigger it for routine operation.

---

## 7. Pull decisions & outputs back (the return path)

**Decisions per module** — **`GET /api/tenants/{tenant_id}/decisions?aom_id=<AOM>&site_id=<store>&limit=<n>`**
(`aom_id` ∈ `PERISHABLE_01`, `VENDOR_PO_01`, `SHELF_MARKDOWN_01`, `SHRINK_01`,
`GROCERS_RETENTION_01`, `GROCERS_LABOR_01`, `GROCERS_PLAN_01`; `limit` default 50, max 500):
```bash
curl -sS -b cookies.txt \
  "$BASE/api/tenants/tenant_abc123/decisions?aom_id=SHELF_MARKDOWN_01&site_id=ST_PRESTON_ROYAL&limit=200"
```
```json
[{"decision_id":"dec_run_shelf_markdown_01_1718835600000_0001","run_id":"run_shelf_markdown_01_1718835600000",
  "site_id":"ST_PRESTON_ROYAL","decision_type":"SET_MARKDOWN","target_id":"ITM_00000",
  "recommended_value":{"depth_pct":0.30,"from_price":2.99,"to_price":2.09,"on_hand":60,
    "remaining_life_days":3,"expected_cleared":58,"clears_by_sellby":true,"floor_relaxed":false},
  "confidence":0.76,"created_at":"2026-06-22T10:16:00Z",
  "why_summary":"Mark ITM_00000 @ ST_PRESTON_ROYAL down 30% to $2.09 — clears all 60 units in 3d left."}]
```
> A richer, grouped read (with KPIs + counterfactuals per decision) is also available via the
> grocers router: `GET /api/sandbox/grocers/decisions?closer=markdown&store_id=<store>[&tenant_id=<tid>]`.
> See spec §4.4.

**Other outputs:**
- KPI tiles: `GET /api/tenants/{tenant_id}/kpis[?site_id=<store>]` → `{title, as_of, tiles:[{id,label,value,raw_value,delta,...}]}`.
- Locations: `GET /api/tenants/{tenant_id}/sites` → `[{site_id, site_name}]`.
- Daily plan: `GET /api/tenants/{tenant_id}/plan/{site_id}` → `{target_date, plan_markdown, ...}`.

**Map each decision back to your POS** using `decision_id` (idempotency key) +
`(site_id, target_id, decision_type)` — see spec §4.3 / §5.2 (Clover write-back).

---

## 8. End-to-end (Python `requests`)

```python
import time, requests

BASE = "https://grocers-app.optiu.ai"
s = requests.Session()                      # persists the opti_session cookie

# 1) auth
s.post(f"{BASE}/api/auth/login",
       json={"email": "ops@acme-grocers.com", "password": "••••••", "sector": "grocers"}).raise_for_status()

# 2) discover instance
tid = s.get(f"{BASE}/api/tenants").json()[0]["id"]

# 3) template (first time only)
open("grocers_template.xlsx", "wb").write(
    s.get(f"{BASE}/api/sectors/grocers/excel-template").content)
#    ... fill it from your POS ...

# 4) upload (one-shot) — or use the analyze/decisions/commit flow for review
job = s.post(f"{BASE}/api/tenants/{tid}/upload",
             files={"file": open("grocers_template.xlsx", "rb")}).json()

# 5) poll
while True:
    j = s.get(f"{BASE}/api/jobs/{job['id']}").json()
    if j["status"] in ("COMPLETE", "FAILED"):
        break
    time.sleep(5)
assert j["status"] == "COMPLETE", j.get("validation_errors")

# 6) (optional) force a fresh think
s.post(f"{BASE}/api/tenants/{tid}/brain/rethink", json={})

# 7) pull decisions and apply to the POS
for aom in ("SHELF_MARKDOWN_01", "VENDOR_PO_01", "PERISHABLE_01", "SHRINK_01", "GROCERS_PLAN_01"):
    for d in s.get(f"{BASE}/api/tenants/{tid}/decisions",
                   params={"aom_id": aom, "limit": 500}).json():
        apply_to_pos(d)          # your write-back: SET_MARKDOWN → price update, PLACE_PO → PO, etc.
```

---

## 9. Steady-state operation (recommended schedule)

| Cadence | Push (incremental) | Then | Pull |
|---|---|---|---|
| **Daily, pre-open** | yesterday's `INP_SALES_HISTORY`, fresh `INP_INVENTORY` + `INP_PRICE` snapshots | `brain/rethink` (or rely on the 6 AM auto-run) | `SET_MARKDOWN`, `PLACE_PO`, spoilage flags, `DAILY_PLAN`, KPIs |
| **On delivery/receiving** | `INP_INVENTORY` (on-hand + age) | re-run PO/perishable | updated `PLACE_PO`, spoilage flags |
| **On master-data change** | `INP_PRODUCT` / `INP_VENDOR` / `INP_POLICY` (full-overwrite of those) | — | — |

Use `incremental` mode for the daily snapshots/appends and `full_overwrite` (or
`closer_overwrite`) only when replacing master data.

---

## 10. Quick reference (endpoints)

| Step | Method & path | Auth | Body / params |
|---|---|---|---|
| Login | `POST /api/auth/login` | — | `{email,password,sector}` → sets `opti_session` |
| Session | `GET /api/auth/session` | cookie | — |
| List instances | `GET /api/tenants` | cookie | — |
| Template | `GET /api/sectors/grocers/excel-template` | cookie | → `.xlsx` |
| Analyze | `POST /api/tenants/{tid}/ingest/analyze?mode=…` | cookie | multipart `file` |
| Review | `GET /api/tenants/{tid}/ingest/sessions/{sid}[/next?cursor=]` | cookie | — |
| Decisions (ETL) | `POST /api/tenants/{tid}/ingest/sessions/{sid}/decisions` | cookie | `{decisions:[…]}` |
| Commit | `POST /api/tenants/{tid}/ingest/sessions/{sid}/commit` | cookie | `{}` → `JobOut` |
| One-shot upload | `POST /api/tenants/{tid}/upload` | cookie | multipart `file` → `JobOut` |
| Atomic import | `POST /fabric/import-tenant-db` | `OPTI_INTERNAL_TOKEN` | server-side bulk only |
| Poll job | `GET /api/jobs/{job_id}` | cookie | — |
| Re-think | `POST /api/tenants/{tid}/brain/rethink` | cookie | `{}` |
| Brain status | `GET /api/tenants/{tid}/brain/status` | cookie | — |
| Decisions | `GET /api/tenants/{tid}/decisions?aom_id=&site_id=&limit=` | cookie | — |
| KPIs | `GET /api/tenants/{tid}/kpis[?site_id=]` | cookie | — |
| Daily plan | `GET /api/tenants/{tid}/plan/{site_id}` | cookie | — |

---

## 11. Errors & gotchas

- **401 on every call** → missing/expired `opti_session` cookie; re-login and resend the cookie.
- **422 from `analyze`/`upload`** → structural validation failed; read `validation_errors`
  (missing column, bad enum, unresolved FK). Fix the workbook and retry.
- **Cold start** → the Azure app scales to zero; the first request after idle can take ~20–40 s (or a
  one-time `503` during warm-up). Retry; it self-heals.
- **Sheet names** must match the `INP_*` template exactly; data starts on **row 3** (2-row header).
- **Units/currency** must match what you declared in `INP_PRODUCT` / `INP_STORE` (Clover money is in
  cents — convert).
- **Idempotency** → track applied `decision_id`s on your side so a re-pull of the same run doesn't
  double-apply price changes / POs.

---

*Generated from the live OptiGrocer codebase (auth, tenants, ingest, jobs, brain, decisions
routers). Endpoint shapes are quoted from source; treat sample IDs/values as illustrative.*
