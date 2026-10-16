from datetime import date
from decimal import Decimal as Dd

import pytest

from capraiq.engine import (CostBook, ProfitInputs, aggregate, cohort_ltv, compute_profit, reconcile, sku_economics)
from capraiq.models import SourceTotal
from .helpers import cost, full_costs, order, prov, refund, spend

D1, D2, D3 = date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 3)


def inp(orders=(), refunds=(), costs=None, ads=(), customers=(), totals=()):
    return ProfitInputs(orders=list(orders), refunds=list(refunds), costs=full_costs() if costs is None else list(costs),
                        ad_spend=list(ads), customers=list(customers), source_totals=list(totals))


def test_basic_contribution_profit_exact_decimal():
    o = order("1", D1, lines=(("A", 2, "10.00"),), disc="2.00")  # gross 20, disc 2
    r = compute_profit(inp([o], [refund("r", "1", D1, "3.00")], ads=[spend(D1, "5.00")]), D1, D1)
    m = r.total
    assert (m.gross, m.discounts, m.refunds) == (Dd("20.00"), Dd("2.00"), Dd("3.00"))
    assert m.net_revenue == Dd("15.00")
    assert m.cogs == Dd("8.00") and m.shipping == Dd("3.00")
    assert m.fees == Dd("18.00") * Dd("0.03") + Dd("0.30") == Dd("0.8400")
    assert m.contribution_profit == Dd("15.00") - 8 - 3 - Dd("0.84") - 5 == Dd("-1.84")
    assert m.cost_complete and m.mer == Dd("3.0000")
    assert isinstance(m.contribution_profit, Dd)


def test_decimal_no_float_drift():
    os_ = [order(str(i), D1, cust=f"c{i}", lines=(("A", 1, "0.10"),)) for i in range(30)]
    m = compute_profit(inp(os_, costs=[cost("cogs", "per_unit", "0.1", sku="A"), cost("shipping", "per_order", "0"), cost("fees", "per_order", "0")]), D1, D1).total
    assert m.gross == Dd("3.00") and m.cogs == Dd("3.0") and m.contribution_profit == Dd("0.00")


def test_cancelled_orders_excluded():
    r = compute_profit(inp([order("1", D1), order("2", D1, status="cancelled")]), D1, D1)
    assert r.total.orders == 1 and r.total.gross == Dd("10.00")


def test_missing_cogs_flagged_not_estimated():
    o = order("1", D1, lines=(("A", 1, "10.00"), ("Z", 3, "20.00")))
    r = compute_profit(inp([o]), D1, D1)
    assert r.total.cogs == Dd("4.00")                      # only A priced; Z NOT estimated
    assert not r.total.cost_complete and "cogs:Z" in r.total.missing
    assert r.total.unpriced_revenue == Dd("60.00")
    mi = [m for m in r.missing if m.kind == "cogs"][0]
    assert (mi.key, mi.units, mi.revenue, mi.days) == ("Z", 3, Dd("60.00"), 1)


def test_missing_shipping_and_fees_flagged():
    r = compute_profit(inp([order("1", D1)], costs=[cost("cogs", "per_unit", "4", sku="A")]), D1, D1)
    assert set(r.total.missing) == {"shipping", "fees"} and r.total.shipping == 0 and r.total.fees == 0


def test_only_one_fee_component_is_not_flagged():
    r = compute_profit(inp([order("1", D1)], costs=[c for c in full_costs() if not (c.cost_type == "fees" and c.basis == "per_order")]), D1, D1)
    assert "fees" not in r.total.missing and r.total.fees == Dd("0.30")


def test_versioned_costs_effective_dating_and_ties():
    book = CostBook([cost("cogs", "per_unit", "4", sku="A", eff="2026-01-01"),
                     cost("cogs", "per_unit", "6", sku="A", eff="2026-03-02", version=2),
                     cost("cogs", "per_unit", "9", sku="A", eff="2026-03-02", version=3)])
    assert book.resolve("cogs", "per_unit", "A", D1).amount == 4
    assert book.resolve("cogs", "per_unit", "A", D2).amount == 9       # tie -> highest version
    assert book.resolve("cogs", "per_unit", "A", date(2025, 1, 1)) is None   # before any effective date
    assert book.resolve("cogs", "per_unit", "B", D1) is None


