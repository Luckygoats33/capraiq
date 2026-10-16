from __future__ import annotations

from decimal import Decimal

from ..models import ZERO
from ..policy import Action, Role
from .base import Agent, AgentContext, AgentOutput, ExpectedImpact, Finding, Proposal


class DataSteward(Agent):
    """Reconciles to the store platform and reports data gaps. Proposes data fixes (draft level)."""
    role = Role.DATA_STEWARD

    def run(self, ctx: AgentContext) -> AgentOutput:
        ctx.policy.enforce(self.role, Action.READ_METRICS)
        out = AgentOutput(self.role)
        rc = ctx.recon
        out.findings.append(Finding(self.role, f"Reconciliation vs store platform: {rc.status} "
                                               f"({rc.days_checked} days checked, {len(rc.diffs)} differences)."))
        gaps = [m for m in ctx.report.missing if m.kind == "cogs"]
        if gaps:
            skus = sorted(m.key for m in gaps)
            exposure = ctx.monthly(ctx.recent, ctx.recent.unpriced_revenue)
            out.proposals.append(Proposal(
                ctx.tenant_id, ctx.store_id, "data_gap_cogs", ",".join(skus),
                f"Supply COGS for {len(skus)} SKU(s): {', '.join(skus)}",
                f"Contribution profit is overstated because these SKUs have no cost on file. CapraIQ does not estimate costs. "
                f"{ctx.recent.unpriced_revenue:.2f} of list revenue in the last {ctx.recent.days}d is unpriced.",
                Action.DRAFT_RECOMMENDATION, self.role,
                ExpectedImpact(exposure, "revenue_exposure", "unpriced revenue exposure (not a profit figure), 30d projection"),
                "Disproved if a COGS record already exists for these SKUs in the cost source (the gap is an ingestion/mapping error) "
                "or if these SKUs have no sales in the next 30 days.",
                "data_gap", depends_on_costs=False, days_in_signal=ctx.recent.days,
                facts={"skus": skus, "unpriced_units_total": sum(m.units for m in gaps)}))
        for m in ctx.report.missing:
            if m.kind in ("shipping", "fees"):
                out.proposals.append(Proposal(
                    ctx.tenant_id, ctx.store_id, f"data_gap_{m.kind}", m.kind,
                    f"Configure {m.kind} costs", f"No {m.kind} cost configured for {m.units} orders; profit overstated.",
                    Action.DRAFT_RECOMMENDATION, self.role,
                    ExpectedImpact(None, "revenue_exposure", "cost input missing; cannot be quantified without the cost"),
                    f"Disproved if a {m.kind} cost record exists in the source.", "data_gap", days_in_signal=ctx.recent.days))
        if rc.status == "mismatch":
            out.proposals.append(Proposal(
                ctx.tenant_id, ctx.store_id, "recon_mismatch", "recon",
                "Investigate reconciliation mismatch against store platform",
                f"{len(rc.diffs)} day/field differences between recomputed and platform totals.",
                Action.DRAFT_RECOMMENDATION, self.role,
                ExpectedImpact(None, "revenue_exposure", "all downstream numbers are suspect until resolved"),
                "Disproved if the differences are explained by timezone/cutoff or a late-arriving export and totals match after re-pull.",
                "data_gap", days_in_signal=ctx.recent.days))
        for i in ctx.dq:
            if i.kind not in ("missing_cogs", "missing_shipping", "missing_fees", "reconciliation_mismatch"):
                out.findings.append(Finding(self.role, f"[{i.severity}] {i.kind}: {i.detail}"))
        return out
