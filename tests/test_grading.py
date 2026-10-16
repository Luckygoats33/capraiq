import pytest

from capraiq.grading import GRADES, EvidenceInputs, cap, grade_evidence


def g(**kw):
    base = dict(basis="store", recon_status="reconciled", depends_on_costs=True, unpriced_share=0.0, days_in_signal=7)
    base.update(kw)
    return grade_evidence(EvidenceInputs(**base))


def test_A_reconciled_sourced_costs_persistent():
    r = g(); assert r.letter == "A" and r.reasons


def test_A_requires_costs_sourced_when_relevant_but_not_otherwise():
    assert g(depends_on_costs=False, unpriced_share=0.5).letter == "A"
    assert g(unpriced_share=0.03).letter == "B"


def test_B_short_persistence():
    assert g(days_in_signal=4).letter == "B"


def test_C_attribution_only_even_if_reconciled():
    assert g(basis="attribution").letter == "C"


def test_C_unreconciled_store_data():
    for s in ("no_source_totals", "partial", None):
        assert g(recon_status=s).letter == "C"


def test_D_mismatch_contradiction_unpriced():
    assert g(recon_status="mismatch").letter == "D"
    assert g(contradicted=True).letter == "D"
    assert g(unpriced_share=0.25).letter == "D"
    assert g(basis="attribution", contradicted=True).letter == "D"


def test_E_weak_signal():
    assert g(days_in_signal=2).letter == "E"


def test_F_missing_data_wins_over_everything():
    assert g(data_missing=True, contradicted=True).letter == "F"


def test_data_gap_claim_is_B():
    assert g(basis="data_gap").letter == "B"


def test_cap_only_lowers():
    assert cap("A", "B") == "B" and cap("D", "B") == "D" and cap("C", "C") == "C"


@pytest.mark.parametrize("days", range(0, 12))
@pytest.mark.parametrize("basis", ["store", "attribution"])
def test_grade_always_valid_and_attribution_never_above_C(days, basis):
    r = g(days_in_signal=days, basis=basis)
    assert r.letter in GRADES
    if basis == "attribution":
        assert r.letter >= "C"
