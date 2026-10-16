"""SYNTHETIC sample-data adapter. Everything generated here is fake (no real brand/customer data).

2 profiles x ~90 days with injected anomalies; ground truth is exposed in .ground_truth so tests
can prove the detectors find them.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from ..models import (AdSpend, AttributionEvidence, CostEntry, Customer, D, Order, OrderLine, Product,
                      Refund, SourceTotal, Store, Tenant, q2)
from .base import SourceAdapter

SYNTH = "synthetic"
DEFAULT_END = date(2026, 10, 14)


@dataclass
class Profile:
    tenant_id: str
    name: str
    store_id: str
    seed: int
    # sku, title, price, unit cogs, popularity weight
    skus: list
    campaigns: list  # (platform, campaign_id, name, base_daily_spend, claimed_roas, tw_roas)
    orders_per_day: float
    missing_cogs: list
    spend_spike: Optional[dict] = None     # {"campaign": id, "mult": x, "first_back": n, "last_back": m}
    margin_drop: Optional[dict] = None     # {"skus": [...], "mult": x, "start_back": n}
    refund_spike: Optional[dict] = None    # {"sku": s, "prob": p, "first_back": n, "last_back": m}


PROFILES = {
    "synth_billy": Profile(
        "synth_billy", "SYNTHETIC Billy Goat Botanicals", "store-main", 11,
        [("BB-01", "Goat Milk Soap", "14.00", "5.20", 20), ("BB-02", "Lavender Balm", "18.00", "6.10", 16),
         ("BB-03", "Shea Body Butter", "26.00", "9.50", 14), ("BB-04", "Oat Face Scrub", "22.00", "8.00", 12),
         ("BB-05", "Gift Set Small", "48.00", "19.00", 9), ("BB-06", "Gift Set Large", "88.00", "34.00", 6),
         ("BB-07", "Travel Kit", "32.00", "12.00", 3), ("BB-08", "Hand Cream Duo", "24.00", "8.40", 10)],
        [("synth_meta", "mp-prospecting", "Prospecting Broad", "230", "1.40", "0.60"),
         ("synth_meta", "mr-retarget", "Retargeting 30d", "90", "3.50", "2.10"),
         ("synth_google", "gs-brand", "Brand Search", "70", "6.00", "4.00"),
         ("synth_google", "gs-shopping", "Shopping Core", "110", "2.80", "1.90")],
        32.0, ["BB-07"],
        spend_spike={"campaign": "mp-prospecting", "mult": 3.2, "first_back": 8, "last_back": 0}),
    "synth_nanny": Profile(
        "synth_nanny", "SYNTHETIC Nanny Home Goods", "store-main", 23,
        [("NH-01", "Wool Throw", "89.00", "38.00", 10), ("NH-02", "Linen Cushion", "42.00", "15.00", 18),
         ("NH-03", "Ceramic Mug Set", "36.00", "14.00", 16), ("NH-04", "Beeswax Candle", "19.00", "6.00", 22),
         ("NH-05", "Felted Slippers", "55.00", "21.00", 12), ("NH-06", "Herb Planter", "28.00", "10.50", 12),
         ("NH-07", "Seasonal Wreath", "64.00", "26.00", 3), ("NH-08", "Mini Loom Kit", "45.00", "17.00", 3)],
        [("synth_meta", "mp-lookalike", "Lookalike Buyers", "170", "1.90", "0.95"),
         ("synth_meta", "mr-cart", "Cart Abandoners", "70", "3.80", "2.40"),
         ("synth_google", "gs-brand", "Brand Search", "55", "5.50", "3.80"),
         ("synth_google", "gs-pmax", "Perf Max", "140", "2.40", "1.50")],
        26.0, ["NH-07", "NH-08"],
        margin_drop={"skus": ["NH-02", "NH-05"], "mult": 1.5, "start_back": 9},
        refund_spike={"sku": "NH-03", "prob": 0.55, "first_back": 9, "last_back": 1}),
}


def _poisson(rng: random.Random, lam: float) -> int:
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


class SyntheticAdapter(SourceAdapter):
    def __init__(self, profile: str | Profile, days: int = 90, end_date: date = DEFAULT_END, seed_offset: int = 0):
        p = PROFILES[profile] if isinstance(profile, str) else profile
        super().__init__(p.tenant_id, p.store_id)
        self.profile, self.days, self.end_date, self.seed_offset = p, days, end_date, seed_offset
        self.start_date = end_date - timedelta(days=days - 1)
        self.ground_truth: list[dict] = []
        self._built = False

    # --- registration helpers
    def tenant(self) -> Tenant:
        return Tenant(tenant_id=self.tenant_id, name=self.profile.name, synthetic=True)

    def store(self) -> Store:
        return Store(tenant_id=self.tenant_id, store_id=self.store_id, platform="synthetic_shopify",
                     name=f"{self.profile.name} (SYNTHETIC store)", synthetic=True)

    def _prov(self, source_id: str, platform: str = SYNTH) -> dict:
        return dict(tenant_id=self.tenant_id, store_id=self.store_id, source_platform=platform, source_id=source_id,
                    raw={"SYNTHETIC": True})

    def _back(self, n: int) -> date:
        return self.end_date - timedelta(days=n)

    def _build(self) -> None:
        if self._built:
            return
        p = self.profile
        rng = random.Random(p.seed + self.seed_offset)
        gt = self.ground_truth
        cost_by_sku = {s[0]: D(s[3]) for s in p.skus}
        prices = {s[0]: D(s[2]) for s in p.skus}
        weights = [s[4] for s in p.skus]
        sku_ids = [s[0] for s in p.skus]
        self.products = [Product(**self._prov(s[0]), sku=s[0], title=s[1], price=D(s[2])) for s in p.skus]

        # ---- costs (versioned)
        start_iso = self.start_date.isoformat()
        costs = []
        for sku in sku_ids:
            if sku in p.missing_cogs:
                continue
            costs.append(("cogs", "per_unit", sku, cost_by_sku[sku], start_iso, 1, "SYNTHETIC supplier invoice v1"))
        costs += [("shipping", "per_order", "", D("5.40"), start_iso, 1, "SYNTHETIC avg label cost"),
                  ("fees", "pct_revenue", "", D("0.029"), start_iso, 1, "SYNTHETIC payment fee pct"),
                  ("fees", "per_order", "", D("0.30"), start_iso, 1, "SYNTHETIC payment fee fixed"),
                  ("fixed", "per_day", "", D("85.00"), start_iso, 1, "SYNTHETIC fixed overhead")]
        md = p.margin_drop
        drop_start = self._back(md["start_back"]) if md else None
        if md:
            for sku in md["skus"]:
                new = q2(cost_by_sku[sku] * D(str(md["mult"])))
                costs.append(("cogs", "per_unit", sku, new, drop_start.isoformat(), 2, "SYNTHETIC supplier price increase v2"))
            gt.append({"type": "margin_drop", "skus": md["skus"], "start": drop_start.isoformat()})
        for sku in p.missing_cogs:
            gt.append({"type": "missing_cogs", "sku": sku})
        self.costs = [CostEntry(**self._prov(f"{t}:{b}:{s}:{e}:v{v}"), cost_type=t, basis=b, sku=s, amount=a,
                                effective_from=e, version=v, note=n) for t, b, s, a, e, v, n in costs]

        def unit_cost(sku, day):
            if md and sku in md["skus"] and day >= drop_start:
                return q2(cost_by_sku[sku] * D(str(md["mult"])))
            return cost_by_sku[sku]

        # ---- orders / customers / refunds
        customers: dict[str, str] = {}
        cust_order_n: dict[str, int] = {}
        orders, refunds = [], []
        oid = 1000
        rs = p.refund_spike
        rs_first = self._back(rs["first_back"]) if rs else None
        rs_last = self._back(rs["last_back"]) if rs else None
        if rs:
            gt.append({"type": "refund_spike", "sku": rs["sku"], "start": rs_first.isoformat(), "end": rs_last.isoformat()})
        for i in range(self.days):
            day = self.start_date + timedelta(days=i)
            season = 1.0 + 0.18 * math.sin(2 * math.pi * day.weekday() / 7) + 0.0025 * i
            n = _poisson(rng, p.orders_per_day * season)
            for _ in range(n):
                oid += 1
                pool = list(customers)
                p_repeat = min(0.32, len(pool) / 500)
                if pool and rng.random() < p_repeat:
                    cid = pool[int(rng.random() ** 1.5 * len(pool))]
                else:
                    cid = f"c{len(customers) + 1:05d}"
                    customers[cid] = day.isoformat()
                cust_order_n[cid] = cust_order_n.get(cid, 0) + 1
                ts = datetime(day.year, day.month, day.day, rng.randint(6, 23), rng.randint(0, 59), rng.randint(0, 59))
                lines, gross = [], Decimal("0")
                for ln in range(rng.choices([1, 2, 3], [60, 30, 10])[0]):
                    sku = rng.choices(sku_ids, weights)[0]
                    qty = rng.choices([1, 2], [85, 15])[0]
                    lines.append(OrderLine(line_no=ln + 1, sku=sku, quantity=qty, unit_price=prices[sku]))
                    gross += prices[sku] * qty
                disc = q2(gross * D(rng.choice(["0.10", "0.15", "0.20"]))) if rng.random() < 0.25 else Decimal("0.00")
                net = gross - disc
                ship = Decimal("5.99") if net < 60 else Decimal("0.00")
                status = "cancelled" if rng.random() < 0.02 else "paid"
                orders.append(Order(**self._prov(str(oid)), customer_source_id=cid, ordered_at=ts.isoformat(),
                                    status=status, gross=gross, discounts=disc, shipping_charged=ship,
                                    tax=q2(net * Decimal("0.08")), lines=lines))
                if status == "paid":
                    in_spike = rs and rs_first <= day <= rs_last and any(l.sku == rs["sku"] for l in lines)
                    prob = rs["prob"] if in_spike else 0.035
                    if rng.random() < prob:
                        if in_spike:
                            base = sum(l.unit_price * l.quantity for l in lines if l.sku == rs["sku"])
                            amt, reason = q2(base * (1 - disc / gross if gross else 1)), "defective"
                        else:
                            amt = q2(net * D(rng.choice(["0.5", "1"])))
                            reason = rng.choice(["changed_mind", "damaged", "wrong_item"])
                        rday = day + timedelta(days=rng.randint(1, 3) if in_spike else rng.randint(2, 10))
                        if rday <= self.end_date:
                            refunds.append(Refund(**self._prov(f"r{oid}"), order_source_id=str(oid),
                                                  refunded_at=datetime(rday.year, rday.month, rday.day, 12).isoformat(),
                                                  amount=amt, reason=reason))
        self.orders, self.refunds = orders, refunds
        self.customers = [Customer(**self._prov(cid), created_at=f"{d}T00:00:00",
                                   email_hash=hashlib.sha256(f"SYNTHETIC-{p.tenant_id}-{cid}".encode()).hexdigest()[:16])
                          for cid, d in customers.items()]

        # ---- ad spend + attribution evidence
        spike = p.spend_spike
        ads, attr = [], []
        if spike:
            gt.append({"type": "spend_spike", "campaign": spike["campaign"], "mult": spike["mult"],
                       "start": self._back(spike["first_back"]).isoformat(), "end": self._back(spike["last_back"]).isoformat()})
        for i in range(self.days):
            day = self.start_date + timedelta(days=i)
            for plat, cid, name, base, claimed, tw in p.campaigns:
                spend = D(base) * D(str(round(rng.uniform(0.88, 1.12), 3)))
                if spike and cid == spike["campaign"] and self._back(spike["first_back"]) <= day <= self._back(spike["last_back"]):
                    spend *= D(str(spike["mult"]))
                spend = q2(spend)
                ads.append(AdSpend(**self._prov(f"{plat}:{cid}:{day}", plat), platform=plat, campaign_id=cid,
                                   campaign_name=name, day=day.isoformat(), spend=spend,
                                   impressions=int(spend * 95), clicks=int(spend * 14 / 10)))
                for src, roas, platform in ((plat, claimed, plat), ("synth_triple_whale", tw, "synth_triple_whale")):
                    rev = q2(spend * D(roas) * D(str(round(rng.uniform(0.93, 1.07), 3))))
                    attr.append(AttributionEvidence(**self._prov(f"{src}:{cid}:{day}", platform), evidence_source=src,
                                                    campaign_id=cid, day=day.isoformat(), attributed_revenue=rev,
                                                    attributed_orders=int(rev / 60), model="synthetic_claim"))
        self.ad_spend, self.attribution = ads, attr

        # ---- source totals (the store platform's own daily totals; independent summation)
        tot = {}
        for o in orders:
            if o.status == "cancelled":
                continue
            t = tot.setdefault(o.day.isoformat(), [Decimal("0"), Decimal("0"), Decimal("0"), 0])
            t[0] += o.gross; t[1] += o.discounts; t[3] += 1
        for r in refunds:
            t = tot.setdefault(r.day.isoformat(), [Decimal("0"), Decimal("0"), Decimal("0"), 0])
            t[2] += r.amount
        self.source_totals = [SourceTotal(**self._prov(d), day=d, gross=t[0], discounts=t[1], refunds=t[2], order_count=t[3])
                              for d, t in sorted(tot.items())]
        if spike:
            pass
        self._built = True

    # --- contract
    def fetch_orders(self): self._build(); return list(self.orders)
    def fetch_refunds(self): self._build(); return list(self.refunds)
    def fetch_customers(self): self._build(); return list(self.customers)
    def fetch_products(self): self._build(); return list(self.products)
    def fetch_costs(self): self._build(); return list(self.costs)
    def fetch_ad_spend(self): self._build(); return list(self.ad_spend)
    def fetch_attribution(self): self._build(); return list(self.attribution)
    def fetch_source_totals(self): self._build(); return list(self.source_totals)


def synthetic_adapters(days: int = 90, end_date: date = DEFAULT_END) -> list[SyntheticAdapter]:
    return [SyntheticAdapter(k, days=days, end_date=end_date) for k in PROFILES]
