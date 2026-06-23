# OptiGrocer — Data Integration Specification (Inputs & Outputs)

**Audience:** integration engineers wiring a POS / ERP / inventory system (e.g. **Clover**, NCR,
Square for Retail, an internal data warehouse) to the OptiGrocer decision engine.

**Scope:** the complete, field-level contract for **(A)** the data OptiGrocer needs *in* and
**(B)** the decisions it produces *out*. The companion document
[`grocers_instance_integration_guide.md`](./grocers_instance_integration_guide.md) shows the
**HTTP wiring** (auth, upload, run, pull) against a live instance — read both together to
establish two-way communication.

> OptiGrocer is **POS-agnostic.** It ingests a small set of normalized tables (`INP_*`) and emits
> normalized decisions (`OUT_DECISION`). Clover (or any POS) is mapped *to* those tables on the way
> in and *from* the decisions on the way out. Clover-specific mapping hints are called out in
> **§5**; everything else is source-system-neutral.

---

## 1. Architecture at a glance

OptiGrocer runs **7 "closers"** (decision modules). Each reads a subset of the input tables and
writes decisions to a shared output schema.

| Closer (key) | Engine `aom_id` | Decides | Status |
|---|---|---|---|
| Perishable Demand Lead (`perishable`) | `PERISHABLE_01` | spoilage / stockout risk flags | live |
| Vendor PO Lead (`vendor_po`) | `VENDOR_PO_01` | what/when/how-much to order | live |
| Shelf-Price / Markdown Lead (`markdown`) | `SHELF_MARKDOWN_01` | markdown depth on at-risk stock | live |
| Shrink Lead (`shrink`) | `SHRINK_01` | redistribute / donate / cull surplus | live |
| Retention Lead (`retention`) | `GROCERS_RETENTION_01` | win-back / comp / nudge actions | live (needs customer inputs) |
| Labor Lead (`labor`) | `GROCERS_LABOR_01` | shift assignments / understaffing flag | live (needs labor inputs) |
| Daily Plan (`plan`) | `GROCERS_PLAN_01` | per-store stitched daily plan | live (synthesizer) |

**Data flow:**

```
POS / ERP / WMS                OptiGrocer                         Back to POS / Ops
───────────────                ──────────                         ─────────────────
catalog, inventory,   ──►  INP_* tables  ──►  Forecaster  ──►  closers  ──►  OUT_DECISION
sales, deliveries,                              (writes                        (prices, POs,
prices, shrink, staff,                           INP_DEMAND_FORECAST,           markdowns, moves,
customers, policy                                INP_ELASTICITY)                shifts, outreach)
```

The **Forecaster** is internal: it consumes your history (`INP_SALES_HISTORY`) and writes
`INP_DEMAND_FORECAST` + `INP_ELASTICITY`, which every closer then reads. **You do not supply
forecasts or elasticities** — but you *may* override them (they round-trip in the workbook).

---

## 2. Global conventions

| Topic | Rule |
|---|---|
| **Identifiers** | Stable string slugs you control: `store_id` (e.g. `ST_PRESTON_ROYAL`), `product_id` (e.g. `ITM_00000`), `vendor_id` (e.g. `VND_FRESHFARMS`). Reuse the same value across every table and across every upload — they are the join keys. |
| **Dates** | ISO `YYYY-MM-DD`. Timestamps are ISO-8601 UTC. |
| **Currency** | Numeric (no symbols). Per-store `currency` column declares the unit (default `USD`). |
| **Units** | Selling units consistent with `INP_PRODUCT.unit_of_measure` (`ea`, `lb`, `case`). Inventory, sales, forecast, shrink all in the *same* selling unit. |
| **Booleans** | `true` / `false` (lowercase in the workbook). |
| **Grain** | Master tables are static; transactional tables are **daily per `(product_id, store_id)`**. |
| **NULL / store-scope** | `store_id = NULL` on `INP_PRICE` / `INP_DEMAND_FORECAST` means a chain-wide default that applies to all stores lacking a store-specific row. |

---

## 3. INPUT requirements (data OptiGrocer needs)