def test_cogs_uses_version_effective_on_order_day():
    cs = full_costs() + [cost("cogs", "per_unit", "6.00", sku="A", eff="2026-03-02", version=2)]
    r = compute_profit(inp([order("1", D1), order("2", D2, cust="c2")], costs=cs), D1, D2)
    assert r.days[0].cogs == 4 and r.days[1].cogs == 6


def test_refund_counted_on_refund_day_cash_basis():
    r = compute_profit(inp([order("1", D1)], [refund("r", "1", D2, "10.00")]), D1, D2)
    assert r.days[0].refunds == 0 and r.days[1].refunds == Dd("10.00")
    assert r.days[1].net_revenue == Dd("-10.00") and r.total.net_revenue == 0


def test_refund_outside_window_ignored_and_empty_day_zero():
    r = compute_profit(inp([order("1", D1)], [refund("r", "1", D3, "10.00")]), D1, D2)
    assert r.total.refunds == 0 and r.days[1].orders == 0 and r.days[1].mer is None and r.days[1].refund_rate is None


def test_zero_spend_and_zero_new_customers_give_none():
    r = compute_profit(inp([order("1", D1)]), D1, D1)
    assert r.total.mer is None
    r2 = compute_profit(inp([order("1", D1, cust="c1"), order("2", D2, cust="c1")], ads=[spend(D2, "10")]), D2, D2)
    assert r2.total.new_customers == 0 and r2.total.cac is None and r2.total.returning_customers == 1


def test_new_customer_logic_across_window_boundary():
    os_ = [order("1", D1, cust="c1"), order("2", D2, cust="c1"), order("3", D2, cust="c2"), order("4", D2, cust=None)]
    r = compute_profit(inp(os_, ads=[spend(D1, "10"), spend(D2, "30")]), D1, D2)
    assert [d.new_customers for d in r.days] == [1, 1] and r.days[1].returning_customers == 1 and r.days[1].guest_orders == 1
    assert r.total.cac == Dd("20.00")        # 40 / 2 new customers
    # window starting D2 uses full history: c1 is returning there
    r2 = compute_profit(inp(os_, ads=[spend(D2, "30")]), D2, D2)
    assert r2.total.new_customers == 1


def test_cancelled_first_order_does_not_make_customer_returning():
    os_ = [order("1", D1, cust="c1", status="cancelled"), order("2", D2, cust="c1")]
    assert compute_profit(inp(os_), D1, D2).total.new_customers == 1


def test_same_day_two_orders_only_first_is_new():
    os_ = [order("1", D1, cust="c1", hour=9), order("2", D1, cust="c1", hour=15)]
    m = compute_profit(inp(os_), D1, D1).total
    assert (m.new_customers, m.returning_customers) == (1, 1)


def test_aggregate_equals_sum_of_days_and_ratios_recomputed():
    os_ = [order(str(i), D1 if i % 2 else D2, cust=f"c{i}") for i in range(6)]
    r = compute_profit(inp(os_, ads=[spend(D1, "7"), spend(D2, "9")]), D1, D2)
    assert r.total.ad_spend == 16 and r.total.orders == 6
    assert r.total.contribution_profit == sum((d.contribution_profit for d in r.days), Dd(0))
    sub = r.window(D2, D2)
    assert sub.orders == r.days[1].orders


def test_end_before_start_raises():
    with pytest.raises(ValueError):
        compute_profit(inp(), D2, D1)


def test_empty_inputs():
    r = compute_profit(inp([], costs=[]), D1, D2)
    assert r.total.net_revenue == 0 and r.total.cost_complete and len(r.days) == 2


def test_fixed_costs_reported_separately_not_in_contribution_profit():
    cs = full_costs() + [cost("fixed", "per_day", "50")]
    r = compute_profit(inp([order("1", D1)], costs=cs), D1, D1)
    assert r.total.fixed_costs == 50 and r.total.contribution_profit == r.total.net_revenue - r.total.cogs - r.total.shipping - r.total.fees


def test_shipping_charged_and_tax_excluded_from_revenue():
    o = order("1", D1); o.shipping_charged = Dd("5.99"); o.tax = Dd("0.80")
    assert compute_profit(inp([o]), D1, D1).total.net_revenue == Dd("10.00")


# ---------------- reconciliation
def totals(day, gross, disc, ref, n):
    return SourceTotal(**prov(str(day)), day=str(day), gross=Dd(gross), discounts=Dd(disc), refunds=Dd(ref), order_count=n)


