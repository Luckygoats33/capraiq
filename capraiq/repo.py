"""Tenant-scoped data-access layer.

Every public method takes tenant_id as a REQUIRED first argument and every SQL statement
filters on it. Writes verify that each record's tenant_id matches and that the store belongs
to that tenant. The DB also enforces (tenant_id, store_id) foreign keys.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable, Optional

from .models import (AdSpend, AttributionEvidence, CostEntry, Customer, D, Order, OrderLine,
                     Product, Record, Refund, SourceTotal, Store, Tenant)


class IsolationError(Exception):
    """Raised on any attempt to read/write without or across tenant scope."""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat()


def _tid(tenant_id) -> str:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise IsolationError("tenant_id is required (non-empty string)")
    return tenant_id


COMMON = ("tenant_id", "store_id", "source_platform", "source_id", "ingested_at", "raw_json")


class Repo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- tenants / stores -------------------------------------------------
    def upsert_tenant(self, t: Tenant) -> None:
        _tid(t.tenant_id)
        self.conn.execute(
            "INSERT INTO tenant (tenant_id,name,synthetic,created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(tenant_id) DO UPDATE SET name=excluded.name, synthetic=excluded.synthetic",
            (t.tenant_id, t.name, int(t.synthetic), now_iso()))
        self.conn.commit()

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        r = self.conn.execute("SELECT * FROM tenant WHERE tenant_id=?", (_tid(tenant_id),)).fetchone()
        return Tenant(tenant_id=r["tenant_id"], name=r["name"], synthetic=bool(r["synthetic"])) if r else None

    def upsert_store(self, s: Store) -> None:
        _tid(s.tenant_id)
        if not self.get_tenant(s.tenant_id):
            raise IsolationError(f"unknown tenant {s.tenant_id}")
        self.conn.execute(
            "INSERT INTO store (tenant_id,store_id,platform,name,currency,synthetic,created_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(tenant_id,store_id) DO UPDATE SET "
            "platform=excluded.platform, name=excluded.name, currency=excluded.currency, synthetic=excluded.synthetic",
            (s.tenant_id, s.store_id, s.platform, s.name, s.currency, int(s.synthetic), now_iso()))
        self.conn.commit()

    def get_store(self, tenant_id: str, store_id: str) -> Optional[Store]:
        r = self.conn.execute("SELECT * FROM store WHERE tenant_id=? AND store_id=?",
                              (_tid(tenant_id), store_id)).fetchone()
        return self._store(r) if r else None

    def list_stores(self, tenant_id: str) -> list[Store]:
        rows = self.conn.execute("SELECT * FROM store WHERE tenant_id=? ORDER BY store_id", (_tid(tenant_id),))
        return [self._store(r) for r in rows]

    @staticmethod
    def _store(r) -> Store:
        return Store(tenant_id=r["tenant_id"], store_id=r["store_id"], platform=r["platform"],
                     name=r["name"], currency=r["currency"], synthetic=bool(r["synthetic"]))

    # ---- generic helpers --------------------------------------------------
    def _check(self, tenant_id: str, records: Iterable[Record]) -> list[Record]:
        _tid(tenant_id)
        recs = list(records)
        stores = {s.store_id for s in self.list_stores(tenant_id)}
        for r in recs:
            if r.tenant_id != tenant_id:
                raise IsolationError(f"record tenant {r.tenant_id!r} != scope {tenant_id!r}")
            if r.store_id not in stores:
                raise IsolationError(f"store {r.store_id!r} not registered for tenant {tenant_id!r}")
        return recs

    def _save(self, table: str, cols: tuple, tenant_id: str, records: Iterable[Record], extract) -> int:
        recs = self._check(tenant_id, records)
        allc = COMMON + cols
        ph = ",".join("?" * len(allc))
        upd = ",".join(f"{c}=excluded.{c}" for c in allc[4:])
        sql = (f"INSERT INTO {table} ({','.join(allc)}) VALUES ({ph}) "
               f"ON CONFLICT(tenant_id,store_id,source_platform,source_id) DO UPDATE SET {upd}")
        ts = now_iso()
        n = 0
        for r in recs:
            stamp = r.ingested_at or ts
            base = (r.tenant_id, r.store_id, r.source_platform, r.source_id, stamp,
                    json.dumps(r.raw, sort_keys=True, default=str))
            self.conn.execute(sql, base + tuple(extract(r)))
            n += 1
        self.conn.commit()
        return n

    def _select(self, table: str, tenant_id: str, store_id: Optional[str] = None,
                where: str = "", params: tuple = (), order: str = "source_id") -> list[sqlite3.Row]:
        sql = f"SELECT * FROM {table} WHERE tenant_id=?"
        args: list = [_tid(tenant_id)]
        if store_id is not None:
            sql += " AND store_id=?"
            args.append(store_id)
        if where:
            sql += f" AND ({where})"
            args.extend(params)
        sql += f" ORDER BY {order}"
        return self.conn.execute(sql, args).fetchall()

    @staticmethod
    def _prov(r) -> dict:
        return dict(tenant_id=r["tenant_id"], store_id=r["store_id"], source_platform=r["source_platform"],
                    source_id=r["source_id"], ingested_at=r["ingested_at"], raw=json.loads(r["raw_json"]))

    def count(self, tenant_id: str, table: str) -> int:
        if table not in {"product", "customer", "orders", "order_line", "refund", "cost", "ad_spend",
                         "attribution_evidence", "source_total", "decision", "approval", "ledger"}:
            raise ValueError(table)
        return self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE tenant_id=?", (_tid(tenant_id),)).fetchone()[0]

    # ---- products / customers --------------------------------------------
    def save_products(self, tenant_id, items) -> int:
        return self._save("product", ("sku", "title", "price"), tenant_id, items,
                          lambda p: (p.sku, p.title, None if p.price is None else str(p.price)))

    def list_products(self, tenant_id, store_id=None) -> list[Product]:
        return [Product(**self._prov(r), sku=r["sku"], title=r["title"],
                        price=None if r["price"] is None else D(r["price"]))
                for r in self._select("product", tenant_id, store_id)]

    def save_customers(self, tenant_id, items) -> int:
        return self._save("customer", ("created_at", "email_hash"), tenant_id, items,
                          lambda c: (c.created_at, c.email_hash))

    def list_customers(self, tenant_id, store_id=None) -> list[Customer]:
        return [Customer(**self._prov(r), created_at=r["created_at"], email_hash=r["email_hash"])
                for r in self._select("customer", tenant_id, store_id)]

    # ---- orders (+ lines) -------------------------------------------------
    def save_orders(self, tenant_id, orders: Iterable[Order]) -> int:
        orders = self._check(tenant_id, orders)
        n = self._save("orders",
                       ("customer_source_id", "ordered_at", "order_date", "status", "currency",
                        "gross", "discounts", "shipping_charged", "tax"),
                       tenant_id, orders,
                       lambda o: (o.customer_source_id, o.ordered_at, o.day.isoformat(), o.status, o.currency,
                                  str(o.gross), str(o.discounts), str(o.shipping_charged), str(o.tax)))
        for o in orders:
            self.conn.execute("DELETE FROM order_line WHERE tenant_id=? AND store_id=? AND source_platform=? "
                              "AND order_source_id=?", (o.tenant_id, o.store_id, o.source_platform, o.source_id))
        line_recs = []
        for o in orders:
            for ln in o.lines:
                lr = Record(tenant_id=o.tenant_id, store_id=o.store_id, source_platform=o.source_platform,
                            source_id=f"{o.source_id}:{ln.line_no}", raw={}, ingested_at=o.ingested_at)
                lr.order_source_id, lr.line_no, lr.sku, lr.quantity, lr.unit_price = (
                    o.source_id, ln.line_no, ln.sku, ln.quantity, ln.unit_price)
                line_recs.append(lr)
        self._save("order_line", ("order_source_id", "line_no", "sku", "quantity", "unit_price"),
                   tenant_id, line_recs,
                   lambda l: (l.order_source_id, l.line_no, l.sku, l.quantity, str(l.unit_price)))
        return n

    def list_orders(self, tenant_id, store_id=None, start: Optional[date] = None,
                    end: Optional[date] = None) -> list[Order]:
        where, params = [], []
        if start:
            where.append("order_date>=?"); params.append(start.isoformat())
        if end:
            where.append("order_date<=?"); params.append(end.isoformat())
        rows = self._select("orders", tenant_id, store_id, " AND ".join(where), tuple(params),
                            order="ordered_at, source_id")
        lines: dict = {}
        for lr in self._select("order_line", tenant_id, store_id, order="order_source_id, line_no"):
            lines.setdefault((lr["store_id"], lr["source_platform"], lr["order_source_id"]), []).append(
                OrderLine(line_no=lr["line_no"], sku=lr["sku"], quantity=lr["quantity"], unit_price=D(lr["unit_price"])))
        return [Order(**self._prov(r), customer_source_id=r["customer_source_id"], ordered_at=r["ordered_at"],
                      status=r["status"], currency=r["currency"], gross=D(r["gross"]), discounts=D(r["discounts"]),
                      shipping_charged=D(r["shipping_charged"]), tax=D(r["tax"]),
                      lines=lines.get((r["store_id"], r["source_platform"], r["source_id"]), []))
                for r in rows]

    # ---- refunds / costs / ads / attribution / source totals --------------
    def save_refunds(self, tenant_id, items) -> int:
        return self._save("refund", ("order_source_id", "refunded_at", "refund_date", "amount", "reason"),
                          tenant_id, items,
                          lambda r: (r.order_source_id, r.refunded_at, r.day.isoformat(), str(r.amount), r.reason))

    def list_refunds(self, tenant_id, store_id=None) -> list[Refund]:
        return [Refund(**self._prov(r), order_source_id=r["order_source_id"], refunded_at=r["refunded_at"],
                       amount=D(r["amount"]), reason=r["reason"])
                for r in self._select("refund", tenant_id, store_id, order="refunded_at, source_id")]

    def save_costs(self, tenant_id, items) -> int:
        return self._save("cost", ("cost_type", "basis", "sku", "amount", "effective_from", "version", "note"),
                          tenant_id, items,
                          lambda c: (c.cost_type, c.basis, c.sku, str(c.amount), c.effective_from, c.version, c.note))

    def list_costs(self, tenant_id, store_id=None) -> list[CostEntry]:
        return [CostEntry(**self._prov(r), cost_type=r["cost_type"], basis=r["basis"], sku=r["sku"],
                          amount=D(r["amount"]), effective_from=r["effective_from"], version=r["version"], note=r["note"])
                for r in self._select("cost", tenant_id, store_id, order="cost_type, sku, effective_from, version")]

    def save_ad_spend(self, tenant_id, items) -> int:
        return self._save("ad_spend", ("platform", "campaign_id", "campaign_name", "day", "spend", "impressions", "clicks"),
                          tenant_id, items,
                          lambda a: (a.platform, a.campaign_id, a.campaign_name, a.day, str(a.spend), a.impressions, a.clicks))

    def list_ad_spend(self, tenant_id, store_id=None) -> list[AdSpend]:
        return [AdSpend(**self._prov(r), platform=r["platform"], campaign_id=r["campaign_id"],
                        campaign_name=r["campaign_name"], day=r["day"], spend=D(r["spend"]),
                        impressions=r["impressions"], clicks=r["clicks"])
                for r in self._select("ad_spend", tenant_id, store_id, order="day, platform, campaign_id")]

    def save_attribution(self, tenant_id, items) -> int:
        return self._save("attribution_evidence",
                          ("evidence_source", "campaign_id", "day", "attributed_revenue", "attributed_orders", "model"),
                          tenant_id, items,
                          lambda a: (a.evidence_source, a.campaign_id, a.day, str(a.attributed_revenue),
                                     a.attributed_orders, a.model))

    def list_attribution(self, tenant_id, store_id=None) -> list[AttributionEvidence]:
        return [AttributionEvidence(**self._prov(r), evidence_source=r["evidence_source"], campaign_id=r["campaign_id"],
                                    day=r["day"], attributed_revenue=D(r["attributed_revenue"]),
                                    attributed_orders=r["attributed_orders"], model=r["model"])
                for r in self._select("attribution_evidence", tenant_id, store_id,
                                      order="day, evidence_source, campaign_id")]

    def save_source_totals(self, tenant_id, items) -> int:
        return self._save("source_total", ("day", "gross", "discounts", "refunds", "order_count"),
                          tenant_id, items,
                          lambda s: (s.day, str(s.gross), str(s.discounts), str(s.refunds), s.order_count))

    def list_source_totals(self, tenant_id, store_id=None) -> list[SourceTotal]:
        return [SourceTotal(**self._prov(r), day=r["day"], gross=D(r["gross"]), discounts=D(r["discounts"]),
                            refunds=D(r["refunds"]), order_count=r["order_count"])
                for r in self._select("source_total", tenant_id, store_id, order="day")]

    # ---- metric definitions ----------------------------------------------
    def save_metric_definition(self, tenant_id: str, metric: str, version: int, definition: str,
                               effective_from: str) -> None:
        _tid(tenant_id)
        self.conn.execute("INSERT OR IGNORE INTO metric_definition_version VALUES (?,?,?,?,?,?)",
                          (tenant_id, metric, version, definition, effective_from, now_iso()))
        self.conn.commit()

    def list_metric_definitions(self, tenant_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM metric_definition_version WHERE tenant_id=? ORDER BY metric, version", (_tid(tenant_id),))]
