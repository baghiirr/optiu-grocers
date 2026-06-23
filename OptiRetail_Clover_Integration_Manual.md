# OptiRetail × Clover — Technical Integration Manual (No-App / Direct API)

**Audience:** OptiU integration & data engineering
**Scope:** Connecting OptiRetail to a single merchant's Clover account to pull operational data **without publishing an app on the Clover App Market**
**Status:** v1.0 · June 2026 · Confidential — Internal
**Companion to:** *OptiU Clover Go-To-Market Strategy* (Prong 1 — Land & Launch)

> ⚠️ **Verify-before-build note.** Clover's docs are the source of truth and change over time. Endpoint paths, permission labels, and the dashboard token UI below reflect Clover's published REST platform API as of this writing. Confirm exact rate-limit numbers and the current token-creation screen against the live docs (links in the Appendix) during the first integration.

---

## 1. Purpose

OptiRetail's AOMs need **read-only** access to a merchant's live store data — items, stock, orders, line items, payments, refunds, customers. This manual describes how to obtain that data through a **direct REST connection to the merchant's existing Clover account**, established during the 90-minute onboarding screen-share, with **no marketplace app, no app approval, and no device deployment**.

The whole point: **revenue is never gated on building or publishing an app.** This is the connection that lets us launch a paying customer this week. (The published App Market app is a separate, parallel track — see the GTM strategy.)

---

## 2. Choose the connection method

There are three ways to get a merchant's data without a listed marketplace app. Default to **Option A**.

| # | Method | How it works | Pros | Cons | Use when |
|---|--------|--------------|------|------|----------|
| **A** ✅ | **Merchant-generated API token** | Merchant creates a read-only API token in their Clover Dashboard and shares it with us. We call the REST API directly with that token. | No app, no approval. Token is long-lived. Merchant controls scope and can revoke. Fastest path to go-live. | Manual token creation; no webhooks (poll instead); token is a shared secret we must store securely. | **Almost always — this is the launch path.** |
| **B** | **Private OAuth app (unlisted)** | Create an app in the Clover Developer Dashboard but never publish it; install only on this merchant via a distribution/install link. | Enables webhooks; OAuth refresh-token model; cleaner for many merchants later. | Requires a dev account + app config; OAuth tokens expire and need refresh handling; more setup than a token. | We want webhooks or are standardizing ahead of the App Market build. |
| **C** | **CSV / report export** | Merchant exports sales/inventory reports from the Clover Dashboard; we ingest the files. | Zero API work; works even if API access is restricted. | Manual, batchy, lossy, no real-time, no line-item granularity guarantees. | Temporary fallback if a merchant cannot or will not issue a token. |

**Decision:** Use **A** to launch. Keep **B** in your back pocket if a merchant needs webhooks or once we standardize toward the App Market app. Use **C** only as a stopgap.

---

## 3. How the no-app token model works (Option A)

```
Merchant's Clover Dashboard                 OptiU
┌──────────────────────────┐                ┌─────────────────────────────┐
│ Creates read-only        │   token + mId  │ Scheduled poller            │
│ API token (scoped perms) │ ─────────────► │  - initial full backfill    │
│                          │                │  - incremental sync (poll)  │
└──────────────────────────┘                │  - normalize → OptiRetail DB│
        Clover REST API  ◄──── Bearer token ─┤  - AOMs run on the data     │
        (api.clover.com)                     └─────────────────────────────┘
```

- **Read-only.** We request only `read` permissions. We never write to the merchant's store.
- **Polling, not push.** Without an app you cannot subscribe to Clover webhooks, so we **poll on a schedule** using `modifiedTime` filters for incremental sync (Section 9).
- **Single-merchant scope.** The token is bound to one merchant ID (`mId`). Each customer = one token + one `mId`.

---

## 4. Prerequisites

- The merchant has an active Clover account and can log in to their Clover Dashboard (or the Clover Web Dashboard) with an **owner/admin** role (required to create API tokens).
- We know the merchant's **region** (US / EU / LatAm) — this determines the API base URL.
- A secure secret store on our side (e.g. AWS Secrets Manager / Vault) to hold the token. **Never** commit tokens to source control or paste them into tickets.

---

## 5. Step-by-step: merchant generates a read-only API token

Walk the merchant through this live during onboarding (screen-share). Steps reflect the current Clover Dashboard; labels may vary slightly by region/account.

