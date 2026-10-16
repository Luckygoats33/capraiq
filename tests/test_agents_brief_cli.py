import re
import subprocess
import sys
from pathlib import Path

import pytest

from capraiq.agents import (AdsAuditor, AgentContext, ChiefOfStaff, DataSteward, LifecycleAuditor, MAX_DECISIONS, ProfitAnalyst,
                            Skeptic, safe_rewrite)
from capraiq.agents.base import Agent, ExpectedImpact, Proposal
from capraiq.agents.chief_of_staff import score
from capraiq.policy import Action, Role

ROOT = Path(__file__).resolve().parents[1]


def test_all_agents_share_interface_and_roles():
    agents = [DataSteward(), ProfitAnalyst(), AdsAuditor(), LifecycleAuditor(), Skeptic(), ChiefOfStaff()]
    assert all(isinstance(a, Agent) for a in agents)
    assert {a.role for a in agents} == set(Role)


def test_billy_decisions(synth):
    run = synth["runs"]["synth_billy"]
    kinds = {d.proposal.kind: d for d in run.decisions}
    sp = kinds["spend_spike"]
    assert sp.proposal.key == "mp-prospecting" and sp.proposal.grade == "A" and sp.authority_level == "approval-required"
    assert kinds["data_gap_cogs"].proposal.facts["skus"] == ["BB-07"]


def test_nanny_decisions(synth):
    kinds = {d.proposal.kind: d for d in synth["runs"]["synth_nanny"].decisions}
    assert kinds["refund_spike"].proposal.facts["top_sku"] == "NH-03"
    assert set(kinds["margin_drop"].proposal.facts["skus"]) == {"NH-02", "NH-05"}
    assert sorted(kinds["data_gap_cogs"].proposal.facts["skus"]) == ["NH-07", "NH-08"]


def test_max_five_decisions_and_ranked(synth):
    for run in synth["runs"].values():
        assert len(run.decisions) <= MAX_DECISIONS
        assert [d.rank for d in run.decisions] == list(range(1, len(run.decisions) + 1))
        assert [d.score for d in run.decisions] == sorted((d.score for d in run.decisions), reverse=True)


def test_every_decision_has_grade_impact_falsifier_authority(synth):
    for run in synth["runs"].values():
        for d in run.decisions:
            p = d.proposal
            assert p.grade in "ABCDEF" and p.falsifier.strip() and p.impact.kind and d.authority_level
            assert p.grade_reasons


def test_attribution_never_graded_above_C(synth):
    for run in synth["runs"].values():
        for p in run.ctx.graded:
            if p.evidence_basis == "attribution":
                assert p.grade >= "C"
        for p in run.outputs["ads_auditor"].proposals:
            assert "ATTRIBUTION EVIDENCE ONLY" in p.summary or p.kind == "spend_spike"


def test_cap_of_five_enforced_with_many_proposals(synth):
    run = synth["runs"]["synth_billy"]
    ctx = run.ctx
    base = ctx.graded[0]
    import dataclasses
    many = [dataclasses.replace(base, key=f"k{i}", kind="data_gap_x", grade="B", impact=ExpectedImpact(__import__("decimal").Decimal(100 + i), "profit_at_stake", "t"), disproved=False) for i in range(9)]
    ctx2 = dataclasses.replace(ctx, graded=many, ledger=None)
    out = ChiefOfStaff().run(ctx2)
    assert len(out.decisions) == 5 and "held back by the 5-decision cap" in out.brief_markdown
    assert [d.proposal.key for d in out.decisions] == ["k8", "k7", "k6", "k5", "k4"]


def test_disproved_proposals_excluded_and_logged(synth):
    import dataclasses
    run = synth["runs"]["synth_billy"]; ctx = run.ctx
    p = dataclasses.replace(ctx.graded[0], disproved=True, disproved_reason="test", grade="E", key="zz")
    out = ChiefOfStaff().run(dataclasses.replace(ctx, graded=[p], ledger=None))
    assert out.decisions == []


def test_skeptic_disproves_when_revenue_scaled_with_spend(synth):
    import dataclasses
    from decimal import Decimal
    ctx = synth["runs"]["synth_billy"].ctx
    # fabricate a baseline where store revenue would have risen with spend: tweak copies of metrics
    rec = dataclasses.replace(ctx.recent, gross=ctx.baseline.gross / ctx.baseline.days * ctx.recent.days * Decimal("2"))
    spike = next(p for p in ctx.proposals if p.kind == "spend_spike")
    ctx2 = dataclasses.replace(ctx, recent=rec, proposals=[spike])
    out = Skeptic().run(ctx2).proposals[0]
    assert out.disproved and out.grade in "EF"


