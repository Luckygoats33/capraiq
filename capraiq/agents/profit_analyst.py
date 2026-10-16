from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from ..engine.profit import sku_economics
from ..models import ZERO, q2
from ..policy import Action, Role
from .base import Agent, AgentContext, AgentOutput, ExpectedImpact, Finding, Proposal

MARGIN_DROP_PTS = Decimal("0.05")
MIN_SKU_UNITS = 15
REFUND_RATIO = Decimal("1.5")


def _rng(e) -> str:
    return str(e.unit_cogs_min) if e.unit_cogs_min == e.unit_cogs_max else f"{e.unit_cogs_min}-{e.unit_cogs_max}"


class ProfitAnalyst(Agent):
    """Explains movement in contribution profit with an exact additive decomposition (code, not prose)."""
    role = Role.PROFIT_ANALYST

    def run(self, ctx: AgentContext) -> AgentOutput:
        ctx.policy.enforce(self.role, Action.READ_METRICS)
        r, b = ctx.recent, ctx.baseline
        out = AgentOutput(self.role)
        pd = lambda m, v: v / m.days
        drivers = {
            "net revenue": pd(r, r.net_revenue) - pd(b, b.net_revenue),
            "COGS": -(pd(r, r.cogs) - pd(b, b.cogs)),
            "shipping cost": -(pd(r, r.shipping) - pd(b, b.shipping)),
            "payment fees": -(pd(r, r.fees) - pd(b, b.fees)),
            "ad spend": -(pd(r, r.ad_spend) - pd(b, b.ad_spend)),
        }
        total = pd(r, r.contribution_profit) - pd(b, b.contribution_profit)
        assert abs(sum(drivers.values(), ZERO) - total) < Decimal("0.0001")  # decomposition is exact
        parts = ", ".join(f"{k} {v:+,.2f}" for k, v in sorted(drivers.items(), key=lambda kv: kv[1]))
        flag = "" if r.cost_complete else f" (cost inputs missing: {', '.join(r.missing)}; figures exclude those costs)"
        out.findings.append(Finding(self.role,
            f"Daily contribution profit moved {total:+,.2f}/day (last {r.days}d vs prior {b.days}d): {parts}.{flag}"))

        # --- margin drop by SKU
        cur, base = sku_economics(ctx.inputs, r.start, r.end), sku_economics(ctx.inputs, b.start, b.end)
        flagged = []
        for sku, e in cur.items():
            be = base.get(sku)
            if not be or not e.priced or not be.priced or e.units < MIN_SKU_UNITS:
                continue
            if be.gross_margin - e.gross_margin >= MARGIN_DROP_PTS:
                flagged.append(sku)
        if flagged:
            flagged.sort()
            lost = ZERO
            detail = []
            for s in flagged:
                e, be = cur[s], base[s]
                lost += (be.gross_margin - e.gross_margin) * e.net_revenue
                detail.append(f"{s}: margin {be.gross_margin:.1%} -> {e.gross_margin:.1%}, unit COGS "
                              f"{_rng(be)} -> {_rng(e)}")
            rev = sum((cur[s].net_revenue for s in flagged), ZERO)
            bmargin = sum((base[s].net_revenue - base[s].cogs for s in flagged), ZERO) / sum((base[s].net_revenue for s in flagged), ZERO)
            days_sig = 0
            for n in range(r.days):
                d = r.start + timedelta(n)
                ds = sku_economics(ctx.inputs, d, d)
                rv = sum((ds[s].net_revenue for s in flagged if s in ds and ds[s].priced), ZERO)
                cg = sum((ds[s].cogs for s in flagged if s in ds and ds[s].priced), ZERO)
                if rv > 0 and (rv - cg) / rv < bmargin - Decimal("0.03"):
                    days_sig += 1
            out.proposals.append(Proposal(
                ctx.tenant_id, ctx.store_id, "margin_drop", ",".join(flagged),
                f"Gross margin fell on {', '.join(flagged)}",
                "; ".join(detail) + ". Review supplier cost / price on these SKUs.",
                Action.CHANGE_PRICE, self.role,
                ExpectedImpact(ctx.monthly(r, lost), "profit_at_stake",
                               f"margin-rate loss x recent net revenue of these SKUs, 30d projection; versioned costs from cost table"),
                f"Disproved if the unit cost increase is a one-off/credited invoice, if margin on {flagged[0]} returns to "
                f"baseline within 7 days without action, or if the SKUs' volume mix explains the store-level change.",
                "store", depends_on_costs=True, unpriced_share=0.0, days_in_signal=days_sig,
                facts={"skus": flagged, "recent_net_revenue": str(q2(rev)), "baseline_margin": str(round(bmargin, 4))}))

        # --- refund spike
        rr_b, rr_r = b.refund_rate, r.refund_rate
        if rr_b is not None and rr_r is not None and rr_r >= rr_b * REFUND_RATIO and r.refunds > 0:
            excess = pd(r, r.refunds) - pd(b, b.refunds)
            by_sku = sorted(cur.values(), key=lambda e: e.refunds, reverse=True)
            top = by_sku[0] if by_sku else None
            share = (top.refunds / sum((e.refunds for e in cur.values()), ZERO)) if top and r.refunds > 0 else ZERO
            reasons: dict[str, Decimal] = {}
            for rf in ctx.inputs.refunds:
                if r.start <= rf.day <= r.end:
                    reasons[rf.reason or "unknown"] = reasons.get(rf.reason or "unknown", ZERO) + rf.amount
            top_reason = max(reasons.items(), key=lambda kv: kv[1])[0] if reasons else "unknown"
            thresh = pd(b, b.refunds) * Decimal("1.5")
            days_sig = sum(1 for m in ctx.report.days if r.start <= m.start <= r.end and m.refunds > thresh)
            single_max = max((rf.amount for rf in ctx.inputs.refunds if r.start <= rf.day <= r.end), default=ZERO)
            sku_txt = f", concentrated in {top.sku} ({share:.0%} of allocated refunds)" if top and share >= Decimal("0.4") else ""
            out.proposals.append(Proposal(
                ctx.tenant_id, ctx.store_id, "refund_spike", top.sku if top and share >= Decimal("0.4") else "store",
                f"Refund rate {rr_r:.1%} vs {rr_b:.1%} baseline{sku_txt}",
                f"Refunds ${r.refunds:,.2f} in last {r.days}d; top reason '{top_reason}'. Investigate quality/fulfilment before scaling spend on this product.",
                Action.DRAFT_RECOMMENDATION, self.role,
                ExpectedImpact(max(excess, ZERO) * 30, "profit_at_stake", "excess refund dollars/day vs baseline x 30 (cash basis, store platform data)"),
                "Disproved if the elevated refunds trace to a single order or batch already resolved, if refund rate returns to baseline "
                "within 7 days, or if refund dates reflect a delayed bulk-processing backlog rather than new returns.",
                "store", depends_on_costs=False, days_in_signal=days_sig,
                facts={"recent_rate": str(rr_r), "baseline_rate": str(rr_b), "top_reason": top_reason,
                       "top_sku": top.sku if top else "", "top_sku_share": str(round(share, 3)),
                       "largest_single_refund": str(single_max), "recent_refunds": str(q2(r.refunds))}))
        return out
