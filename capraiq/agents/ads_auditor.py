from __future__ import annotations

import statistics
from datetime import timedelta
from decimal import Decimal

from ..models import ZERO, day_of, q2
from ..policy import Action, Role
from .base import Agent, AgentContext, AgentOutput, ExpectedImpact, Finding, Proposal

SPIKE_RATIO = Decimal("1.5")
MIN_MONTHLY_EXCESS = Decimal("150")
WINNER_MULT = Decimal("1.25")
MIN_WEEKLY_SPEND = Decimal("50")
WASTE_MIN_WEEKLY_SPEND = Decimal("100")


class AdsAuditor(Agent):
    """Finds waste / winners. Spend comes from ad platforms; revenue truth comes ONLY from the store.
    Attribution (platform / Triple Whale) is evidence, labelled as such, and is graded C at best."""
    role = Role.ADS_AUDITOR

    def run(self, ctx: AgentContext) -> AgentOutput:
        ctx.policy.enforce(self.role, Action.READ_METRICS)
        r, b = ctx.recent, ctx.baseline
        out = AgentOutput(self.role)
        camps: dict[str, dict] = {}
        for a in ctx.inputs.ad_spend:
            d = day_of(a.day)
            c = camps.setdefault(a.campaign_id, {"name": a.campaign_name or a.campaign_id, "platform": a.platform,
                                                 "r": ZERO, "b": ZERO, "daily": {}})
            if r.start <= d <= r.end:
                c["r"] += a.spend; c["daily"][d] = c["daily"].get(d, ZERO) + a.spend
            elif b.start <= d <= b.end:
                c["b"] += a.spend
        claims: dict[tuple, Decimal] = {}
        for e in ctx.inputs.attribution:
            if r.start <= day_of(e.day) <= r.end:
                claims[(e.evidence_source, e.campaign_id)] = claims.get((e.evidence_source, e.campaign_id), ZERO) + e.attributed_revenue

        def roas(src, cid, spend):
            return (claims.get((src, cid), ZERO) / spend) if spend > 0 else None

        store_rev_change = ((r.net_revenue / r.days) / (b.net_revenue / b.days) - 1) if b.net_revenue > 0 else ZERO
        total_claimed_platform = sum((v for (s, _), v in claims.items() if s != "synth_triple_whale" and s != "triple_whale"), ZERO)
        if r.net_revenue > 0 and total_claimed_platform > 0:
            out.findings.append(Finding(self.role,
                f"Attribution evidence (NOT truth): ad platforms claim ${total_claimed_platform:,.2f} revenue in the last {r.days}d "
                f"vs store net revenue ${r.net_revenue:,.2f} ({total_claimed_platform / r.net_revenue:.0%} of store truth)."))
        mer_r, mer_b = r.mer, b.mer
        out.findings.append(Finding(self.role, f"Blended MER (store net revenue / spend): {mer_r} last {r.days}d vs {mer_b} prior {b.days}d."))

        for cid, c in sorted(camps.items()):
            rb, bb = c["r"] / r.days, c["b"] / b.days
            if bb > 0 and rb >= bb * SPIKE_RATIO and (rb - bb) * 30 >= MIN_MONTHLY_EXCESS:
                days_sig = sum(1 for v in c["daily"].values() if v >= bb * SPIKE_RATIO)
                claimed = roas(c["platform"], cid, c["r"])
                tw = roas("synth_triple_whale", cid, c["r"]) or roas("triple_whale", cid, c["r"])
                out.proposals.append(Proposal(
                    ctx.tenant_id, ctx.store_id, "spend_spike", cid,
                    f"{c['name']}: spend {rb / bb:.1f}x its baseline while store revenue moved {store_rev_change:+.0%}",
                    f"Avg daily spend ${q2(rb)} vs ${q2(bb)} baseline (+${q2((rb - bb) * 30)}/30d if sustained). "
                    f"Blended MER {mer_r} vs {mer_b}. Platform-claimed ROAS {q2(claimed) if claimed else 'n/a'} and Triple Whale "
                    f"{q2(tw) if tw else 'n/a'} are attribution evidence only.",
                    Action.CHANGE_CAMPAIGN_BUDGET, self.role,
                    ExpectedImpact((rb - bb) * 30, "spend_at_risk", "excess daily spend vs 28d baseline x 30; incremental store revenue not credited unless it appears in store data"),
                    "Disproved if store net revenue rose roughly in proportion to the extra spend (revenue lift >= half the spend increase "
                    "with MER within 10% of baseline), if the spike is a planned promo/launch, or if spend reverts to baseline within 3 days.",
                    "store", depends_on_costs=False, days_in_signal=days_sig,
                    facts={"campaign": cid, "recent_daily_spend": str(q2(rb)), "baseline_daily_spend": str(q2(bb)),
                           "store_revenue_change": str(round(store_rev_change, 4)), "mer_recent": str(mer_r), "mer_baseline": str(mer_b),
                           "claimed_roas_attribution_only": str(q2(claimed)) if claimed else "", "tw_roas_attribution_only": str(q2(tw)) if tw else ""}))

        spiking = {p.key for p in out.proposals}
        rows = []
        for cid, c in camps.items():
            if c["r"] < MIN_WEEKLY_SPEND or cid in spiking:
                continue
            rows.append((cid, c, roas(c["platform"], cid, c["r"]), roas("synth_triple_whale", cid, c["r"]) or roas("triple_whale", cid, c["r"])))
        platform_roas = [x[2] for x in rows if x[2] is not None]
        if platform_roas:
            med = statistics.median(platform_roas)
            winners = [x for x in rows if x[2] is not None and x[2] >= Decimal(str(med)) * WINNER_MULT and "brand" not in x[1]["name"].lower()]
            winners.sort(key=lambda x: x[2], reverse=True)
            if winners:
                cid, c, pr, tw = winners[0]
                claimed_rev = claims.get((c["platform"], cid), ZERO)
                out.proposals.append(Proposal(
                    ctx.tenant_id, ctx.store_id, "campaign_winner", cid,
                    f"{c['name']} looks efficient on platform data (claimed ROAS {q2(pr)}) - candidate to test a bounded budget increase",
                    f"ATTRIBUTION EVIDENCE ONLY: platform claims ${claimed_rev:,.2f} on ${c['r']:,.2f} spend in {r.days}d; Triple Whale "
                    f"{q2(tw) if tw else 'n/a'} ROAS. Branded search excluded (captures existing demand).",
                    Action.CHANGE_CAMPAIGN_BUDGET, self.role,
                    ExpectedImpact(max(claimed_rev - c["r"], ZERO) * 30 / r.days, "attribution_claimed", "platform-claimed revenue minus spend, 30d projection; unverified against store truth"),
                    "Disproved if a holdout / geo test or store-level MER shows no incremental net revenue when this campaign's budget rises, "
                    "or if blended MER falls as its spend rises.",
                    "attribution", days_in_signal=r.days,
                    facts={"campaign": cid, "claimed_roas_attribution_only": str(q2(pr)), "tw_roas_attribution_only": str(q2(tw)) if tw else ""}))
            for cid, c, pr, tw in rows:
                if tw is not None and tw < 1 and c["r"] >= WASTE_MIN_WEEKLY_SPEND:
                    shortfall = c["r"] * (1 - tw) * 30 / r.days
                    out.proposals.append(Proposal(
                        ctx.tenant_id, ctx.store_id, "campaign_waste", cid,
                        f"{c['name']}: Triple Whale-attributed ROAS {q2(tw)} (<1.0) - waste candidate",
                        f"ATTRIBUTION EVIDENCE ONLY: Triple Whale attributes ${claims.get(('synth_triple_whale', cid), ZERO):,.2f} to ${c['r']:,.2f} spend; "
                        f"platform claims ROAS {q2(pr) if pr else 'n/a'}. Neither is financial truth; assisted/upper-funnel value is not captured.",
                        Action.CHANGE_CAMPAIGN_BUDGET, self.role,
                        ExpectedImpact(shortfall, "spend_at_risk", "attributed shortfall (spend minus Triple Whale revenue), 30d projection"),
                        "Disproved if turning the campaign down (or a holdout) does not reduce store net revenue, or if its first-order "
                        "new-customer share / assisted conversions show value attribution misses.",
                        "attribution", days_in_signal=r.days,
                        facts={"campaign": cid, "claimed_roas_attribution_only": str(q2(pr)) if pr else "", "tw_roas_attribution_only": str(q2(tw))}))
        return out