11 canonical tables, defined in `compute/app/sectors/grocers/excel_schema.py` and delivered as one
multi-sheet workbook (one sheet per table) — see the companion guide for the template download.

**Required to run the core (perishable/PO/markdown/shrink):** `INP_STORE`, `INP_PRODUCT`,
`INP_VENDOR`, `INP_VENDOR_PRODUCT`, `INP_INVENTORY`, `INP_PRICE`, `INP_SALES_HISTORY`, `INP_POLICY`.
**Strongly recommended:** `INP_SHRINK_HISTORY`. **Engine-produced (optional override):**
`INP_DEMAND_FORECAST`, `INP_ELASTICITY`. **Required for Retention/Labor:** see §3.12.

### 3.1 `INP_STORE` — store master
Grain: one row per store. Cadence: static (reload on change).

| Column | Type | Unit | Req | Notes |
|---|---|---|---|---|
| `store_id` | string (PK) | — | ✔ | Stable store slug; join key everywhere. |
| `store_name` | string | — | ✔ | Display name. |
| `format` | enum | — | | `flagship` \| `neighborhood` \| `express`. |
| `region` | string | — | | Geographic/admin region (for region-scoped policy). |
| `selling_area_sqft` | decimal | sqft | | Drives revenue-per-sqft KPI. |
| `weekly_footfall` | int | customers/wk | | Sizes per-store demand. |
| `currency` | string | — | | Default `USD`. |

### 3.2 `INP_PRODUCT` — item master
Grain: one row per SKU. Cadence: static.

| Column | Type | Unit | Req | Notes |
|---|---|---|---|---|
| `product_id` | string (PK) | — | ✔ | Stable SKU slug. |
| `product_name` | string | — | ✔ | Display name. |
| `department` | enum | — | ✔ | `produce`/`meat`/`seafood`/`dairy`/`deli`/`bakery`/`floral`/`frozen`/`grocery`/`beverage`. |
| `subcategory` | string | — | | e.g. `stone_fruit`. |
| `is_perishable` | bool | — | | Turns on shelf-life / shrink logic. |
| `shelf_life_days` | int | days | | Sellable days from receipt — the spoilage clock. |
| `temp_zone` | enum | — | | `ambient` \| `refrigerated` \| `frozen`. |
| `unit_cost` | decimal | money/unit | | Landed cost — bounds the markdown margin floor. |
| `list_price` | decimal | money/unit | | Regular shelf price. |
| `unit_of_measure` | string | — | | `ea` \| `lb` \| `case`. |
| `default_vendor_id` | string (FK→`INP_VENDOR`) | — | | Fallback supplier if no `INP_VENDOR_PRODUCT` row. |

### 3.3 `INP_VENDOR` — supplier master
Grain: one row per vendor. Cadence: static.

| Column | Type | Unit | Req | Notes |
|---|---|---|---|---|
| `vendor_id` | string (PK) | — | ✔ | Stable vendor slug. |
| `vendor_name` | string | — | ✔ | Display name. |
| `vendor_type` | enum | — | | `produce_distributor`/`direct_farm`/`broadline`/`dsd`/`local`. |
| `lead_time_days` | int | days | | Order→receipt; drives PO timing & safety stock. |
| `delivery_cadence_days` | decimal | days | | Days between routine deliveries (1.0 = daily truck). |
| `min_order_value` | decimal | money | | Minimum $ to activate an order. |
| `reliability_score` | decimal | % OTIF | | 0–100; slip widens safety stock. |
| `cutoff_time` | string | `HH:MM` | | Daily order cutoff. |

### 3.4 `INP_VENDOR_PRODUCT` — vendor catalog per item
Grain: one row per `(vendor_id, product_id)`. Cadence: static. Multiple vendors per item allowed
(primary + backup); the PO Lead picks the cheapest per unit.

| Column | Type | Unit | Req | Notes |
|---|---|---|---|---|
| `vendor_id` | string (FK, PK) | — | ✔ | |
| `product_id` | string (FK, PK) | — | ✔ | |
| `case_pack_units` | decimal | units/case | | Order increment. |
| `case_cost` | decimal | money/case | | Cost per case from this vendor. |
| `min_order_cases` | int | cases | | Per-line MOQ. |
| `lead_time_days` | int | days | | Item-specific; overrides vendor default if set. |

