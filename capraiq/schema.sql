-- CapraIQ canonical schema (SQLite). Portable SQL; Postgres twin: schema_postgres.sql.
-- Money columns are TEXT holding exact decimal strings (parsed to Decimal in code).
-- Every data row carries tenant_id/store_id/source_platform/source_id/ingested_at/raw_json.
-- (tenant/store/metric_definition_version/ledger are control tables: scoped by tenant_id.)

CREATE TABLE tenant (
  tenant_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  synthetic INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE store (
  tenant_id TEXT NOT NULL REFERENCES tenant(tenant_id),
  store_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  name TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  synthetic INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY (tenant_id, store_id)
);

CREATE TABLE product (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TEXT NOT NULL, raw_json TEXT NOT NULL,
  sku TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', price TEXT,
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE customer (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TEXT NOT NULL, raw_json TEXT NOT NULL,
  created_at TEXT, email_hash TEXT,
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE orders (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TEXT NOT NULL, raw_json TEXT NOT NULL,
  customer_source_id TEXT, ordered_at TEXT NOT NULL, order_date TEXT NOT NULL,
  status TEXT NOT NULL, currency TEXT NOT NULL,
  gross TEXT NOT NULL, discounts TEXT NOT NULL, shipping_charged TEXT NOT NULL, tax TEXT NOT NULL,
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);
CREATE INDEX idx_orders_date ON orders (tenant_id, store_id, order_date);

CREATE TABLE order_line (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TEXT NOT NULL, raw_json TEXT NOT NULL,
  order_source_id TEXT NOT NULL, line_no INTEGER NOT NULL, sku TEXT NOT NULL,
  quantity INTEGER NOT NULL, unit_price TEXT NOT NULL,
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);
CREATE INDEX idx_order_line_order ON order_line (tenant_id, store_id, order_source_id);

CREATE TABLE refund (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TEXT NOT NULL, raw_json TEXT NOT NULL,
  order_source_id TEXT NOT NULL, refunded_at TEXT NOT NULL, refund_date TEXT NOT NULL,
  amount TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);
CREATE INDEX idx_refund_date ON refund (tenant_id, store_id, refund_date);

CREATE TABLE cost (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TEXT NOT NULL, raw_json TEXT NOT NULL,
  cost_type TEXT NOT NULL CHECK (cost_type IN ('cogs','shipping','fees','fixed')),
  basis TEXT NOT NULL CHECK (basis IN ('per_unit','per_order','pct_revenue','per_day')),
  sku TEXT NOT NULL DEFAULT '', amount TEXT NOT NULL,
  effective_from TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE ad_spend (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TEXT NOT NULL, raw_json TEXT NOT NULL,
  platform TEXT NOT NULL, campaign_id TEXT NOT NULL, campaign_name TEXT NOT NULL DEFAULT '',
  day TEXT NOT NULL, spend TEXT NOT NULL, impressions INTEGER NOT NULL DEFAULT 0,
  clicks INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);
CREATE INDEX idx_ad_spend_day ON ad_spend (tenant_id, store_id, day);

CREATE TABLE attribution_evidence (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TEXT NOT NULL, raw_json TEXT NOT NULL,
  evidence_source TEXT NOT NULL, campaign_id TEXT NOT NULL DEFAULT '', day TEXT NOT NULL,
  attributed_revenue TEXT NOT NULL, attributed_orders INTEGER NOT NULL DEFAULT 0,
  model TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE source_total (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TEXT NOT NULL, raw_json TEXT NOT NULL,
  day TEXT NOT NULL, gross TEXT NOT NULL, discounts TEXT NOT NULL, refunds TEXT NOT NULL,
  order_count INTEGER NOT NULL,
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE metric_definition_version (
  tenant_id TEXT NOT NULL REFERENCES tenant(tenant_id),
  metric TEXT NOT NULL, version INTEGER NOT NULL, definition TEXT NOT NULL,
  effective_from TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (tenant_id, metric, version)
);

CREATE TABLE decision (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TEXT NOT NULL, raw_json TEXT NOT NULL,
  decision_id TEXT NOT NULL, brief_date TEXT NOT NULL, rank INTEGER NOT NULL,
  kind TEXT NOT NULL, title TEXT NOT NULL, grade TEXT NOT NULL,
  impact_amount TEXT, impact_kind TEXT NOT NULL, falsifier TEXT NOT NULL,
  authority_level TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed',
  PRIMARY KEY (tenant_id, decision_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE approval (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TEXT NOT NULL, raw_json TEXT NOT NULL,
  approval_id TEXT NOT NULL, decision_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('approved','rejected','deferred')),
  approver TEXT NOT NULL, decided_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tenant_id, approval_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id),
  FOREIGN KEY (tenant_id, decision_id) REFERENCES decision(tenant_id, decision_id)
);

CREATE TABLE ledger (
  tenant_id TEXT NOT NULL REFERENCES tenant(tenant_id),
  seq INTEGER NOT NULL, ts TEXT NOT NULL, entry_type TEXT NOT NULL,
  payload_json TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL,
  PRIMARY KEY (tenant_id, seq)
);

-- Append-only enforcement
CREATE TRIGGER ledger_no_update BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER ledger_no_delete BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER decision_no_update BEFORE UPDATE ON decision
BEGIN SELECT RAISE(ABORT, 'decision is append-only'); END;
CREATE TRIGGER decision_no_delete BEFORE DELETE ON decision
BEGIN SELECT RAISE(ABORT, 'decision is append-only'); END;
CREATE TRIGGER approval_no_update BEFORE UPDATE ON approval
BEGIN SELECT RAISE(ABORT, 'approval is append-only'); END;
CREATE TRIGGER approval_no_delete BEFORE DELETE ON approval
BEGIN SELECT RAISE(ABORT, 'approval is append-only'); END;