1. Merchant logs in to their **Clover Dashboard** as an account owner.
2. Go to **Account & Setup → API Tokens** (in some accounts: **Setup → API Tokens**).
3. Click **Create New Token**.
4. Enter a recognizable **token name**, e.g. `OptiRetail – read only`.
5. In the permissions list, grant **Read** on the resources OptiRetail needs and **leave all Write boxes unchecked**:
   - Read **Inventory** (items, categories, modifiers, tags, item stock)
   - Read **Orders**
   - Read **Payments** (payments, refunds, credits)
   - Read **Customers** *(only if the merchant runs loyalty / opts in — see PII note in Section 14)*
   - Read **Employees** *(optional — enables labor/shift signals)*
   - Read **Merchant** (merchant profile, address, business hours)
6. **Create** the token and copy the generated token string.
7. Merchant shares the token with OptiU through a **secure channel** (our onboarding secrets link — not email/Slack/plain text). We immediately store it in our secret store.
8. Capture the merchant's **Merchant ID (`mId`)** — visible in the Dashboard under **Account & Setup → About Your Business**, or in the Dashboard URL.

> 🔐 The merchant can **revoke** this token at any time from the same screen. Treat it as a long-lived secret on our side and rotate on customer offboarding.

---

## 6. Environments & base URLs

Pick the base URL by the merchant's region. All paths in this manual are relative to one of these.

| Environment | Base URL |
|-------------|----------|
| Production — North America | `https://api.clover.com` |
| Production — Europe | `https://api.eu.clover.com` |
| Production — Latin America | `https://api.la.clover.com` |
| Sandbox (development/testing) | `https://apisandbox.dev.clover.com` |

> The Dashboard (where merchants create tokens) lives on a separate web host (`www.clover.com` / regional equivalents, and `sandbox.dev.clover.com` for test). API calls always go to the `api.*` hosts above.

Always develop and certify the connector against **sandbox** with a test merchant before pointing at a production token.

---

## 7. Authentication

Every request includes the merchant API token as a Bearer credential:

```
Authorization: Bearer <MERCHANT_API_TOKEN>
Accept: application/json
```

- The token implicitly scopes calls to its merchant, but you still address the merchant by `mId` in the path: `/v3/merchants/{mId}/...`.
- A `401 Unauthorized` means a bad/expired/revoked token. A `403 Forbidden` means the token lacks the **read permission** for that resource (go back to Section 5 and have the merchant grant it).

---

## 8. Core API concepts

**Collection envelope.** List endpoints return an `elements` array plus an `href`:

```json
{
  "elements": [ { "...": "..." } ],
  "href": "https://api.clover.com/v3/merchants/ABC/items?offset=0&limit=100"
}
```

**Pagination.** Use `limit` and `offset`. `limit` max is **1000** (default 100). Page until a returned page has fewer than `limit` elements.

```
GET /v3/merchants/{mId}/orders?limit=1000&offset=0
GET /v3/merchants/{mId}/orders?limit=1000&offset=1000
```

**Filtering (the key to incremental sync).** Use `filter` with epoch-millisecond timestamps:

```
GET /v3/merchants/{mId}/orders?filter=modifiedTime>=1718000000000
GET /v3/merchants/{mId}/items?filter=modifiedTime>=<lastSyncMillis>
```

Combine filters by repeating the param (`filter=createdTime>=...&filter=createdTime<=...`). All Clover timestamps are **epoch milliseconds (UTC)**.

**Expansion.** Use `expand` to inline related objects in one call (comma-separated):

```
GET /v3/merchants/{mId}/orders?expand=lineItems,payments
GET /v3/merchants/{mId}/items?expand=categories,itemStock,tags
```

**Ordering.** `orderBy=modifiedTime` (append ` DESC` for descending).

**Money is in cents.** All amounts (item `price`, order `total`, payment `amount`) are **integers in the merchant's currency minor unit** (e.g. `price: 499` = $4.99). Divide by 100 for display; keep cents internally.

---

## 9. Endpoint reference (read-only, mapped to OptiRetail needs)

> All paths are `GET` and prefixed by the regional base URL + `/v3/merchants/{mId}`.