### 3.5 `INP_INVENTORY` — on-hand / on-order snapshot
Grain: one row per `(product_id, store_id)`. **Cadence: daily snapshot** (the spoilage clock).

| Column | Type | Unit | Req | Notes |
|---|---|---|---|---|
| `product_id` | string (FK, PK) | — | ✔ | |
| `store_id` | string (FK, PK) | — | ✔ | |
| `on_hand_units` | decimal | units | | Physical on-shelf. |
| `on_order_units` | decimal | units | | Inbound/in-transit. |
| `received_date` | date | — | | When the oldest on-hand lot arrived. |
| `age_days` | int | days | | Days the oldest units have aged (vs `shelf_life_days`). |
| `days_of_supply` | decimal | days | | Est. days current on-hand lasts. |
| `as_of_date` | date | — | | Snapshot anchor. |

> Drives: `remaining_life = shelf_life_days − age_days`; `spoilage_units = max(0, on_hand − daily_forecast × remaining_life)`.

### 3.6 `INP_PRICE` — current shelf price
Grain: per `(product_id[, store_id])`. Cadence: daily / on price change (upsert).

| Column | Type | Unit | Req | Notes |
|---|---|---|---|---|
| `product_id` | string (FK) | — | ✔ | |
| `store_id` | string (FK) or NULL | — | | NULL = chain-wide default. |
| `current_price` | decimal | money | | Live shelf price (markdown baseline). |
| `floor_price` | decimal | money | | Lowest allowed (margin floor). |
| `ceiling_price` | decimal | money | | Highest allowed. |
| `currency` | string | — | | Default `USD`. |

### 3.7 `INP_SALES_HISTORY` — daily demand history
Grain: one row per `(product_id, store_id, sale_date)`. **Cadence: daily append.** The Forecaster
learns from this — supply as much history as you have (≥ 8–13 weeks recommended).

| Column | Type | Unit | Req | Notes |
|---|---|---|---|---|
| `product_id` | string (FK, PK) | — | ✔ | |
| `store_id` | string (FK, PK) | — | ✔ | |
| `sale_date` | date (PK) | — | ✔ | |
| `units_sold` | decimal | units | | Quantity transacted. |
| `revenue` | decimal | money | | Total sale value. |
| `avg_price` | decimal | money | | Realized price (revenue ÷ units). |
| `on_markdown` | bool | — | | Was the day at markdown? (elasticity signal). |

### 3.8 `INP_SHRINK_HISTORY` — recorded waste
Grain: one row per shrink event. Cadence: daily/batch append. The Shrink Lead learns chronic-loss
items from this.

| Column | Type | Unit | Req | Notes |
|---|---|---|---|---|
| `shrink_id` | string (PK) | — | ✔ | Unique event id. |
| `product_id` | string (FK) | — | ✔ | |
| `store_id` | string (FK) | — | ✔ | |
| `shrink_date` | date | — | ✔ | |
| `units_lost` | decimal | units | | Written-off quantity. |
| `cost_lost` | decimal | money | | Cost basis of loss. |
| `reason` | enum | — | | `spoilage`/`damage`/`expired`/`theft`/`overstock`/`donation`. |

### 3.9 `INP_DEMAND_FORECAST` — *engine-produced* (optional override)
Written by the Forecaster each cycle; round-trips in the workbook so you can override. Grain:
`(product_id[, store_id], forecast_date)`.

| Column | Type | Unit | Notes |
|---|---|---|---|
| `product_id` | string (FK) | — | |
| `store_id` | string (FK) or NULL | — | NULL = chain-level. |
| `forecast_date` | date | — | Horizon date. |
| `expected_units` | decimal | units | Mean point forecast. |
| `units_p10` / `units_p90` | decimal | units | Downside / upside (safety stock). |
| `confidence` | decimal | 0–1 | |

### 3.10 `INP_ELASTICITY` — *engine-produced* (optional override)
Per item. Read by the Markdown Lead.

