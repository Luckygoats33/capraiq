"""Morning brief renderer (markdown). Max 5 decisions. SYNTHETIC banner when sample data."""
from __future__ import annotations

from .models import q2

SYN_BANNER = ("> **SYNTHETIC DATA** - every number below comes from generated sample data. "
              "No real client, store, or platform was accessed.")


def _m(v) -> str:
    return "n/a" if v is None else f"{v:,.2f}"


def render_brief(ctx, decisions, overflow) -> str:
    r, b = ctx.recent, ctx.baseline
    tag = " [SYNTHETIC]" if ctx.synthetic else ""
    L = [f"# CapraIQ Morning Brief{tag} - {ctx.tenant_name}", ""]
    if ctx.synthetic:
        L += [SYN_BANNER, ""]
    L += [f"**As of:** {ctx.as_of} | **Window:** last {r.days}d ({r.start} to {r.end}) vs prior {b.days}d | "
          f"**Mode:** v1 READ-ONLY - nothing in this brief is executed; every action needs a human.", "",
          "## Snapshot (store platform = financial truth)", "",
          "| Metric | Last 7d | Prior 28d | Per-day basis |", "|---|---:|---:|---|"]
    def row(name, f, fmt=_m):
        L.append(f"| {name} | {fmt(f(r))} | {fmt(f(b))} | {'avg/day' if name in ('Net revenue','Contribution profit','Ad spend') else 'ratio'} |")
    row("Net revenue", lambda m: m.net_revenue / m.days)
    row("Contribution profit", lambda m: m.contribution_profit / m.days)
    row("Ad spend", lambda m: m.ad_spend / m.days)
    row("MER", lambda m: m.mer)
    row("Blended CAC", lambda m: m.cac)
    row("Refund rate", lambda m: m.refund_rate, lambda v: "n/a" if v is None else f"{v:.2%}")
    if not r.cost_complete:
        L += ["", f"> **Cost data incomplete** - missing: {', '.join(r.missing)}. Contribution profit excludes these costs "
                  f"(not estimated) and is therefore overstated."]
    L += ["", f"## Decisions ({len(decisions)} of max 5, ranked)", ""]
    if not decisions:
        L.append("_No decision-grade items today._")
    for d in decisions:
        p = d.proposal
        L += [f"### {d.rank}. {p.title}", "",
              f"- **Grade:** {p.grade} - " + "; ".join(p.grade_reasons),
              f"- **Expected impact:** {p.impact.describe()}",
              f"- **What would prove this wrong:** {p.falsifier}",
              f"- **Authority:** {d.authority_level} for `{p.action.value}` (v1: advisory only, not executed)",
              f"- **Evidence basis:** {p.evidence_basis} | raised by {p.source_role.value}; days in signal: {p.days_in_signal}",
              f"- **Detail:** {p.summary}"]
        if p.challenges:
            L.append("- **Skeptic's checks:** " + " | ".join(p.challenges))
        L.append("")
    if overflow:
        L += [f"_{len(overflow)} further item(s) held back by the 5-decision cap (logged in the ledger)._", ""]
    L += ["## Data quality", ""]
    L.append(f"- Reconciliation vs store platform: **{ctx.recon.status}** ({ctx.recon.days_checked} days)")
    shown = [i for i in ctx.dq if i.kind != "reconciliation_mismatch"]
    for i in shown:
        L.append(f"- [{i.severity}] {i.detail}")
    if not shown:
        L.append("- No other data-quality issues detected.")
    L += ["", "## Metric definitions", ""]
    from .engine.profit import METRIC_DEFINITIONS
    L += [f"- `{k}` v1: {v}" for k, v in METRIC_DEFINITIONS.items()]
    L += ["", "---", "_Numbers are computed by deterministic code. Agents investigate and propose; humans decide._"]
    return "\n".join(L) + "\n"
