from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..grading import GRADE_WEIGHT
from ..policy import Action, Role
from .base import KIND_WEIGHT, Agent, AgentContext, AgentOutput, Finding, Proposal

MAX_DECISIONS = 5


@dataclass
class Decision:
    decision_id: str
    rank: int
    proposal: Proposal
    authority_level: str
    score: float

    def as_record(self, brief_date) -> dict:
        p = self.proposal
        return {"decision_id": self.decision_id, "brief_date": str(brief_date), "rank": self.rank, "kind": p.kind,
                "title": p.title, "grade": p.grade, "impact_amount": None if p.impact.amount is None else str(round(p.impact.amount, 2)),
                "impact_kind": p.impact.kind, "falsifier": p.falsifier, "authority_level": self.authority_level,
                "action": p.action.value, "role": p.source_role.value, "evidence_basis": p.evidence_basis,
                "grade_reasons": p.grade_reasons, "challenges": p.challenges, "executed": False}


def score(p: Proposal) -> float:
    amt = float(p.impact.amount) if p.impact.amount is not None else 0.0
    return amt * GRADE_WEIGHT[p.grade] * KIND_WEIGHT.get(p.impact.kind, 0.25)


class ChiefOfStaff(Agent):
    """Ranks graded proposals, keeps at most 5, logs decisions to the ledger, writes the brief."""
    role = Role.CHIEF_OF_STAFF

    def run(self, ctx: AgentContext) -> AgentOutput:
        from ..brief import render_brief  # local import: brief depends on Decision
        pol = ctx.policy
        pol.enforce(self.role, Action.READ_METRICS)
        out = AgentOutput(self.role)
        live = [p for p in ctx.graded if not p.disproved]
        best: dict[tuple, Proposal] = {}
        for p in live:  # dedupe on (kind,key): keep best score
            k = (p.kind, p.key)
            if k not in best or score(p) > score(best[k]):
                best[k] = p
        ranked = sorted(best.values(), key=lambda p: (-score(p), p.kind, p.key))
        shown, overflow = ranked[:MAX_DECISIONS], ranked[MAX_DECISIONS:]
        decisions = []
        for i, p in enumerate(shown, 1):
            pol.enforce(self.role, Action.DRAFT_RECOMMENDATION)
            level = pol.level_for(p.action).label
            decisions.append(Decision(f"{ctx.as_of}-{p.proposal_id}", i, p, level, score(p)))
        if ctx.ledger is not None:
            pol.enforce(self.role, Action.WRITE_DECISION_LOG)
            for d in decisions:
                ctx.ledger.record_decision(ctx.tenant_id, ctx.store_id, d.as_record(ctx.as_of))
            for p in ctx.graded:
                if p.disproved:
                    ctx.ledger.append(ctx.tenant_id, "proposal_rejected_by_skeptic",
                                      {"proposal_id": p.proposal_id, "kind": p.kind, "key": p.key, "reason": p.disproved_reason})
            for p in overflow:
                ctx.ledger.append(ctx.tenant_id, "proposal_not_shown",
                                  {"proposal_id": p.proposal_id, "kind": p.kind, "key": p.key, "grade": p.grade, "reason": "beyond daily cap of 5"})
        out.decisions = decisions
        out.findings.append(Finding(self.role, f"{len(decisions)} decision(s) shown, {len(overflow)} held back, "
                                               f"{sum(1 for p in ctx.graded if p.disproved)} rejected by skeptic."))
        out.brief_markdown = render_brief(ctx, decisions, overflow)
        return out
