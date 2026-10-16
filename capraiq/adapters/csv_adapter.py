"""File/CSV adapter + Google-Drive-folder-style local directory ingestion (no Drive API)."""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..models import (AdSpend, AttributionEvidence, CostEntry, Customer, D, Order, OrderLine, Product,
                      Refund, SourceTotal, day_of)
from .base import SourceAdapter

KINDS = ("order_lines", "orders", "refunds", "customers", "products", "costs", "ad_spend",
         "attribution", "source_totals")
# alias tokens (normalised file stem contains token) -> kind. Order matters: most specific first.
ALIASES = [("order_line", "order_lines"), ("line_item", "order_lines"), ("orderline", "order_lines"),
           ("source_total", "source_totals"), ("sourcetotal", "source_totals"), ("reconcil", "source_totals"),
           ("ad_spend", "ad_spend"), ("adspend", "ad_spend"), ("ads", "ad_spend"),
           ("attribution", "attribution"), ("triple_whale", "attribution"),
           ("refund", "refunds"), ("customer", "customers"), ("product", "products"),
           ("cost", "costs"), ("cogs", "costs"), ("order", "orders")]


def classify(filename: str) -> Optional[str]:
    stem = re.sub(r"[^a-z0-9]+", "_", Path(filename).stem.lower()).strip("_")
    for token, kind in ALIASES:
        if token in stem:
            return kind
    return None


