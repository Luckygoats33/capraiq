# CapraIQ v1

Governed, **read-only** decision system for the done-for-you service Support Wellness Global runs for its clients.
Multi-tenant, per-client isolation, no cross-client sharing. **All bundled sample data is SYNTHETIC.**
No network calls, no credentials, no live connectors, no spend. Python 3.11+, SQLite (stdlib), pytest for tests.

## Principles (enforced in code)
- Agents investigate and propose; **deterministic code computes every number** (Decimal). An LLM may only re-word text via the
  `Narrator` hook and `safe_rewrite` rejects any rewrite that changes a number.
- The store platform (e.g. Shopify) is financial truth. Ad-platform and Triple Whale numbers are *attribution evidence only* (grade C at best).
- Optimise **contribution profit**, not ROAS. Missing cost inputs are flagged, never silently estimated.
- Every recommendation: grade A-F, expected impact, "what would prove it wrong", authority level. Max 5 decisions per brief.
- Authority ladder: observe < draft < bounded < approval-required < prohibited. **v1: every external write is blocked.**

## Run
```bash
make setup            # venv + pytest (only dev dependency; runtime is stdlib-only)
make test             # python -m pytest -q
make demo             # python -m capraiq demo --out out   (prints briefs, writes out/brief_<tenant>.md + out/capraiq_demo.db)
./run.sh              # test then demo
python -m capraiq verify-ledger --db out/capraiq_demo.db
python -m capraiq ingest-dir <folder> --tenant acme --store main --db x.db --synthetic --brief   # local Drive-style CSV folder
```
`ingest-dir` accepts a locally-synced folder (recursive; files recognised by name: orders, order_lines, refunds, customers, products,
costs, ad_spend, attribution, source_totals). Bad rows are skipped and reported, never guessed. No Drive API.

## Architecture
```
capraiq/
  models.py            canonical dataclasses (Decimal money, provenance on every record)
  schema.sql           SQLite schema (+ schema_postgres.sql twin: NUMERIC/JSONB/TIMESTAMPTZ, RLS notes)
  repo.py              tenant-scoped data-access layer (tenant_id REQUIRED on every call; store must belong to tenant; DB composite FKs)
  adapters/            base.py (abstract contract + ingest), csv_adapter.py (CSV + DriveFolderAdapter), synthetic.py (2 tenants x 90d + injected anomalies)
  engine/profit.py     contribution profit, MER, blended CAC, new-customer logic, cohort LTV 30/90/180, reconciliation, SKU economics
  anomalies.py         rolling robust-z (MAD) rules + data-quality anomalies
  grading.py           evidence grades A-F (ordered rules)
  policy.py            authority ladder, role clearances, action registry, default-deny, audit log
  ledger.py            append-only SHA-256 hash-chained decision/approval ledger (per tenant, DB triggers block UPDATE/DELETE)
  agents/              data_steward, profit_analyst, ads_auditor, lifecycle_auditor (phase-2 stub), skeptic, chief_of_staff
  brief.py, pipeline.py, cli.py
tests/                 pytest suite
```
Flow: adapter -> `ingest` -> repo -> engine/anomalies/dq -> Data Steward, Profit Analyst, Ads Auditor, Lifecycle (stub) propose ->
Skeptic tries to disprove and is the only role that sets grades -> Chief of Staff dedupes, ranks (impact x grade weight x impact-kind weight),
keeps 5, logs to ledger, renders the brief.

Grades: A reconciled to store totals + sourced costs + signal >= 7 days; B reconciled but minor cost gaps (<=10%) or 3-6 days; C attribution-only
or store data not fully reconciled; D mismatch / contradicted / >10% unpriced; E < 3 days; F missing critical data.

## Metric definitions (v1, also stored in `metric_definition_version`)
Net revenue = gross - discounts - refunds (refunds on refund day; tax and shipping charged excluded). Contribution profit = net revenue - COGS - shipping cost -
payment fees - ad spend (fixed costs reported separately). Refunds do not reverse COGS (restock unknown). Cohort LTV uses observed net revenue only; customers
not yet observed for N days are excluded (so 90/180-day LTV is `None` with 90 days of data - never extrapolated).

## Known limitations / what's next
- Daily robust-z rules produce some false positives on noisy series (refund rate, CAC); only episodes in the recent window feed decisions, and the Skeptic caps uncorroborated claims at B.
- No incrementality/holdout evidence, so ad claims cannot exceed C. Seasonality/promo calendars are not modelled.
- Postgres schema is provided but not executed in tests (no Postgres available offline). RLS policies are sketched only.
- The `tenant`/`store`/`ledger`/`metric_definition_version` control tables carry tenant_id but not the full provenance column set.
- Customer new/returning is based on loaded history only (no source first-order hint); guest orders are excluded.
- Next: real read-only connectors (Shopify, Meta, Google, Triple Whale) behind the adapter contract; Klaviyo lifecycle audit (phase 2); approval UI;
  bounded-write pilot behind the policy engine; Postgres + RLS; LLM narrator via the `Narrator` hook (wording only); holdout-test tracking.