def test_reconciled_ok():
    i = inp([order("1", D1, disc="1")], [refund("r", "1", D1, "2")], totals=[totals(D1, "10", "1", "2", 1)])
    assert reconcile(i, D1, D1).status == "reconciled"


def test_reconcile_detects_mismatch_each_field():
    i = inp([order("1", D1, disc="1")], [refund("r", "1", D1, "2")], totals=[totals(D1, "11", "1", "2.50", 2)])
    rc = reconcile(i, D1, D1)
    assert rc.status == "mismatch" and {d.field for d in rc.diffs} == {"gross", "refunds", "order_count"}


def test_reconcile_tolerance_and_penny():
    i = inp([order("1", D1)], totals=[totals(D1, "10.01", "0", "0", 1)])
    assert reconcile(i, D1, D1).status == "reconciled"
    assert reconcile(i, D1, D1, tolerance=Dd("0.001")).status == "mismatch"


def test_reconcile_no_totals_and_partial_and_extra_source_day():
    assert reconcile(inp([order("1", D1)]), D1, D1).status == "no_source_totals"
    i = inp([order("1", D1), order("2", D2, cust="c2")], totals=[totals(D1, "10", "0", "0", 1)])
    rc = reconcile(i, D1, D2)
    assert rc.status == "partial" and rc.coverage == Dd("0.5")
    i2 = inp([order("1", D1)], totals=[totals(D1, "10", "0", "0", 1), totals(D2, "50", "0", "0", 3)])
    assert reconcile(i2, D1, D2).status == "mismatch"    # platform says orders exist that we lack


def test_reconcile_ignores_cancelled():
    i = inp([order("1", D1), order("2", D1, status="cancelled", cust="c2")], totals=[totals(D1, "10", "0", "0", 1)])
    assert reconcile(i, D1, D1).reconciled


def test_synthetic_reconciles(synth):
    for run in synth["runs"].values():
        assert run.ctx.recon.status == "reconciled"


# ---------------- cohort LTV
def test_cohort_ltv_observed_only():
    os_ = [order("1", date(2026, 1, 1), cust="a"), order("2", date(2026, 1, 20), cust="a", lines=(("A", 1, "30.00"),)),
           order("3", date(2026, 3, 1), cust="a", lines=(("A", 1, "100.00"),)),
           order("4", date(2026, 1, 5), cust="b"),
           order("5", date(2026, 2, 20), cust="c")]
    rf = [refund("r", "2", date(2026, 1, 21), "10.00")]
    res = {c.cohort: c for c in cohort_ltv(inp(os_, rf), date(2026, 3, 10))}
    jan = res["2026-01"]
    assert jan.customers == 2
    assert jan.ltv[30] == (Dd("20.00"), 2)          # a: 10+30-10 = 30 ; b: 10 ; mean 20
    assert jan.ltv[90] == (None, 0) and jan.ltv[180] == (None, 0)   # not observed long enough -> None, never extrapolated
    assert res["2026-02"].ltv[30] == (None, 0)      # c observed only 18 days
    r60 = {c.cohort: c for c in cohort_ltv(inp(os_, rf), date(2026, 3, 10), horizons=(60,))}
    assert r60["2026-01"].ltv[60] == (Dd("70.00"), 2)   # a: 130 (incl. day-59 order), b: 10


def test_cohort_excludes_guests_and_cancelled():
    os_ = [order("1", date(2026, 1, 1), cust=None), order("2", date(2026, 1, 1), cust="a", status="cancelled")]
    assert cohort_ltv(inp(os_), date(2026, 6, 1)) == []


def test_cohort_synthetic_has_no_extrapolated_values(synth):
    from datetime import date as d
    for run in synth["runs"].values():
        cl = cohort_ltv(run.ctx.inputs, run.ctx.as_of)
        assert cl and all(v[0] is None or v[1] > 0 for c in cl for v in c.ltv.values())
        assert all(c.ltv[180] == (None, 0) for c in cl)   # only 90 days of synthetic history


# ---------------- sku economics
def test_sku_economics_discount_prorata_and_unpriced():
    o = order("1", D1, lines=(("A", 1, "10.00"), ("Z", 1, "30.00")), disc="4.00")
    e = sku_economics(inp([o]), D1, D1)
    assert e["A"].net_revenue == Dd("9.00") and e["Z"].net_revenue == Dd("27.00")
    assert e["A"].gross_margin == Dd("0.5556") and e["Z"].gross_margin is None and not e["Z"].priced