| Column | Type | Notes |
|---|---|---|
| `product_id` | string (FK) | |
| `elasticity` | decimal | d(ln q)/d(ln p); typically negative (default −1.2). |
| `quality` | enum | `measured`/`modeled`/`prior`. |
| `confidence` | decimal | 0–1. |

### 3.11 `INP_POLICY` — guardrails (your business rules)
Grain: one row per `policy_key`. Cadence: static (admin-configurable in the Configurations panel).

| Column | Type | Notes |
|---|---|---|
| `policy_key` | string (PK) | see keys below |
| `policy_value` | string | value as text (numbers/booleans parsed by the closers) |
| `scope` | enum | `global` \| `department` \| `store` |
| `department` | string | only when `scope=department` |
| `description` | string | human rationale |

**Known policy keys:** `min_margin_pct` (e.g. `0.18`), `target_service_level` (`0.95`),
`spoil_risk_days` (`4`), `markdown_trigger_days` (`4`), `max_markdown_depth_pct` (`0.50`),
`max_days_of_supply` (`7.0`), `perishable_overbuy_guard` (`true`), `allow_donation` (`true`),
`min_redistribute_units` (`4`).

### 3.12 Retention & Labor inputs (closers live; tables supplied separately)
The Retention and Labor closers run on the shared LTV / labor-optimizer engines. They consume:

- **Retention:** `INP_CUSTOMER` (customer_id, household value, tenure, plan), `INP_VISIT`
  (customer_id, store_id, visit_date, basket value/last-seen → recency/frequency), `INP_LOYALTY_POLICY`
  (save budget, per-action cost/response, max comps/calls, risk floor).
- **Labor:** `INP_EMPLOYEE` (employee_id, role/skill, hourly wage, max hours), `INP_AVAILABILITY`
  (employee_id, day, blocks available), `INP_LABOR_DEMAND` (store_id, day, block, required staff by
  skill), `INP_LABOR_POLICY` (OT threshold, min-shift, max days/consecutive, doubles cap, understaff cost).

> These sheets are not yet in the default grocers workbook; request the Retention/Labor template
> addendum if you intend to drive those closers.

### 3.13 Which closer reads what (quick map)

| Closer | Reads |
|---|---|
| `PERISHABLE_01` | PRODUCT, INVENTORY, DEMAND_FORECAST, PRICE, SALES_HISTORY, SHRINK_HISTORY, STORE, POLICY |
| `VENDOR_PO_01` | PRODUCT, INVENTORY, VENDOR, VENDOR_PRODUCT, DEMAND_FORECAST, STORE, POLICY |
| `SHELF_MARKDOWN_01` | PRODUCT, INVENTORY, PRICE, DEMAND_FORECAST, ELASTICITY, SHRINK_HISTORY, STORE, POLICY |
| `SHRINK_01` | PRODUCT, INVENTORY, DEMAND_FORECAST, SHRINK_HISTORY, STORE, POLICY |
| `GROCERS_RETENTION_01` | CUSTOMER, VISIT, LOYALTY_POLICY, STORE, POLICY |
| `GROCERS_LABOR_01` | EMPLOYEE, AVAILABILITY, LABOR_DEMAND, LABOR_POLICY, STORE, POLICY |
| `GROCERS_PLAN_01` | STORE + the fresh decisions of the four core closers |

---

## 4. OUTPUT requirements (decisions OptiGrocer produces)

All closers write to a **shared, normalized output schema**. A POS integration consumes these and
maps each decision back to an operational action (price change, PO, transfer, schedule, outreach).

### 4.1 Shared output tables

```sql
OUT_RUN(run_id PK, aom_id, scope, started_at, completed_at,
        status['PENDING'|'RUNNING'|'COMPLETE'|'FAILED'], config_snapshot JSON, trigger)

OUT_DECISION(decision_id PK, run_id, site_id, decision_type, target_id,
             recommended_value JSON, confidence DECIMAL(4,3), created_at, trigger)

OUT_EXPLANATION(explanation_id PK, decision_id, why_summary, why_detail JSON, counterfactuals JSON)

OUT_KPI_IMPACT(impact_id PK, decision_id, kpi_name, delta_value, delta_unit, horizon_days)
```