### Inventory & assortment
| Endpoint | Returns | Useful params |
|----------|---------|---------------|
| `/items` | Inventory items: name, `price`, `priceType`, `cost`, `sku`, `code`, hidden/available flags | `expand=categories,itemStock,tags,modifierGroups`, `filter=modifiedTime>=`, `limit`, `offset` |
| `/items/{itemId}` | Single item detail | `expand=...` |
| `/categories` | Category hierarchy (department/aisle structure) | `limit`, `offset` |
| `/item_stocks` | Per-item stock `quantity` (for stock-tracked items) | `filter=modifiedTime>=` |
| `/tags` (labels) | Item tags/labels (e.g. "perishable", "local") | — |
| `/modifier_groups` | Modifier groups + modifiers | `expand=modifiers` |
| `/attributes`, `/options` | Item variants/options (size, flavor) | — |

### Sales: orders, line items, payments
| Endpoint | Returns | Useful params |
|----------|---------|---------------|
| `/orders` | Orders: `total`, `state`, `createdTime`, `modifiedTime`, employee, device | `expand=lineItems,payments,lineItems.modifications`, `filter=modifiedTime>=`, `orderBy=modifiedTime` |
| `/orders/{orderId}` | Single order with full detail | `expand=lineItems,payments` |
| `/orders/{orderId}/line_items` | Line items: item ref, `price`, qty, `unitQty`, discounts | — |
| `/payments` | Payments: `amount`, `tipAmount`, `taxAmount`, tender, result, `createdTime` | `filter=createdTime>=`, `expand=order` |
| `/orders/{orderId}/payments` | Payments for one order | — |
| `/refunds` | Refunds | `filter=createdTime>=` |
| `/credits`, `/manual_refunds` | Non-payment credits / manual refunds | `filter=createdTime>=` |

### Operations & context
| Endpoint | Returns | Notes |
|----------|---------|-------|
| `/` (merchant root: `/v3/merchants/{mId}`) | Merchant profile, currency, timezone, address | Pull once at connect; cache. |
| `/employees` | Employees/roles | For labor & shift signals (optional perm). |
| `/devices` | Registered Clover devices | Multi-register stores. |
| `/cash_events` | Cash drawer events | Shrink / cash-handling signals. |
| `/customers` | Customers + (with expand) emails, phones, addresses | **PII — only with explicit opt-in.** `expand=emailAddresses,phoneNumbers` |
| `/order_types`, `/tenders` | Order types & tender types | Reference data. |

### Data → AOM cluster mapping
| OptiRetail cluster | Primary Clover data |
|--------------------|---------------------|
| **Fresh & Inventory Guardian** (Shrink Watchtower, Auto-Replenishment, Peak-Day Forecaster) | `items`, `item_stocks`, `categories`, `tags`, `orders`+`line_items` (sell-through), `refunds` |
| **Price & Margin Discipline** (Smart Pricing, Flyer ROI, Category Profit) | `items` (`price`, `cost`), `line_items` (discounts), `orders`, `payments`, `categories` |
| **Assortment & Customer Lift** (SKU Lineup Coach, Loyalty Lift) | `items`, `categories`, `line_items`, `customers` (opt-in) |
| **Store Execution & Multi-Store** (Shift Coach, Inter-Store Balancer, Store-vs-Store) | `employees`, `devices`, `orders` (traffic by time), `cash_events`, merchant root |

> **Cost/margin caveat:** Clover item `cost` is only as good as what the merchant maintains. Flag at onboarding; where cost is missing, the Price & Margin AOMs degrade gracefully or rely on supplier-cost CSV.

---

## 10. Sync architecture

Because there are no webhooks on the no-app path, run a **two-phase poll**:

**Phase 1 — Initial backfill (at connect).**
- Pull reference data once: merchant root, `categories`, `tags`, `modifier_groups`, `employees`, `devices`.
- Pull full `items` and `item_stocks`.
- Backfill `orders` (+ `expand=lineItems,payments`) and `payments`/`refunds` over the lookback window the AOMs need (typically 13–18 months for seasonality). Page with `limit=1000`.

**Phase 2 — Incremental sync (scheduled).**
- Persist a per-resource high-water mark (`lastModifiedMillis`).
- On each run: `GET .../{resource}?filter=modifiedTime>=<lastModifiedMillis>&limit=1000&orderBy=modifiedTime` and page forward.
- Advance the high-water mark to the max `modifiedTime` seen (subtract a small safety overlap, e.g. 60s, to avoid edge misses).

**Recommended cadence (matches OptiRetail tiers):**
| Tier | Orders/payments | Inventory/stock | Reference data |
|------|-----------------|------------------|----------------|
| Starter (weekly) | every 4–6 h | daily | weekly |
| Operator / Growth (daily) | every 30–60 min | every 1–2 h | daily |
| Network (daily + peaks) | every 15 min during open hours | hourly | daily |

