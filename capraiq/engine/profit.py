"""Deterministic contribution-profit engine. Decimal only. No estimation of missing costs.

Metric definition v1 (see METRIC_DEFINITIONS):
  net_revenue = gross - discounts - refunds   (refunds on cash basis: counted on refund day;
                                                shipping charged and tax are excluded)
  contribution_profit = net_revenue - cogs - shipping_cost - fees - ad_spend   (fixed costs excluded)
Missing cost inputs are listed in `missing`; the affected cost is counted as 0 in the number and the
row/period is flagged cost_complete=False. Nothing is ever back-filled or estimated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from ..models import (ZERO, AdSpend, AttributionEvidence, CostEntry, Customer, Order, Refund, SourceTotal, day_of, q2)
from ..repo import Repo

METRIC_DEFINITIONS = {
    "net_revenue": "gross - discounts - refunds; refunds on refund day; excludes tax and shipping charged. Source: store platform.",
    "contribution_profit": "net_revenue - COGS - shipping cost - payment fees - ad spend; fixed costs excluded; missing costs flagged not estimated.",
    "mer": "net_revenue / ad_spend (blended, all channels); null when spend = 0.",
    "blended_cac": "ad_spend / new_customers; new = customer's first non-cancelled order observed in loaded history; null when 0 new.",
    "refund_rate": "refunds / gross on the same period (cash basis).",
    "cohort_ltv": "cohort by first-order month; LTV_N = mean net revenue within N days of first order over customers observed >= N days; observed data only.",
}


def r4(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


@dataclass
class ProfitInputs:
    orders: list[Order] = field(default_factory=list)
    refunds: list[Refund] = field(default_factory=list)
    customers: list[Customer] = field(default_factory=list)
    costs: list[CostEntry] = field(default_factory=list)
    ad_spend: list[AdSpend] = field(default_factory=list)
    source_totals: list[SourceTotal] = field(default_factory=list)
    attribution: list[AttributionEvidence] = field(default_factory=list)
    products: list = field(default_factory=list)


def load_inputs(repo: Repo, tenant_id: str, store_id: str) -> ProfitInputs:
    return ProfitInputs(
        orders=repo.list_orders(tenant_id, store_id), refunds=repo.list_refunds(tenant_id, store_id),
        customers=repo.list_customers(tenant_id, store_id), costs=repo.list_costs(tenant_id, store_id),
        ad_spend=repo.list_ad_spend(tenant_id, store_id), source_totals=repo.list_source_totals(tenant_id, store_id),
        attribution=repo.list_attribution(tenant_id, store_id), products=repo.list_products(tenant_id, store_id))


class CostBook:
    """Versioned cost resolution: latest effective_from <= day, ties -> highest version."""

    def __init__(self, costs: list[CostEntry]):
        self._idx: dict[tuple, list[CostEntry]] = {}
        for c in costs:
            self._idx.setdefault((c.cost_type, c.basis, c.sku), []).append(c)
        for v in self._idx.values():
            v.sort(key=lambda c: (c.effective_from, c.version))

    def resolve(self, cost_type: str, basis: str, sku: str, day: date) -> Optional[CostEntry]:
        best = None
        iso = day.isoformat()
        for c in self._idx.get((cost_type, basis, sku), []):
            if c.effective_from <= iso:
                best = c
        return best


@dataclass
class MissingInput:
    kind: str            # cogs | shipping | fees
    key: str             # sku for cogs
    units: int = 0       # units (cogs) or orders (shipping/fees) affected
    revenue: Decimal = ZERO  # list-price revenue of affected lines (cogs) / net revenue of affected orders
    days: int = 0


@dataclass
class Metrics:
    start: date
    end: date
    orders: int = 0
    new_customers: int = 0
    returning_customers: int = 0
    guest_orders: int = 0
    gross: Decimal = ZERO
    discounts: Decimal = ZERO
    refunds: Decimal = ZERO
    cogs: Decimal = ZERO
    shipping: Decimal = ZERO
    fees: Decimal = ZERO
    ad_spend: Decimal = ZERO
    fixed_costs: Optional[Decimal] = None
    unpriced_revenue: Decimal = ZERO
    missing: list[str] = field(default_factory=list)

    @property
    def net_revenue(self) -> Decimal:
        return self.gross - self.discounts - self.refunds

    @property
    def contribution_profit(self) -> Decimal:
        return self.net_revenue - self.cogs - self.shipping - self.fees - self.ad_spend

    @property
    def cost_complete(self) -> bool:
        return not self.missing

    @property
    def mer(self) -> Optional[Decimal]:
        return r4(self.net_revenue / self.ad_spend) if self.ad_spend > 0 else None

    @property
    def cac(self) -> Optional[Decimal]:
        return q2(self.ad_spend / self.new_customers) if self.new_customers > 0 else None

    @property
    def refund_rate(self) -> Optional[Decimal]:
        return r4(self.refunds / self.gross) if self.gross > 0 else None

    @property
    def aov(self) -> Optional[Decimal]:
        return q2((self.gross - self.discounts) / self.orders) if self.orders else None

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def aggregate(rows: list[Metrics], start: date, end: date) -> Metrics:
    m = Metrics(start=start, end=end)
    miss = set()
    fixed_vals = [r.fixed_costs for r in rows]
    for r in rows:
        for f in ("orders", "new_customers", "returning_customers", "guest_orders", "gross", "discounts",
                  "refunds", "cogs", "shipping", "fees", "ad_spend", "unpriced_revenue"):
            setattr(m, f, getattr(m, f) + getattr(r, f))
        miss.update(r.missing)
    m.missing = sorted(miss)
    if fixed_vals and all(v is not None for v in fixed_vals):
        m.fixed_costs = sum(fixed_vals, ZERO)
    return m


@dataclass
class ProfitReport:
    start: date
    end: date
    days: list[Metrics]
    total: Metrics
    missing: list[MissingInput]

    def window(self, start: date, end: date) -> Metrics:
        return aggregate([d for d in self.days if start <= d.start <= end], start, end)


def _first_orders(orders: list[Order]) -> dict[str, tuple]:
    first: dict[str, tuple] = {}
    for o in orders:
        if o.status == "cancelled" or not o.customer_source_id:
            continue
        key = (o.ordered_at, o.source_id)
        if o.customer_source_id not in first or key < first[o.customer_source_id]:
            first[o.customer_source_id] = key
    return first


def compute_profit(inputs: ProfitInputs, start: date, end: date) -> ProfitReport:
    if end < start:
        raise ValueError("end before start")
    book = CostBook(inputs.costs)
    first = _first_orders(inputs.orders)
    days = {start + timedelta(n): Metrics(start=start + timedelta(n), end=start + timedelta(n))
            for n in range((end - start).days + 1)}
    miss: dict[tuple, MissingInput] = {}
    miss_days: dict[tuple, set] = {}

    def flag(m: Metrics, kind: str, key: str, units: int, rev: Decimal):
        tag = f"{kind}:{key}" if key else kind
        if tag not in m.missing:
            m.missing.append(tag)
        mi = miss.setdefault((kind, key), MissingInput(kind, key))
        mi.units += units
        mi.revenue += rev
        miss_days.setdefault((kind, key), set()).add(m.start)

    for o in inputs.orders:
        m = days.get(o.day)
        if m is None or o.status == "cancelled":
            continue
        m.orders += 1
        m.gross += o.gross
        m.discounts += o.discounts
        if not o.customer_source_id:
            m.guest_orders += 1
        elif first.get(o.customer_source_id) == (o.ordered_at, o.source_id):
            m.new_customers += 1
        else:
            m.returning_customers += 1
        for ln in o.lines:
            c = book.resolve("cogs", "per_unit", ln.sku, o.day)
            if c is None:
                m.unpriced_revenue += ln.unit_price * ln.quantity
                flag(m, "cogs", ln.sku, ln.quantity, ln.unit_price * ln.quantity)
            else:
                m.cogs += c.amount * ln.quantity
        net = o.gross - o.discounts
        ship = book.resolve("shipping", "per_order", "", o.day)
        if ship is None:
            flag(m, "shipping", "", 1, net)
        else:
            m.shipping += ship.amount
        pct = book.resolve("fees", "pct_revenue", "", o.day)
        fixed = book.resolve("fees", "per_order", "", o.day)
        if pct is None and fixed is None:
            flag(m, "fees", "", 1, net)
        else:
            m.fees += net * (pct.amount if pct else ZERO) + (fixed.amount if fixed else ZERO)
    for r in inputs.refunds:
        m = days.get(r.day)
        if m is not None:
            m.refunds += r.amount
    for a in inputs.ad_spend:
        m = days.get(day_of(a.day))
        if m is not None:
            m.ad_spend += a.spend
    for d, m in days.items():
        f = book.resolve("fixed", "per_day", "", d)
        m.fixed_costs = f.amount if f else None
    for k, mi in miss.items():
        mi.days = len(miss_days[k])
    rows = [days[d] for d in sorted(days)]
    return ProfitReport(start, end, rows, aggregate(rows, start, end),
                        sorted(miss.values(), key=lambda x: (x.kind, x.key)))


# ---------------------------------------------------------------- reconciliation
@dataclass
class ReconDiff:
    day: date
    field: str
    ours: Decimal
    source: Decimal


@dataclass
class ReconResult:
    status: str  # reconciled | mismatch | partial | no_source_totals
    diffs: list[ReconDiff]
    days_checked: int
    days_with_orders: int
    coverage: Decimal
    tolerance: Decimal

    @property
    def reconciled(self) -> bool:
        return self.status == "reconciled"


def reconcile(inputs: ProfitInputs, start: date, end: date, tolerance: Decimal = Decimal("0.01")) -> ReconResult:
    """Compare our recomputed daily gross/discounts/refunds/order_count with the store platform's totals."""
    src = {s.day: s for s in inputs.source_totals if start <= day_of(s.day) <= end}
    ours: dict[date, list] = {}
    for o in inputs.orders:
        if o.status == "cancelled" or not (start <= o.day <= end):
            continue
        t = ours.setdefault(o.day, [ZERO, ZERO, ZERO, 0])
        t[0] += o.gross; t[1] += o.discounts; t[3] += 1
    for r in inputs.refunds:
        if start <= r.day <= end:
            ours.setdefault(r.day, [ZERO, ZERO, ZERO, 0])[2] += r.amount
    if not src:
        return ReconResult("no_source_totals", [], 0, len(ours), ZERO, tolerance)
    diffs: list[ReconDiff] = []
    for iso, s in sorted(src.items()):
        d = day_of(iso)
        t = ours.get(d, [ZERO, ZERO, ZERO, 0])
        for name, a, b in (("gross", t[0], s.gross), ("discounts", t[1], s.discounts),
                           ("refunds", t[2], s.refunds), ("order_count", Decimal(t[3]), Decimal(s.order_count))):
            if abs(a - b) > tolerance:
                diffs.append(ReconDiff(d, name, a, b))
    uncovered = [d for d in ours if d.isoformat() not in src]
    cov = Decimal(len(ours) - len(uncovered)) / Decimal(len(ours)) if ours else Decimal(1)
    status = "mismatch" if diffs else ("partial" if uncovered else "reconciled")
    return ReconResult(status, diffs, len(src), len(ours), r4(cov), tolerance)


