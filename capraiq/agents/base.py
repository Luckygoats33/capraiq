"""Agent interface. Agents are deterministic/rule-based; an LLM may later be plugged in ONLY via the
Narrator hook (wording), and may never produce or alter a number (see safe_rewrite)."""
from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional, Protocol

from ..anomalies import DQIssue, Episode
from ..engine.profit import Metrics, ProfitInputs, ProfitReport, ReconResult
from ..models import ZERO, q2
from ..policy import Action, PolicyEngine, Role


@dataclass(frozen=True)
class ExpectedImpact:
    amount: Optional[Decimal]
    kind: str            # profit_at_stake | spend_at_risk | revenue_exposure | attribution_claimed
    basis: str
    horizon_days: int = 30

    def describe(self) -> str:
        if self.amount is None:
            return f"not quantified ({self.basis})"
        return f"${q2(self.amount):,.2f} per {self.horizon_days}d [{self.kind}] - {self.basis}"


KIND_WEIGHT = {"profit_at_stake": 1.0, "spend_at_risk": 0.6, "revenue_exposure": 0.3, "attribution_claimed": 0.25}


@dataclass
class Proposal:
    tenant_id: str
    store_id: str
    kind: str
    key: str
    title: str
    summary: str
    action: Action
    source_role: Role
    impact: ExpectedImpact
    falsifier: str
    evidence_basis: str              # store | attribution | data_gap
    depends_on_costs: bool = False
    unpriced_share: float = 0.0
    days_in_signal: int = 0
    facts: dict = field(default_factory=dict)
    grade: Optional[str] = None
    grade_reasons: list = field(default_factory=list)
    challenges: list = field(default_factory=list)
    disproved: bool = False
    disproved_reason: str = ""

    @property
    def proposal_id(self) -> str:
        return hashlib.sha1(f"{self.tenant_id}|{self.kind}|{self.key}".encode()).hexdigest()[:10]


@dataclass
class Finding:
    role: Role
    text: str


@dataclass
class AgentOutput:
    role: Role
    proposals: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    brief_markdown: str = ""


@dataclass
class AgentContext:
    tenant_id: str
    store_id: str
    tenant_name: str
    synthetic: bool
    inputs: ProfitInputs
    report: ProfitReport
    recon: ReconResult
    dq: list
    episodes: list
    recent: Metrics
    baseline: Metrics
    policy: PolicyEngine
    ledger: object = None
    proposals: list = field(default_factory=list)   # filled by pipeline between agents
    graded: list = field(default_factory=list)
    narrator: object = None

    @property
    def as_of(self) -> date:
        return self.report.end

    def per_day(self, m: Metrics, value: Decimal) -> Decimal:
        return value / m.days

    def monthly(self, m: Metrics, value: Decimal) -> Decimal:
        return value * 30 / m.days

    def recent_episodes(self, *metrics: str) -> list:
        return [e for e in self.episodes if e.metric in metrics and e.end >= self.recent.start]

    def unpriced_share_recent(self) -> float:
        return float(self.recent.unpriced_revenue / self.recent.gross) if self.recent.gross > 0 else 1.0


class Agent(ABC):
    role: Role

    @abstractmethod
    def run(self, ctx: AgentContext) -> AgentOutput: ...


# ------------------------------------------------------------------ LLM guard
class Narrator(Protocol):
    def rewrite(self, text: str) -> str: ...


_NUM = re.compile(r"\d[\d,]*\.?\d*")


def numbers_in(text: str) -> list[str]:
    return sorted(n.replace(",", "") for n in _NUM.findall(text))


def safe_rewrite(narrator: Optional[Narrator], text: str) -> str:
    """Let an LLM re-word text, but reject the rewrite if the multiset of numbers changes."""
    if narrator is None:
        return text
    out = narrator.rewrite(text)
    return out if numbers_in(out) == numbers_in(text) else text