Keep polling **inside store open hours** plus an end-of-day reconciliation pull. Always reconcile a full day once daily (re-pull yesterday by `modifiedTime`) to catch late edits/voids.

---

## 11. Sample requests

**Merchant profile (run once at connect):**
```bash
curl -s https://api.clover.com/v3/merchants/$MID \
  -H "Authorization: Bearer $TOKEN" -H "Accept: application/json"
```

**Items with categories, stock, and tags:**
```bash
curl -s "https://api.clover.com/v3/merchants/$MID/items?expand=categories,itemStock,tags&limit=1000&offset=0" \
  -H "Authorization: Bearer $TOKEN"
```

**Incremental orders since last sync, with line items and payments:**
```bash
curl -s "https://api.clover.com/v3/merchants/$MID/orders?filter=modifiedTime>=$LAST_SYNC_MS&expand=lineItems,payments&orderBy=modifiedTime&limit=1000" \
  -H "Authorization: Bearer $TOKEN"
```

**Payments since a timestamp:**
```bash
curl -s "https://api.clover.com/v3/merchants/$MID/payments?filter=createdTime>=$SINCE_MS&limit=1000" \
  -H "Authorization: Bearer $TOKEN"
```

**Trimmed sample response (`/items`):**
```json
{
  "elements": [
    {
      "id": "0X1Y2Z",
      "name": "Organic Whole Milk 1gal",
      "price": 499,
      "priceType": "FIXED",
      "cost": 312,
      "sku": "MILK-WH-1G",
      "categories": { "elements": [ { "id": "CAT1", "name": "Dairy" } ] },
      "itemStock": { "quantity": 24 },
      "modifiedTime": 1718050000000
    }
  ],
  "href": "https://api.clover.com/v3/merchants/$MID/items?offset=0&limit=1000"
}
```

---

## 12. Reference poller (pseudocode)

```python
import time, requests

BASE  = "https://api.clover.com"            # region-specific
MID   = secret("clover.mid")
TOKEN = secret("clover.token")              # read-only merchant token
H = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}

def get_page(path, params):
    for attempt in range(6):
        r = requests.get(f"{BASE}{path}", headers=H, params=params, timeout=30)
        if r.status_code == 429:                      # rate limited
            wait = int(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(wait); continue
        r.raise_for_status()
        return r.json().get("elements", [])
    raise RuntimeError("exhausted retries (429)")

def sync(resource, since_ms, expand=None, ts="modifiedTime"):
    offset, out = 0, []
    while True:
        params = {"limit": 1000, "offset": offset,
                  "filter": f"{ts}>={since_ms}", "orderBy": ts}
        if expand: params["expand"] = expand
        page = get_page(f"/v3/merchants/{MID}/{resource}", params)
        out += page
        if len(page) < 1000: break
        offset += 1000
    return out

# incremental example
orders = sync("orders", high_water_mark("orders"), expand="lineItems,payments")
```

Tune concurrency low (a few requests in flight), always honor `Retry-After`, and checkpoint the high-water mark only after a page is durably persisted.

---

## 13. Rate limits & error handling

Clover enforces per-token/per-merchant **rate limits** and returns **`429 Too Many Requests`** when exceeded. Handle it defensively:

- **Honor `Retry-After`** when present; otherwise back off exponentially (e.g. 1s, 2s, 4s, 8s…).
- Keep request concurrency modest; prefer `expand` and `limit=1000` to reduce call volume rather than firing many small requests.
- Spread large backfills over time; don't hammer at connect.

| Status | Meaning | Action |
|--------|---------|--------|
| `200` | OK | Process `elements`; paginate. |
| `401` | Bad/expired/revoked token | Alert; re-request token from merchant. |
| `403` | Token lacks read permission for resource | Have merchant grant the read scope (Section 5). |
| `400` | Bad filter/param | Check `filter`/timestamp formatting (epoch ms). |
| `429` | Rate limited | Back off; honor `Retry-After`. |
| `5xx` | Clover-side error | Retry with backoff; page is idempotent. |

> Confirm the current documented rate-limit numbers in Clover's "API usage and rate limits" / "429 Too Many Requests" docs (Appendix) and bake the real ceiling into your throttler.

---

## 14. Security, privacy & compliance

