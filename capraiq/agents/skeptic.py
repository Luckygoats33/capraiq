from __future__ import annotations

import dataclasses
from decimal import Decimal

from ..grading import EvidenceInputs, cap, grade_evidence
from ..models import ZERO
from ..policy import Action, Role
from .base import Agent, AgentContext, AgentOutput, Finding, Proposal

CORROBORATING = {"spend_spike": ("ad_spend", "mer"), "margin_drop": ("contribution_profit",),
                 "refund_spike": ("refund_rate",)}


class Skeptic(Agent):
    """Tries to disprove every proposal and is the ONLY role that sets the grade."""
    role = Role.SKEPTIC

    def run(self, ctx: AgentContext) -> AgentOutput:
        ctx.policy.enforce(self.role, Action.READ_METRICS)
        out = AgentOutput(self.role)
        for p in ctx.proposals:
            q = dataclasses.replace(p, challenges=list(p.challenges), grade_reasons=[])
            contradicted = self._challenge(ctx, q)
            g = grade_evidence(EvidenceInputs(
                basis=q.evidence_basis, recon_status=ctx.recon.status, depends_on_costs=q.depends_on_costs,
                unpriced_share=q.unpriced_share, days_in_signal=q.days_in_signal, contradicted=contradicted,
                data_missing=not ctx.inputs.orders))
            letter, reasons = g.letter, list(g.reasons)
            if q.disproved:
                letter = cap(letter, "E"); reasons.append(f"skeptic disproved: {q.disproved_reason}")
            elif q.kind in CORROBORATING and not ctx.recent_episodes(*CORROBORATING[q.kind]):
                new = cap(letter, "B")
                if new != letter:
                    reasons.append("not corroborated by a statistical anomaly vs 28d history -> capped at B")
                letter = new
            q.grade, q.grade_reasons = letter, reasons
            out.proposals.append(q)
            out.findings.append(Finding(self.role, f"{q.kind}:{q.key} graded {letter}; challenges: {len(q.challenges)}; disproved: {q.disproved}"))
        return out

    def _challenge(self, ctx: AgentContext, q: Proposal) -> bool:
        r, b = ctx.recent, ctx.baseline
        contradicted = False
        if ctx.recent.missing and q.depends_on_costs:
            q.challenges.append(f"store-wide cost gaps exist ({', '.join(r.missing)}); store-level profit is overstated, SKU-level claim unaffected")
        if q.kind == "spend_spike":
            spend_g = (r.ad_spend / r.days) / (b.ad_spend / b.days) - 1 if b.ad_spend > 0 else ZERO
            rev_g = (r.net_revenue / r.days) / (b.net_revenue / b.days) - 1 if b.net_revenue > 0 else ZERO
            mer_ok = r.mer is not None and b.mer is not None and r.mer >= b.mer * Decimal("0.9")
            if spend_g > 0 and rev_g >= spend_g / 2 and mer_ok:
                q.disproved = True
                q.disproved_reason = f"store revenue rose {rev_g:+.0%} alongside spend {spend_g:+.0%}, MER held"
            else:
                q.challenges.append(f"checked revenue lift: store revenue {rev_g:+.0%} vs account spend {spend_g:+.0%}, MER {r.mer} vs {b.mer} - not proportional")
            if q.facts.get("claimed_roas_attribution_only"):
                q.challenges.append("platform-claimed ROAS ignored for grading (attribution is evidence only)")
        elif q.kind == "refund_spike":
            single = Decimal(q.facts.get("largest_single_refund", "0"))
            tot = Decimal(q.facts.get("recent_refunds", "0"))
            if tot > 0 and single / tot > Decimal("0.4"):
                q.disproved = True
                q.disproved_reason = f"one refund is {single / tot:.0%} of the window total"
            else:
                q.challenges.append(f"checked outlier: largest single refund is {(single / tot if tot else 0):.0%} of window total - not a single-order artefact")
        elif q.kind == "margin_drop":
            if q.days_in_signal < 3:
                q.challenges.append("margin gap seen on <3 days; may be mix noise")
            else:
                q.challenges.append(f"checked persistence: margin below baseline on {q.days_in_signal}/{r.days} recent days")
        elif q.kind in ("campaign_winner", "campaign_waste"):
            if q.kind == "campaign_winner" and r.mer is not None and b.mer is not None and r.mer < b.mer * Decimal("0.9"):
                contradicted = True
                q.challenges.append(f"store truth contradicts: blended MER fell {b.mer} -> {r.mer} while this campaign 'looks efficient'")
            pr, tw = q.facts.get("claimed_roas_attribution_only"), q.facts.get("tw_roas_attribution_only")
            if pr and tw and Decimal(pr) >= Decimal("1.5") * Decimal(tw):
                q.challenges.append(f"attribution sources disagree (platform {pr} vs Triple Whale {tw}); neither is financial truth")
            q.challenges.append("no store-level incrementality evidence available in v1 -> cannot exceed C")
        elif q.kind == "data_gap_cogs":
            still = {m.key for m in ctx.report.missing if m.kind == "cogs"}
            if not set(q.facts.get("skus", [])) & still:
                q.disproved = True
                q.disproved_reason = "gap no longer present on recomputation"
            else:
                q.challenges.append("re-verified: gap reproduces from the cost book")
        return contradicted
