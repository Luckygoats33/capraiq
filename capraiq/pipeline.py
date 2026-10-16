"""End-to-end orchestration for one tenant: load -> compute -> detect -> agents -> brief (+ ledger)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Optional

from .agents import (AdsAuditor, AgentContext, ChiefOfStaff, DataSteward, LifecycleAuditor, ProfitAnalyst, Skeptic)
from .anomalies import data_quality, detect, episodes
from .engine.profit import METRIC_DEFINITIONS, aggregate, compute_profit, load_inputs, reconcile
from .ledger import Ledger
from .models import day_of
from .policy import Action, PolicyEngine, Role
from .repo import Repo


@dataclass
class TenantRun:
    tenant_id: str
    brief_markdown: str
    decisions: list
    ctx: AgentContext
    outputs: dict


def seed_metric_definitions(repo: Repo, tenant_id: str, effective_from: str) -> None:
    for k, v in METRIC_DEFINITIONS.items():
        repo.save_metric_definition(tenant_id, k, 1, v, effective_from)


def run_tenant(repo: Repo, tenant_id: str, store_id: str, policy: Optional[PolicyEngine] = None,
               ledger: Optional[Ledger] = None, narrator=None) -> TenantRun:
    policy = policy or PolicyEngine()
    tenant = repo.get_tenant(tenant_id)
    store = repo.get_store(tenant_id, store_id)
    if tenant is None or store is None:
        raise ValueError("unknown tenant/store")
    policy.enforce(Role.DATA_STEWARD, Action.READ_TENANT_DATA)
    inputs = load_inputs(repo, tenant_id, store_id)
    if not inputs.orders:
        raise ValueError(f"no orders loaded for {tenant_id}/{store_id}")
    start = min(o.day for o in inputs.orders)
    end = max(o.day for o in inputs.orders)
    report = compute_profit(inputs, start, end)
    recon = reconcile(inputs, start, end)
    eps = episodes(detect(report))
    dq = data_quality(inputs, report, recon)
    recent = report.window(end - timedelta(days=6), end)
    base = report.window(end - timedelta(days=34), end - timedelta(days=7))
    seed_metric_definitions(repo, tenant_id, start.isoformat())
    ctx = AgentContext(tenant_id, store_id, tenant.name, tenant.synthetic or store.synthetic, inputs, report, recon,
                       dq, eps, recent, base, policy, ledger, narrator=narrator)
    outputs = {}
    for agent in (DataSteward(), ProfitAnalyst(), AdsAuditor(), LifecycleAuditor()):
        o = agent.run(ctx)
        outputs[agent.role.value] = o
        ctx.proposals.extend(o.proposals)
    sk = Skeptic().run(ctx)
    outputs[Role.SKEPTIC.value] = sk
    ctx.graded = sk.proposals
    chief = ChiefOfStaff().run(ctx)
    outputs[Role.CHIEF_OF_STAFF.value] = chief
    if ledger is not None:
        policy.enforce(Role.CHIEF_OF_STAFF, Action.WRITE_DECISION_LOG)
        ledger.append(tenant_id, "brief_published", {"as_of": str(end), "decisions": len(chief.decisions),
                      "sha256": hashlib.sha256(chief.brief_markdown.encode()).hexdigest()})
    return TenantRun(tenant_id, chief.brief_markdown, chief.decisions, ctx, outputs)


def write_brief(policy: PolicyEngine, out_dir: Path, tenant_id: str, markdown: str) -> Path:
    policy.enforce(Role.CHIEF_OF_STAFF, Action.WRITE_BRIEF_FILE)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"brief_{tenant_id}.md"
    path.write_text(markdown)
    return path