- `decision_id` = `dec_{run_id}_{seq}` (e.g. `dec_run_shelf_markdown_01_1718835600000_0001`) — **stable, unique; use it as the idempotency key for write-back.**
- `run_id` = `run_{aom_id_lower}_{epoch_ms}`.
- `site_id` = `store_id` (or `chain` for chain-scoped decisions).
- `target_id` = the object acted on: `product_id` | `vendor_id` | `customer_id` | `employee_id`.
- `recommended_value` = JSON dict (shapes per closer below).
- `confidence` = 0.000–1.000.

### 4.2 Decision catalog (every type, with `recommended_value` shape)

#### `PERISHABLE_01`
- **`FLAG_SPOILAGE_RISK`** → `{product_id, store_id, on_hand, remaining_life_days, daily_forecast, expected_sellable, spoilage_units, department, subcategory}`
- **`FLAG_STOCKOUT_RISK`** → `{product_id, store_id, on_hand, on_order, need_over_window, window_days, shortfall_units, department}`
- KPIs: `spoilage_risk_units` (units), `at_risk_gm_dollars` (money), `expected_stockout_units` (units).
- *Action:* advisory — hand spoilage to Markdown, stockout to Vendor PO.

#### `VENDOR_PO_01`
- **`PLACE_PO`** / **`DELAY_PO`** → `{store_id, vendor_id, vendor_name, fire_today(bool), lead_time_days, line_count, total_units, po_dollars, service_level, overbuy_capped_lines, lines:[{product_id, cases, units, perishable(bool), overbuy_capped(bool)}]}`
- KPIs: `expected_stockout_units`, `expected_spoilage_units`, `in_stock_pct` (ratio), `po_dollars`.
- *Action:* send PO to `vendor_id` (`PLACE_PO` now; `DELAY_PO` schedule ~`lead_time−1` days out).

#### `SHELF_MARKDOWN_01`
- **`SET_MARKDOWN`** → `{product_id, store_id, depth_pct(0.05–0.70), from_price, to_price, on_hand, remaining_life_days, expected_cleared, clears_by_sellby(bool), floor_relaxed(bool), department}`
- KPIs: `markdown_recovery_rate` (ratio), `sell_through_pct` (ratio), `waste_units_avoided` (units), `gross_margin_dollars` (money).
- *Action:* set the shelf price of `product_id` at `store_id` to `to_price`.

#### `SHRINK_01`
- **`REDISTRIBUTE_STOCK`** → `{product_id, from_store, to_store, units, remaining_life_days, department}`
- **`DONATE_STOCK`** / **`CULL_STOCK`** → `{product_id, store_id, units, remaining_life_days, department}`
- KPIs: `waste_units_avoided` (units), `waste_dollars_avoided` (money).
- *Action:* transfer / donate (log for tax) / cull `units`.

#### `GROCERS_RETENTION_01`
- **`SAVE_CUSTOMER` / `COMP_CUSTOMER` / `NUDGE_CUSTOMER` / `UPGRADE_CUSTOMER` / `LOYALTY_OFFER` / `HOLD_CUSTOMER`** → `{customer_id, action, rank, churn_risk(0–1), retained_ltv, intervention_cost, response(0–1), expected_net_save, monthly_value, driver}`
- KPIs: `churn_risk` (ratio, neg), `retained_ltv` (money), `intervention_cost` (money), `customers_saved` (count).
- *Action:* outreach / comp / automated nudge per `action`.

#### `GROCERS_LABOR_01`
- **`ASSIGN_SHIFT` / `ASSIGN_OT_SHIFT`** → `{employee_id, employee_name, day, shift, start_block, end_block, hours, skill, wage_cost, ot_hours, is_double(bool), rank}`
- **`FLAG_UNDERSTAFFED`** → `{deficit_staff_hours, coverage_pct, rank}` (site_id = `chain`).
- KPIs: `labor_cost` (money), `shift_hours`/`overtime_hours` (hours), `coverage_filled` (count); flag adds `coverage_deficit_hours`, `coverage_pct`.
- *Action:* push shifts to the scheduling system; flag = staffing alert.

