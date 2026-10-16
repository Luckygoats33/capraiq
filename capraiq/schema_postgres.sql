-- CapraIQ canonical schema (PostgreSQL twin of schema.sql). NOT executed by v1 tests (no Postgres available offline).
-- Money columns are NUMERIC(18,4); timestamps TIMESTAMPTZ; raw payloads JSONB.
-- Every data row carries tenant_id/store_id/source_platform/source_id/ingested_at/raw_json.
-- (tenant/store/metric_definition_version/ledger are control tables: scoped by tenant_id.)

CREATE TABLE tenant (
  tenant_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  synthetic BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE store (
  tenant_id TEXT NOT NULL REFERENCES tenant(tenant_id),
  store_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  name TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  synthetic BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, store_id)
);

CREATE TABLE product (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL, raw_json JSONB NOT NULL,
  sku TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', price NUMERIC(18,4),
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE customer (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL, raw_json JSONB NOT NULL,
  created_at TIMESTAMPTZ, email_hash TEXT,
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE orders (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL, raw_json JSONB NOT NULL,
  customer_source_id TEXT, ordered_at TIMESTAMPTZ NOT NULL, order_date DATE NOT NULL,
  status TEXT NOT NULL, currency TEXT NOT NULL,
  gross NUMERIC(18,4) NOT NULL, discounts NUMERIC(18,4) NOT NULL, shipping_charged NUMERIC(18,4) NOT NULL, tax NUMERIC(18,4) NOT NULL,
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);
CREATE INDEX idx_orders_date ON orders (tenant_id, store_id, order_date);

CREATE TABLE order_line (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL, raw_json JSONB NOT NULL,
  order_source_id TEXT NOT NULL, line_no BIGINT NOT NULL, sku TEXT NOT NULL,
  quantity BIGINT NOT NULL, unit_price NUMERIC(18,4) NOT NULL,
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);
CREATE INDEX idx_order_line_order ON order_line (tenant_id, store_id, order_source_id);

CREATE TABLE refund (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL, raw_json JSONB NOT NULL,
  order_source_id TEXT NOT NULL, refunded_at TIMESTAMPTZ NOT NULL, refund_date DATE NOT NULL,
  amount NUMERIC(18,4) NOT NULL, reason TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);
CREATE INDEX idx_refund_date ON refund (tenant_id, store_id, refund_date);

CREATE TABLE cost (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL, raw_json JSONB NOT NULL,
  cost_type TEXT NOT NULL CHECK (cost_type IN ('cogs','shipping','fees','fixed')),
  basis TEXT NOT NULL CHECK (basis IN ('per_unit','per_order','pct_revenue','per_day')),
  sku TEXT NOT NULL DEFAULT '', amount NUMERIC(18,4) NOT NULL,
  effective_from DATE NOT NULL, version BIGINT NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE ad_spend (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL, raw_json JSONB NOT NULL,
  platform TEXT NOT NULL, campaign_id TEXT NOT NULL, campaign_name TEXT NOT NULL DEFAULT '',
  day DATE NOT NULL, spend NUMERIC(18,4) NOT NULL, impressions BIGINT NOT NULL DEFAULT 0,
  clicks BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);
CREATE INDEX idx_ad_spend_day ON ad_spend (tenant_id, store_id, day);

CREATE TABLE attribution_evidence (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL, raw_json JSONB NOT NULL,
  evidence_source TEXT NOT NULL, campaign_id TEXT NOT NULL DEFAULT '', day DATE NOT NULL,
  attributed_revenue NUMERIC(18,4) NOT NULL, attributed_orders BIGINT NOT NULL DEFAULT 0,
  model TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE source_total (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL, raw_json JSONB NOT NULL,
  day DATE NOT NULL, gross NUMERIC(18,4) NOT NULL, discounts NUMERIC(18,4) NOT NULL, refunds NUMERIC(18,4) NOT NULL,
  order_count BIGINT NOT NULL,
  PRIMARY KEY (tenant_id, store_id, source_platform, source_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE metric_definition_version (
  tenant_id TEXT NOT NULL REFERENCES tenant(tenant_id),
  metric TEXT NOT NULL, version BIGINT NOT NULL, definition TEXT NOT NULL,
  effective_from DATE NOT NULL, created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, metric, version)
);

CREATE TABLE decision (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL, raw_json JSONB NOT NULL,
  decision_id TEXT NOT NULL, brief_date DATE NOT NULL, rank BIGINT NOT NULL,
  kind TEXT NOT NULL, title TEXT NOT NULL, grade TEXT NOT NULL,
  impact_amount NUMERIC(18,4), impact_kind TEXT NOT NULL, falsifier TEXT NOT NULL,
  authority_level TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed',
  PRIMARY KEY (tenant_id, decision_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE approval (
  tenant_id TEXT NOT NULL, store_id TEXT NOT NULL, source_platform TEXT NOT NULL,
  source_id TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL, raw_json JSONB NOT NULL,
  approval_id TEXT NOT NULL, decision_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('approved','rejected','deferred')),
  approver TEXT NOT NULL, decided_at TIMESTAMPTZ NOT NULL, note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tenant_id, approval_id),
  FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id),
  FOREIGN KEY (tenant_id, decision_id) REFERENCES decision(tenant_id, decision_id)
);

CREATE TABLE ledger (
  tenant_id TEXT NOT NULL REFERENCES tenant(tenant_id),
  seq BIGINT NOT NULL, ts TIMESTAMPTZ NOT NULL, entry_type TEXT NOT NULL,
  payload_json JSONB NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL,
  PRIMARY KEY (tenant_id, seq)
);

-- Append-only enforcement
CREATE FUNCTION capraiq_append_only() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER ledger_no_update BEFORE UPDATE OR DELETE ON ledger FOR EACH ROW EXECUTE FUNCTION capraiq_append_only();
CREATE TRIGGER decision_no_update BEFORE UPDATE OR DELETE ON decision FOR EACH ROW EXECUTE FUNCTION capraiq_append_only();
CREATE TRIGGER approval_no_update BEFORE UPDATE OR DELETE ON approval FOR EACH ROW EXECUTE FUNCTION capraiq_append_only();

-- Recommended in Postgres: row-level security per tenant, e.g.
--   ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
--   CREATE POLICY tenant_iso ON orders USING (tenant_id = current_setting('capraiq.tenant_id'));