- **Least privilege:** request only the **read** scopes the active AOM tier uses. Don't ask for Customers/PII unless loyalty features are in scope and the merchant opts in.
- **Token handling:** store in a managed secret store, encrypted at rest; never log the token; never place it in URLs, tickets, or chat. Rotate/revoke on offboarding.
- **PII minimization:** `/customers` data is personal data. Pull it only with explicit merchant opt-in, store the minimum needed, and honor deletion requests. Hash/tokenize identifiers where the AOMs don't need raw values.
- **Read-only guarantee:** the connector issues only `GET`s. Document this; it's a trust point with the merchant (and a selling point — we cannot alter their store).
- **Transport:** TLS only (the API is HTTPS); reject non-TLS.
- **Auditability:** log every sync run (resource, window, row counts, status) without logging payload PII or the token.

---

## 15. Onboarding checklist (maps to the 90-minute screen-share)

- [ ] Confirm merchant region → choose base URL.
- [ ] Owner logs in to Clover Dashboard.
- [ ] Create read-only API token named `OptiRetail – read only` (Section 5 scopes).
- [ ] Capture token via secure link → store in secret store.
- [ ] Capture `mId`.
- [ ] Smoke test: `GET /v3/merchants/{mId}` returns 200 with merchant profile.
- [ ] Verify read scopes: one successful call each to `items`, `orders?expand=lineItems`, `payments`.
- [ ] Kick off Phase-1 backfill; confirm row counts look sane vs. the merchant's expectations.
- [ ] Schedule Phase-2 incremental sync at the tier cadence.
- [ ] Hand off to the shadow-run / calibration step (Days 6–14 of onboarding).

---

## 16. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `401` on every call | Token wrong, revoked, or pasted with whitespace | Re-copy token; confirm not revoked; check `Bearer ` prefix. |
| `403` on a specific resource | That read permission wasn't granted | Merchant edits the token and checks the read box. |
| Empty `elements` on incremental pull | `modifiedTime` high-water mark too recent / clock skew | Re-pull with a wider window; add 60s overlap; confirm epoch **ms** (not seconds). |
| Missing `cost` / margins look wrong | Merchant hasn't maintained item cost in Clover | Collect supplier-cost CSV at onboarding; flag to AOM. |
| Stock always null | Item not stock-tracked in Clover | Use `item_stocks`; confirm tracking enabled per item. |
| Pagination seems to stop early | Page size assumption wrong | Page until a page returns `< limit` rows; don't stop on first short page mid-run. |
| Frequent `429`s | Concurrency/back-to-back backfill | Lower concurrency; honor `Retry-After`; use `expand` + `limit=1000`. |
| Need real-time push | Webhooks require an app | Not available on no-app path; tighten poll cadence, or move to the private-app (Option B) / App Market track. |

---

## 17. When to graduate off the no-app path

The token approach is ideal for launching one (or a handful of) merchants fast. Move to a **private OAuth app (Option B)** or the **App Market app** when you want: webhooks/near-real-time, self-serve onboarding at scale, OAuth-managed tokens, or Clover-managed billing. The data model and endpoints in this manual are identical across all three — only **auth and provisioning** change — so connector code written here is reused directly by the App Market build (Prong 2 of the GTM strategy).

---

## Appendix — Clover documentation links

- Use the Clover REST API — https://docs.clover.com/dev/docs/making-rest-api-calls
- Create merchant-specific API token — https://docs.clover.com/dev/docs/gdp-create-merchant-specific-api-token
- Merchant ID & API token for development — https://docs.clover.com/dev/docs/merchant-id-and-api-token-for-development
- Set app permissions (read/write scopes) — https://docs.clover.com/dev/docs/permissions
- OAuth & tokens FAQ — https://docs.clover.com/dev/docs/oauth-and-tokens-faqs
- API usage & rate limits — https://docs.clover.com/dev/docs/api-usage-rate-limits
- 429 Too Many Requests — https://docs.clover.com/dev/docs/429-too-many-requests
- API Reference overview — https://docs.clover.com/dev/reference/api-reference-overview

---

### Glossary
- **`mId`** — Clover Merchant ID; identifies one merchant/store.
- **API token** — long-lived, merchant-scoped credential created in the Dashboard; used as a Bearer token.
- **`elements`** — the array wrapper on every list response.
- **`expand`** — query param to inline related objects in one call.
- **`modifiedTime` / `createdTime`** — epoch-millisecond timestamps used for incremental sync.
- **AOM** — Autonomous Optimization Model (an OptiRetail engine).

*Confidential — © OptiU 2026. Verify all Clover-specific details against current Clover developer documentation during implementation.*