# ---------------------------------------------------------------- cohort LTV
@dataclass
class CohortLTV:
    cohort: str
    customers: int
    ltv: dict  # horizon -> (Decimal|None, mature_customers)


def cohort_ltv(inputs: ProfitInputs, observed_end: date, horizons=(30, 90, 180)) -> list[CohortLTV]:
    """Observed data only: customers not yet observed for N full days are excluded from the LTV_N mean."""
    valid = [o for o in inputs.orders if o.status != "cancelled" and o.customer_source_id]
    refund_by_order: dict[str, Decimal] = {}
    for r in inputs.refunds:
        refund_by_order[r.order_source_id] = refund_by_order.get(r.order_source_id, ZERO) + r.amount
    by_cust: dict[str, list[Order]] = {}
    for o in valid:
        by_cust.setdefault(o.customer_source_id, []).append(o)
    cohorts: dict[str, list[tuple[date, list[Order]]]] = {}
    for cid, os_ in by_cust.items():
        os_.sort(key=lambda o: (o.ordered_at, o.source_id))
        first = os_[0].day
        cohorts.setdefault(first.strftime("%Y-%m"), []).append((first, os_))
    out = []
    for name in sorted(cohorts):
        members = cohorts[name]
        ltv = {}
        for h in horizons:
            vals = []
            for first, os_ in members:
                if (observed_end - first).days < h:
                    continue
                vals.append(sum((o.gross - o.discounts - refund_by_order.get(o.source_id, ZERO)
                                 for o in os_ if (o.day - first).days <= h), ZERO))
            ltv[h] = (q2(sum(vals, ZERO) / len(vals)) if vals else None, len(vals))
        out.append(CohortLTV(name, len(members), ltv))
    return out