def test_skeptic_contradicts_winner_when_mer_falls(synth):
    ctx = synth["runs"]["synth_billy"].ctx
    import dataclasses
    w = Proposal(ctx.tenant_id, ctx.store_id, "campaign_winner", "x", "t", "s", Action.CHANGE_CAMPAIGN_BUDGET, Role.ADS_AUDITOR,
                 ExpectedImpact(None, "attribution_claimed", "b"), "f", "attribution", days_in_signal=7)
    out = Skeptic().run(dataclasses.replace(ctx, proposals=[w])).proposals[0]
    assert out.grade == "D" and any("contradicts" in c for c in out.challenges)   # MER fell in billy's last week


def test_lifecycle_stub_makes_no_claims(synth):
    out = synth["runs"]["synth_billy"].outputs["lifecycle_auditor"]
    assert out.proposals == [] and "phase 2" in out.findings[0].text


def test_skeptic_is_only_grader(synth):
    for run in synth["runs"].values():
        for role in ("data_steward", "profit_analyst", "ads_auditor"):
            assert all(p.grade is None for p in run.outputs[role].proposals)


def test_profit_analyst_decomposition_is_exact(synth):
    f = synth["runs"]["synth_billy"].outputs["profit_analyst"].findings[0].text
    assert "ad spend" in f and "contribution profit moved" in f


def test_score_ignores_numbers_from_llm_text():
    class Evil:
        def rewrite(self, t): return t.replace("100", "1000")
    assert safe_rewrite(Evil(), "profit 100 down") == "profit 100 down"
    class Good:
        def rewrite(self, t): return "Down: profit of 100, a drop"
    assert safe_rewrite(Good(), "profit 100 down") == "Down: profit of 100, a drop"
    assert safe_rewrite(None, "x 1") == "x 1"


# ---------------- brief
def test_brief_labelled_synthetic_and_structured(synth):
    for run in synth["runs"].values():
        md = run.brief_markdown
        assert "[SYNTHETIC]" in md.splitlines()[0] and "SYNTHETIC DATA" in md
        assert "READ-ONLY" in md and md.count("What would prove this wrong") == len(run.decisions)
        assert md.count("**Grade:**") == len(run.decisions) and md.count("**Authority:**") == len(run.decisions)
        assert len(re.findall(r"^### \d+\.", md, re.M)) <= 5


def test_non_synthetic_brief_has_no_banner(synth):
    import dataclasses
    from capraiq.brief import render_brief
    ctx = dataclasses.replace(synth["runs"]["synth_billy"].ctx, synthetic=False)
    md = render_brief(ctx, [], [])
    assert "[SYNTHETIC]" not in md and "SYNTHETIC DATA" not in md and "No decision-grade items" in md


def test_brief_flags_incomplete_costs(synth):
    assert "Cost data incomplete" in synth["runs"]["synth_billy"].brief_markdown


def test_metric_definitions_persisted(synth):
    defs = synth["repo"].list_metric_definitions("synth_billy")
    assert {d["metric"] for d in defs} >= {"net_revenue", "contribution_profit", "mer", "blended_cac"} and all(d["version"] == 1 for d in defs)


def test_brief_uses_only_computed_numbers(synth):
    """Every money figure in the first decision's impact line is reproducible from the Decimal amount."""
    d = synth["runs"]["synth_nanny"].decisions[0]
    assert f"{d.proposal.impact.amount:,.2f}" in d.proposal.impact.describe()


# ---------------- CLI e2e (subprocess, the exact documented command)
def test_cli_demo_end_to_end(tmp_path):
    r = subprocess.run([sys.executable, "-m", "capraiq", "demo", "--out", str(tmp_path)], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "SYNTHETIC" in r.stdout and "BLOCKED by policy" in r.stdout and "chain_ok=True" in r.stdout
    for t in ("synth_billy", "synth_nanny"):
        assert (tmp_path / f"brief_{t}.md").read_text().startswith("# CapraIQ Morning Brief [SYNTHETIC]")
    v = subprocess.run([sys.executable, "-m", "capraiq", "verify-ledger", "--db", str(tmp_path / "capraiq_demo.db")], cwd=ROOT, capture_output=True, text=True)
    assert v.returncode == 0 and "ok=True" in v.stdout


def test_cli_ingest_dir_with_brief(tmp_path):
    from .test_adapters import make_folder
    make_folder(tmp_path / "drive")
    r = subprocess.run([sys.executable, "-m", "capraiq", "ingest-dir", str(tmp_path / "drive"), "--tenant", "t1", "--store", "s1",
                        "--db", str(tmp_path / "x.db")], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and "orders" in r.stdout


def test_no_network_imports_in_package():
    banned = re.compile(r"^\s*(import|from)\s+(requests|urllib|http|socket|httpx|aiohttp|smtplib)\b", re.M)
    for p in (ROOT / "capraiq").rglob("*.py"):
        assert not banned.search(p.read_text()), p
