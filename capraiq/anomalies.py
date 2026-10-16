"""Statistical anomaly rules (rolling robust z / MAD) + data-quality anomalies. Deterministic."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from .engine.profit import ProfitInputs, ProfitReport, ReconResult
from .models import ZERO, day_of

# metric -> (getter, bad direction)
METRICS = {
    "contribution_profit": (lambda m: m.contribution_profit, "down"),
    "mer": (lambda m: m.mer, "down"),
    "cac": (lambda m: m.cac, "up"),
    "ad_spend": (lambda m: m.ad_spend, "up"),
    "refund_rate": (lambda m: m.refund_rate, "up"),
}
Z_THRESHOLD = 3.5
MIN_HISTORY = 14
WINDOW = 28
MIN_REL_CHANGE = 0.15


def robust_z(history: list[float], x: float) -> Optional[tuple[float, float]]:
    """Return (z, median) using MAD (scaled by 1.4826); falls back to mean abs deviation, then a 5%-of-median floor.
    None only when history is too short."""
    if len(history) < MIN_HISTORY:
        return None
    med = statistics.median(history)
    mad = statistics.median([abs(v - med) for v in history])
    scale = 1.4826 * mad
    if scale == 0:
        meanad = sum(abs(v - med) for v in history) / len(history)
        scale = 1.2533 * meanad
    if scale == 0:  # flat history (e.g. refund rate always 0): any real move is a deviation; floor the scale
        scale = max(abs(med) * 0.05, 1e-9)
    return (x - med) / scale, med


@dataclass
class Anomaly:
    metric: str
    day: date
    value: float
    baseline: float
    z: float
    direction: str  # "up" | "down"


@dataclass
class Episode:
    metric: str
    start: date
    end: date
    days: int
    peak_z: float
    direction: str
    peak_value: float
    baseline: float


def detect(report: ProfitReport, lookback_days: Optional[int] = None, z_threshold: float = Z_THRESHOLD,
           window: int = WINDOW, min_rel_change: float = MIN_REL_CHANGE) -> list[Anomaly]:
    """Flag days whose value is a bad-direction outlier vs the trailing window (excluding the day itself)."""
    out: list[Anomaly] = []
    for name, (get, bad) in METRICS.items():
        series = [(m.start, get(m)) for m in report.days]
        vals = [(d, float(v)) for d, v in series if v is not None]
        floor = report.end - timedelta(days=lookback_days - 1) if lookback_days else report.start
        for i, (d, x) in enumerate(vals):
            if d < floor:
                continue
            hist = [v for _, v in vals[max(0, i - window):i]]
            rz = robust_z(hist, x)
            if rz is None:
                continue
            z, med = rz
            direction = "up" if z > 0 else "down"
            if direction != bad or abs(z) < z_threshold:
                continue
            if med != 0 and abs(x - med) / abs(med) < min_rel_change:
                continue
            out.append(Anomaly(name, d, x, med, z, direction))
    return out


def episodes(anoms: list[Anomaly], max_gap_days: int = 1) -> list[Episode]:
    out: list[Episode] = []
    for metric in {a.metric for a in anoms}:
        rows = sorted((a for a in anoms if a.metric == metric), key=lambda a: a.day)
        cur = [rows[0]]
        for a in rows[1:]:
            if (a.day - cur[-1].day).days <= max_gap_days + 0:
                cur.append(a)
            else:
                out.append(_ep(metric, cur)); cur = [a]
        out.append(_ep(metric, cur))
    return sorted(out, key=lambda e: (e.start, e.metric))


def _ep(metric, rows):
    peak = max(rows, key=lambda a: abs(a.z))
    return Episode(metric, rows[0].day, rows[-1].day, len(rows), peak.z, peak.direction, peak.value, peak.baseline)


# ------------------------------------------------------------ data-quality anomalies
@dataclass
class DQIssue:
    kind: str
    severity: str  # high | medium | low
    detail: str
    keys: list[str] = field(default_factory=list)
    count: int = 0


def data_quality(inputs: ProfitInputs, report: ProfitReport, recon: ReconResult) -> list[DQIssue]:
    issues: list[DQIssue] = []
    for mi in report.missing:
        if mi.kind == "cogs":
            issues.append(DQIssue("missing_cogs", "high",
                                  f"SKU {mi.key}: no COGS on file; {mi.units} units / {mi.revenue:.2f} list revenue unpriced "
                                  f"(contribution profit overstated by the unknown cost; NOT estimated)", [mi.key], mi.units))
        else:
            issues.append(DQIssue(f"missing_{mi.kind}", "high",
                                  f"No {mi.kind} cost configured for {mi.units} orders; contribution profit overstated", [], mi.units))
    if recon.status == "mismatch":
        issues.append(DQIssue("reconciliation_mismatch", "high",
                              f"{len(recon.diffs)} day/field differences vs store platform totals (tolerance {recon.tolerance})",
                              sorted({f"{d.day}:{d.field}" for d in recon.diffs})[:10], len(recon.diffs)))
    elif recon.status == "partial":
        issues.append(DQIssue("reconciliation_partial", "medium", f"Source totals cover {recon.coverage:.0%} of order days"))
    elif recon.status == "no_source_totals":
        issues.append(DQIssue("reconciliation_unavailable", "medium", "No store-platform source totals loaded; cannot reconcile"))

    counts = [m.orders for m in report.days]
    med = statistics.median(counts) if counts else 0
    zero_days = [m.start.isoformat() for m in report.days if m.orders == 0]
    if med >= 5 and zero_days:
        issues.append(DQIssue("order_gap_days", "high", f"{len(zero_days)} day(s) with zero orders (median {med:.0f}/day)", zero_days[:10], len(zero_days)))
    spend_days = {a.day for a in inputs.ad_spend}
    if spend_days:
        gap = [m.start.isoformat() for m in report.days if m.start.isoformat() not in spend_days]
        if gap:
            issues.append(DQIssue("ad_spend_gap_days", "medium", f"{len(gap)} day(s) without any ad spend rows", gap[:10], len(gap)))
    order_ids = {o.source_id for o in inputs.orders}
    orphans = [r.source_id for r in inputs.refunds if r.order_source_id not in order_ids]
    if orphans:
        issues.append(DQIssue("orphan_refunds", "medium", f"{len(orphans)} refund(s) reference unknown orders", orphans[:10], len(orphans)))
    bad_lines = [o.source_id for o in inputs.orders if o.lines and abs(sum((l.unit_price * l.quantity for l in o.lines), ZERO) - o.gross) > Decimal("0.01")]
    if bad_lines:
        issues.append(DQIssue("order_line_total_mismatch", "medium", f"{len(bad_lines)} order(s) whose lines do not sum to gross", bad_lines[:10], len(bad_lines)))
    if inputs.products:
        known = {p.sku for p in inputs.products}
        unknown = sorted({l.sku for o in inputs.orders for l in o.lines if l.sku not in known})
        if unknown:
            issues.append(DQIssue("unknown_sku", "medium", f"{len(unknown)} SKU(s) on orders not in product catalogue", unknown[:10], len(unknown)))
    if report.total.guest_orders:
        issues.append(DQIssue("guest_orders", "low", f"{report.total.guest_orders} orders without customer id are excluded from new/returning split", [], report.total.guest_orders))
    neg = [o.source_id for o in inputs.orders if o.gross < 0 or o.discounts < 0]
    if neg:
        issues.append(DQIssue("negative_values", "high", f"{len(neg)} order(s) with negative gross/discount", neg[:10], len(neg)))
    return issues