# ---------------------------------------------------------------- SKU economics
@dataclass
class SkuEcon:
    sku: str
    units: int = 0
    net_revenue: Decimal = ZERO     # line revenue after pro-rata order discount (refunds excluded)
    cogs: Decimal = ZERO
    unpriced_units: int = 0
    refunds: Decimal = ZERO         # refunds allocated pro rata by line value
    unit_cogs_min: Optional[Decimal] = None
    unit_cogs_max: Optional[Decimal] = None

    @property
    def priced(self) -> bool:
        return self.unpriced_units == 0 and self.units > 0

    @property
    def gross_margin(self) -> Optional[Decimal]:
        return r4((self.net_revenue - self.cogs) / self.net_revenue) if self.priced and self.net_revenue > 0 else None


def sku_economics(inputs: ProfitInputs, start: date, end: date) -> dict[str, SkuEcon]:
    book = CostBook(inputs.costs)
    ref_by_order: dict[str, Decimal] = {}
    for r in inputs.refunds:
        ref_by_order[r.order_source_id] = ref_by_order.get(r.order_source_id, ZERO) + r.amount
    out: dict[str, SkuEcon] = {}
    for o in inputs.orders:
        if o.status == "cancelled" or not (start <= o.day <= end) or not o.lines:
            continue
        line_vals = [ln.unit_price * ln.quantity for ln in o.lines]
        total = sum(line_vals, ZERO)
        for ln, lv in zip(o.lines, line_vals):
            e = out.setdefault(ln.sku, SkuEcon(ln.sku))
            share = lv / total if total else ZERO
            e.units += ln.quantity
            e.net_revenue += lv - o.discounts * share
            e.refunds += ref_by_order.get(o.source_id, ZERO) * share
            c = book.resolve("cogs", "per_unit", ln.sku, o.day)
            if c is None:
                e.unpriced_units += ln.quantity
            else:
                e.cogs += c.amount * ln.quantity
                e.unit_cogs_min = c.amount if e.unit_cogs_min is None else min(e.unit_cogs_min, c.amount)
                e.unit_cogs_max = c.amount if e.unit_cogs_max is None else max(e.unit_cogs_max, c.amount)
    return out