#### `GROCERS_PLAN_01`
- **`DAILY_PLAN`** → `{target_date, store_name, plan_markdown, counts:{spoilage_risks, markdowns, purchase_orders, waste_moves}, po_dollars, spoilage_units_at_risk}`
- No KPI rows. *Action:* display only (the human-readable daily brief per store).

### 4.3 Decision → POS action cross-reference

| `decision_type` | `target_id` | `site_id` | POS / ops action |
|---|---|---|---|
| `FLAG_SPOILAGE_RISK` | product | store | alert (no write) |
| `FLAG_STOCKOUT_RISK` | product | store | alert (no write) |
| `PLACE_PO` | vendor | store | **send PO** (lines from `recommended_value.lines`) |
| `DELAY_PO` | vendor | store | schedule PO for ~`lead_time−1`d |
| `SET_MARKDOWN` | product | store | **update price** → `to_price` |
| `REDISTRIBUTE_STOCK` | product | from_store | **stock transfer** `units` → `to_store` |
| `DONATE_STOCK` | product | store | remove + log donation (tax) |
| `CULL_STOCK` | product | store | remove + log waste |
| `SAVE/COMP/NUDGE/UPGRADE/LOYALTY_OFFER` | customer | store | CRM outreach / comp / offer |
| `ASSIGN_SHIFT` / `ASSIGN_OT_SHIFT` | employee | store | schedule shift |
| `FLAG_UNDERSTAFFED` | `chain` | `chain` | staffing alert |
| `DAILY_PLAN` | store | store | display brief |

### 4.4 Output API surface (how to read decisions)
Two equivalent read paths exist (see the companion guide for full request/response):

- **Per-instance (tenant):** `GET /api/tenants/{tenant_id}/decisions?aom_id=<AOM>&site_id=<store>&limit=<n>`
- **Sector-scoped (grocers router):** `GET /api/sandbox/grocers/decisions?closer=<key>&store_id=<store>[&tenant_id=<tid>]`
  → returns `{closer_key, aom_id, run_id, run_completed_at, total, decisions:[{decision_id, decision_type, store_id, target_id, recommended_value, confidence, created_at, why_summary, why_detail, counterfactuals:[{alternative, why_not}], kpis:[{kpi_name, delta_value, delta_unit, horizon_days}]}]}`

Plus `GET …/kpis`, `GET …/sites|entities`, `GET …/plan/{site_id}`, `GET …/brain/runs`.

### 4.5 Closing the loop (write-back & idempotency)
- Identify a recommendation by **`decision_id`** (idempotency key) and apply via `(site_id, target_id, decision_type)`.
- The base schema does **not** track applied/approved state. For a robust integration, your side
  should record which `decision_id`s have been executed (and the external POS id) so re-pulling the
  same run doesn't double-apply. (A managed `OUT_DECISION_STATUS` table with
  `status/approved_by/executed_at/execution_result` is the recommended extension if you want OptiGrocer
  to hold that state — ask the OptiU team to enable it.)

---

## 5. Mapping a POS to OptiGrocer (Clover worked example)

OptiGrocer is source-neutral; below is how **Clover** entities map to the `INP_*` tables. Use Clover's
REST API (`https://api.clover.com/v3/merchants/{mId}/…`) or Clover's data export. One Clover
**merchant** = one OptiGrocer **store** (`store_id`); a multi-location chain syncs one merchant per store.

### 5.1 Inbound (Clover → `INP_*`)

