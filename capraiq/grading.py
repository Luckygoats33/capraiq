"""Evidence grading A-F. Rules are ordered; first match wins. Deterministic, no LLM."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

GRADES = "ABCDEF"
GRADE_WEIGHT = {"A": 1.0, "B": 0.8, "C": 0.5, "D": 0.3, "E": 0.15, "F": 0.05}
MIN_DAYS_STRONG = 7
MIN_DAYS_WEAK = 3
MAX_UNPRICED_SHARE = 0.10


@dataclass
class EvidenceInputs:
    basis: str                         # "store" | "attribution" | "data_gap"
    recon_status: Optional[str]        # reconciled|mismatch|partial|no_source_totals|None
    depends_on_costs: bool
    unpriced_share: float              # share of revenue on the claim's critical path with missing cost (0..1)
    days_in_signal: int
    contradicted: bool = False         # store truth contradicts the claim
    data_missing: bool = False         # critical input absent


@dataclass
class Grade:
    letter: str
    reasons: list[str] = field(default_factory=list)


def grade_evidence(e: EvidenceInputs) -> Grade:
    """A = reconciled to store truth with sourced costs; C = attribution-only; F = missing data."""
    if e.data_missing:
        return Grade("F", ["critical input missing; claim cannot be evaluated"])
    if e.basis == "data_gap":
        return Grade("B", ["gap observed directly in store/cost data (a gap claim is verifiable but is not a profit claim)"])
    if e.contradicted:
        return Grade("D", ["store-platform truth contradicts the claim"])
    if e.recon_status == "mismatch":
        return Grade("D", ["store data does not reconcile to platform totals"])
    if e.basis == "attribution":
        return Grade("C", ["attribution-only evidence (ad platform / Triple Whale claims are not financial truth)"])
    if e.days_in_signal < MIN_DAYS_WEAK:
        return Grade("E", [f"signal seen on only {e.days_in_signal} day(s); treat as anecdotal"])
    if e.depends_on_costs and e.unpriced_share > MAX_UNPRICED_SHARE:
        return Grade("D", [f"{e.unpriced_share:.0%} of relevant revenue has no cost on file (> {MAX_UNPRICED_SHARE:.0%})"])
    if e.recon_status != "reconciled":
        return Grade("C", ["store data not fully reconciled to platform totals (" + str(e.recon_status) + ")"])
    reasons = ["reconciled to store-platform totals"]
    if e.depends_on_costs and e.unpriced_share > 0:
        return Grade("B", reasons + [f"{e.unpriced_share:.1%} of relevant revenue has no cost on file (below threshold, flagged)"])
    if e.days_in_signal < MIN_DAYS_STRONG:
        return Grade("B", reasons + [f"signal persisted {e.days_in_signal} day(s) (< {MIN_DAYS_STRONG} for A)"])
    reasons.append("costs sourced" if e.depends_on_costs else "claim does not depend on cost inputs")
    reasons.append(f"signal persisted {e.days_in_signal} days")
    return Grade("A", reasons)


def cap(current: str, ceiling: str) -> str:
    """Grades only go down: return the worse of the two."""
    return max(current, ceiling, key=GRADES.index)
