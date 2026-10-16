"""Canonical, platform-neutral records. Money is always Decimal."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

ZERO = Decimal("0")
CENT = Decimal("0.01")


def D(value: Any) -> Decimal:
    """Strict Decimal parse. Rejects empty, NaN and Infinity."""
    if isinstance(value, Decimal):
        d = value
    else:
        if value is None:
            raise ValueError("empty decimal")
        s = str(value).strip().replace(",", "") if isinstance(value, str) else str(value)
        if s == "":
            raise ValueError("empty decimal")
        d = Decimal(s)
    if not d.is_finite():
        raise ValueError(f"non-finite decimal: {value!r}")
    return d


def q2(d: Decimal) -> Decimal:
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)


def day_of(ts: str) -> date:
    return parse_ts(ts).date()


@dataclass(kw_only=True)
class Record:
    """Provenance carried by every canonical row."""
    tenant_id: str
    store_id: str
    source_platform: str
    source_id: str
    raw: dict = field(default_factory=dict)
    ingested_at: Optional[str] = None


@dataclass(kw_only=True)
class Tenant:
    tenant_id: str
    name: str
    synthetic: bool = False


@dataclass(kw_only=True)
class Store:
    tenant_id: str
    store_id: str
    platform: str
    name: str
    currency: str = "USD"
    synthetic: bool = False


@dataclass(kw_only=True)
class Product(Record):
    sku: str
    title: str = ""
    price: Optional[Decimal] = None


@dataclass(kw_only=True)
class Customer(Record):
    created_at: Optional[str] = None
    email_hash: Optional[str] = None  # never raw PII


@dataclass(kw_only=True)
class OrderLine:
    line_no: int
    sku: str
    quantity: int
    unit_price: Decimal


@dataclass(kw_only=True)
class Order(Record):
    customer_source_id: Optional[str]
    ordered_at: str
    status: str = "paid"  # paid | cancelled | ...
    currency: str = "USD"
    gross: Decimal
    discounts: Decimal = ZERO
    shipping_charged: Decimal = ZERO  # recorded, excluded from net revenue in metric v1
    tax: Decimal = ZERO               # excluded from revenue
    lines: list[OrderLine] = field(default_factory=list)

    @property
    def day(self) -> date:
        return day_of(self.ordered_at)


@dataclass(kw_only=True)
class Refund(Record):
    order_source_id: str
    refunded_at: str
    amount: Decimal
    reason: str = ""

    @property
    def day(self) -> date:
        return day_of(self.refunded_at)


@dataclass(kw_only=True)
class CostEntry(Record):
    """cost_type: cogs|shipping|fees|fixed ; basis: per_unit|per_order|pct_revenue|per_day.
    Versioned: the entry with the latest effective_from <= day (ties: highest version) wins."""
    cost_type: str
    basis: str
    sku: str = ""
    amount: Decimal
    effective_from: str  # YYYY-MM-DD
    version: int = 1
    note: str = ""


@dataclass(kw_only=True)
class AdSpend(Record):
    platform: str
    campaign_id: str
    campaign_name: str = ""
    day: str
    spend: Decimal
    impressions: int = 0
    clicks: int = 0


@dataclass(kw_only=True)
class AttributionEvidence(Record):
    """Platform / Triple Whale claims. EVIDENCE ONLY - never financial truth."""
    evidence_source: str
    campaign_id: str = ""
    day: str
    attributed_revenue: Decimal
    attributed_orders: int = 0
    model: str = ""


@dataclass(kw_only=True)
class SourceTotal(Record):
    """Store-platform reported daily totals, used only for reconciliation."""
    day: str
    gross: Decimal
    discounts: Decimal
    refunds: Decimal
    order_count: int