class CsvAdapter(SourceAdapter):
    """files: kind -> list of CSV paths. Bad rows are skipped and recorded in .errors (never guessed)."""

    def __init__(self, tenant_id: str, store_id: str, files: dict[str, list[Path]],
                 source_platform: str = "csv"):
        super().__init__(tenant_id, store_id)
        self.files = {k: [Path(p) for p in v] for k, v in files.items()}
        self.source_platform = source_platform
        self.errors: list[str] = []

    # ---- helpers
    def _rows(self, kind: str) -> Iterable[tuple[str, int, dict]]:
        for path in sorted(self.files.get(kind, [])):
            with open(path, newline="", encoding="utf-8-sig") as fh:
                for i, row in enumerate(csv.DictReader(fh), start=2):
                    row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                    yield path.name, i, row

    def _each(self, kind: str, build: Callable[[dict], object]) -> list:
        out = []
        for fname, line, row in self._rows(kind):
            try:
                obj = build(row)
                obj.raw = {**row, "_file": fname, "_line": line}
                out.append(obj)
            except (ValueError, ArithmeticError, KeyError) as e:
                self.errors.append(f"{fname}:{line}: {kind} row skipped ({type(e).__name__}: {e})")
        return out

    def _prov(self, source_id: str) -> dict:
        if not source_id:
            raise ValueError("missing source id")
        return dict(tenant_id=self.tenant_id, store_id=self.store_id,
                    source_platform=self.source_platform, source_id=source_id)

    # ---- fetchers
    def fetch_orders(self) -> list[Order]:
        lines: dict[str, list[OrderLine]] = {}
        for fname, ln, row in self._rows("order_lines"):
            try:
                qty = int(row["quantity"])
                if qty <= 0:
                    raise ValueError("quantity must be > 0")
                oid = row["order_id"]
                lines.setdefault(oid, []).append(OrderLine(
                    line_no=len(lines.get(oid, [])) + 1, sku=row["sku"], quantity=qty, unit_price=D(row["unit_price"])))
            except (ValueError, ArithmeticError, KeyError) as e:
                self.errors.append(f"{fname}:{ln}: order_lines row skipped ({type(e).__name__}: {e})")

        def build(r):
            gross = D(r["gross"])
            if gross < 0:
                raise ValueError("negative gross")
            day_of(r["ordered_at"])  # validates
            return Order(**self._prov(r["order_id"]), customer_source_id=r.get("customer_id") or None,
                         ordered_at=r["ordered_at"], status=(r.get("status") or "paid").lower(),
                         currency=r.get("currency") or "USD", gross=gross,
                         discounts=D(r.get("discounts") or "0"), shipping_charged=D(r.get("shipping_charged") or "0"),
                         tax=D(r.get("tax") or "0"), lines=lines.get(r["order_id"], []))
        return self._each("orders", build)

    def fetch_refunds(self) -> list[Refund]:
        def build(r):
            day_of(r["refunded_at"])
            return Refund(**self._prov(r["refund_id"]), order_source_id=r["order_id"],
                          refunded_at=r["refunded_at"], amount=D(r["amount"]), reason=r.get("reason", ""))
        return self._each("refunds", build)

    def fetch_customers(self) -> list[Customer]:
        return self._each("customers", lambda r: Customer(
            **self._prov(r["customer_id"]), created_at=r.get("created_at") or None, email_hash=r.get("email_hash") or None))

    def fetch_products(self) -> list[Product]:
        return self._each("products", lambda r: Product(
            **self._prov(r["sku"]), sku=r["sku"], title=r.get("title", ""),
            price=D(r["price"]) if r.get("price") else None))

    def fetch_costs(self) -> list[CostEntry]:
        def build(r):
            version = int(r.get("version") or 1)
            sku = r.get("sku", "")
            sid = f"{r['cost_type']}:{r['basis']}:{sku}:{r['effective_from']}:v{version}"
            if r["cost_type"] not in ("cogs", "shipping", "fees", "fixed") or \
               r["basis"] not in ("per_unit", "per_order", "pct_revenue", "per_day"):
                raise ValueError("unknown cost_type/basis")
            day_of(r["effective_from"])
            return CostEntry(**self._prov(sid), cost_type=r["cost_type"], basis=r["basis"], sku=sku,
                             amount=D(r["amount"]), effective_from=r["effective_from"][:10], version=version,
                             note=r.get("note", ""))
        return self._each("costs", build)

    def fetch_ad_spend(self) -> list[AdSpend]:
        def build(r):
            day_of(r["day"])
            sid = f"{r['platform']}:{r['campaign_id']}:{r['day']}"
            spend = D(r["spend"])
            if spend < 0:
                raise ValueError("negative spend")
            return AdSpend(**self._prov(sid), platform=r["platform"], campaign_id=r["campaign_id"],
                           campaign_name=r.get("campaign_name", ""), day=r["day"][:10], spend=spend,
                           impressions=int(r.get("impressions") or 0), clicks=int(r.get("clicks") or 0))
        return self._each("ad_spend", build)

    def fetch_attribution(self) -> list[AttributionEvidence]:
        def build(r):
            day_of(r["day"])
            sid = f"{r['evidence_source']}:{r.get('campaign_id','')}:{r['day']}"
            return AttributionEvidence(**self._prov(sid), evidence_source=r["evidence_source"],
                                       campaign_id=r.get("campaign_id", ""), day=r["day"][:10],
                                       attributed_revenue=D(r["attributed_revenue"]),
                                       attributed_orders=int(r.get("attributed_orders") or 0), model=r.get("model", ""))
        return self._each("attribution", build)

    def fetch_source_totals(self) -> list[SourceTotal]:
        def build(r):
            day_of(r["day"])
            return SourceTotal(**self._prov(r["day"][:10]), day=r["day"][:10], gross=D(r["gross"]),
                               discounts=D(r["discounts"]), refunds=D(r["refunds"]), order_count=int(r["order_count"]))
        return self._each("source_totals", build)


class DriveFolderAdapter(CsvAdapter):
    """Ingest a locally synced 'Google Drive folder': recursively scan for CSVs, classify by filename
    ("Orders - Oct 2026.csv", "orders_2026-09.csv", "Ad Spend Meta.csv" ...). Several files per kind are merged.
    Read-only; unrecognised files are listed in .skipped_files. No Drive API or network."""

    def __init__(self, tenant_id: str, store_id: str, root: Path, source_platform: str = "drive_folder"):
        root = Path(root)
        if not root.is_dir():
            raise FileNotFoundError(f"not a directory: {root}")
        files: dict[str, list[Path]] = {}
        skipped: list[str] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            name = p.name
            if name.startswith((".", "~$", "._")) or p.suffix.lower() != ".csv":
                skipped.append(str(p.relative_to(root)))
                continue
            kind = classify(name)
            if kind is None:
                skipped.append(str(p.relative_to(root)))
                continue
            files.setdefault(kind, []).append(p)
        super().__init__(tenant_id, store_id, files, source_platform)
        self.root = root
        self.skipped_files = skipped
