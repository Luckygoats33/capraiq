"""Adapter contract: source system -> canonical records. Adapters never write; ingest() does."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..models import (AdSpend, AttributionEvidence, CostEntry, Customer, Order, Product, Record,
                      Refund, SourceTotal)
from ..repo import IsolationError, Repo, now_iso


class SourceAdapter(ABC):
    """Subclasses MUST stamp tenant_id/store_id/source_platform on everything they return."""

    def __init__(self, tenant_id: str, store_id: str):
        if not tenant_id or not store_id:
            raise IsolationError("adapter requires tenant_id and store_id")
        self.tenant_id = tenant_id
        self.store_id = store_id

    @abstractmethod
    def fetch_orders(self) -> list[Order]: ...
    @abstractmethod
    def fetch_refunds(self) -> list[Refund]: ...
    @abstractmethod
    def fetch_customers(self) -> list[Customer]: ...
    @abstractmethod
    def fetch_products(self) -> list[Product]: ...
    @abstractmethod
    def fetch_costs(self) -> list[CostEntry]: ...
    @abstractmethod
    def fetch_ad_spend(self) -> list[AdSpend]: ...

    # optional extras (default: none)
    def fetch_attribution(self) -> list[AttributionEvidence]:
        return []

    def fetch_source_totals(self) -> list[SourceTotal]:
        return []


@dataclass
class IngestSummary:
    tenant_id: str
    store_id: str
    counts: dict = field(default_factory=dict)


def ingest(adapter: SourceAdapter, repo: Repo, clock: Callable[[], str] = now_iso) -> IngestSummary:
    """Pull everything from an adapter into the tenant-scoped repo (idempotent upserts)."""
    tid = adapter.tenant_id
    if repo.get_store(tid, adapter.store_id) is None:
        raise IsolationError(f"store {adapter.store_id!r} is not registered for tenant {tid!r}")
    ts = clock()
    summary = IngestSummary(tid, adapter.store_id)
    plan = [("products", adapter.fetch_products, repo.save_products),
            ("customers", adapter.fetch_customers, repo.save_customers),
            ("orders", adapter.fetch_orders, repo.save_orders),
            ("refunds", adapter.fetch_refunds, repo.save_refunds),
            ("costs", adapter.fetch_costs, repo.save_costs),
            ("ad_spend", adapter.fetch_ad_spend, repo.save_ad_spend),
            ("attribution", adapter.fetch_attribution, repo.save_attribution),
            ("source_totals", adapter.fetch_source_totals, repo.save_source_totals)]
    for name, fetch, save in plan:
        recs = fetch()
        for r in recs:
            if r.tenant_id != tid or r.store_id != adapter.store_id:
                raise IsolationError(f"adapter produced {name} row outside its tenant/store scope")
            r.ingested_at = r.ingested_at or ts
        summary.counts[name] = save(tid, recs)
    return summary