| OptiGrocer table | Clover source | Key mapping notes |
|---|---|---|
| `INP_STORE` | Merchant (`/v3/merchants/{mId}`) | `store_id`=merchant id; `currency` from merchant; `store_name` from merchant name. |
| `INP_PRODUCT` | Inventory **Items** (`/v3/merchants/{mId}/items`) + Categories | `product_id`=item id; `product_name`=name; `list_price`=`price`/100 (Clover prices are cents); `unit_cost`=`cost`/100; `department`/`subcategory` from category; `unit_of_measure` from `unitName`. Set `is_perishable`/`shelf_life_days`/`temp_zone` from a category convention or a custom attribute (Clover has no native shelf-life field). |
| `INP_INVENTORY` | Item **stock** (`/v3/merchants/{mId}/item_stocks`) | `on_hand_units`=`quantity`; snapshot daily. `age_days`/`received_date` are **not in Clover** — derive from your receiving/PO records or set conservatively. |
| `INP_PRICE` | Item `price` (+ price modifiers) | `current_price`=`price`/100; set `floor_price`/`ceiling_price` from your margin policy. |
| `INP_SALES_HISTORY` | **Orders** + **LineItems** (`/v3/merchants/{mId}/orders?expand=lineItems`, `payments`) | Aggregate paid line items to daily per `(item, store)`: `units_sold`=Σ qty, `revenue`=Σ line total/100, `avg_price`=revenue÷units, `on_markdown`=any discount applied. |
| `INP_VENDOR` / `INP_VENDOR_PRODUCT` | Clover has **no native vendor/PO model** | Supply from your purchasing system / spreadsheet. |
| `INP_SHRINK_HISTORY` | Clover **Inventory adjustments** (shrink/waste reasons) if used | Map adjustment reason → `reason` enum; else supply from your shrink log. |
| `INP_CUSTOMER` / `INP_VISIT` | Clover **Customers** + Orders joined to customer | For the Retention closer. |
| `INP_EMPLOYEE` / `INP_AVAILABILITY` / `INP_LABOR_DEMAND` | Clover **Employees** + Shifts (Clock-in) | For the Labor closer; demand from your traffic/forecast. |
| `INP_POLICY` | n/a | Your business rules (margins, service level). |

> **Clover unit gotchas:** money is in **cents** (divide by 100); quantities for weighed items use
> `unitQty` (thousandths) — normalize to the `unit_of_measure` you declare in `INP_PRODUCT`.

### 5.2 Outbound (OptiGrocer decisions → Clover)

| Decision | Clover action |
|---|---|
| `SET_MARKDOWN` | `POST /v3/merchants/{mId}/items/{itemId}` with `price = round(to_price*100)` (or apply a Clover **discount**). |
| `REDISTRIBUTE_STOCK` | Adjust stock at both stores: `POST /v3/merchants/{mId}/item_stocks/{itemId}` (decrement source, increment receiver). |
| `DONATE_STOCK` / `CULL_STOCK` | Inventory adjustment (decrement `quantity`) with a waste/donation reason. |
| `PLACE_PO` / `DELAY_PO` | No native Clover PO — route to your purchasing/EDI system using `recommended_value.lines`. |
| `ASSIGN_SHIFT` | No native Clover scheduler — route to your scheduling system. |
| Retention actions | Route `customer_id` to your CRM / marketing tool. |

### 5.3 Recommended sync cadence
- **Daily (pre-open):** push yesterday's `INP_SALES_HISTORY`, fresh `INP_INVENTORY` & `INP_PRICE`
  snapshots → trigger a Brain run → pull `SET_MARKDOWN`, `PLACE_PO`, spoilage flags, `DAILY_PLAN`.
- **On delivery / receiving:** refresh `INP_INVENTORY` (on-hand + age) → re-run PO/perishable.
- **Master data (items/vendors/policy):** push on change (full-overwrite).

---

## 6. Validation & data quality (what the upload checks)

On upload, OptiGrocer runs a **structural validator** (blocks load on hard errors) then a
**cleanser** (flags row-level anomalies for review, never silently drops):

- **Structural (blocking):** required columns present, types parse, enums valid, FK references
  resolve (`store_id`/`product_id`/`vendor_id` exist), numeric bounds (e.g. non-negative units/prices).
- **Row anomalies (triaged, recoverable):** outliers (e.g. a 250,000 sqft store), negatives where
  impossible, suspected typos — each returned with a `suggested_action` of `keep` / `change` /
  `remove` for an operator to confirm before commit.

Header convention: the workbook uses a **2-row header** (column names + a metadata/description row);
the validator skips those. Keep one sheet per `INP_*` table; do not rename sheets.

See the companion guide §3 for the exact upload + review + commit calls.

---

*Generated from the live OptiGrocer codebase (`compute/app/sectors/grocers/excel_schema.py`,
the `grocers_*` AOM runners, `OUT_*` schema, and the grocers API router). Field names and decision
shapes are quoted from source; Clover field mappings are integration guidance.*
