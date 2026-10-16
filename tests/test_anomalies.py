from datetime import date, timedelta
from decimal import Decimal as Dd

from capraiq.anomalies import data_quality, detect, episodes, robust_z
from capraiq.engine import ProfitInputs, compute_profit, reconcile
from .helpers import cost, full_costs, order, refund, spend

START = date(2026, 1, 1)


def build(days=60, spike=None, orders_per_day=10, drop_days=(), no_spend_days=()):
    os_, ads, n = [], [], 0
    for i in range(days):
        d = START + timedelta(i)
        if d in drop_days:
            continue
        for k in range(orders_per_day + (i % 3)):
            n += 1
            os_.append(order(str(n), d, cust=f"c{n}", lines=(("A", 1, "30.00"),)))
        if d not in no_spend_days:
            amt = Dd("100") + (i % 5)
            if spike and d >= START + timedelta(spike):
                amt *= 4
            ads.append(spend(d, amt))
    return ProfitInputs(orders=os_, ad_spend=ads, costs=full_costs())


def test_robust_z_basics():
    assert robust_z([1.0] * 5, 1.0) is None            # not enough history
    h = [10.0, 11.0, 9.0, 10.0, 10.5, 9.5, 10.0, 10.2, 9.8, 10.1, 9.9, 10.0, 10.3, 9.7]
    z, med = robust_z(h, 20.0)
    assert z > 10 and med == 10.0
    assert abs(robust_z(h, 10.0)[0]) < 0.5


def test_robust_z_constant_history_flat_baseline():
    z, med = robust_z([5.0] * 20, 50.0)
    assert z > 100 and med == 5.0
    assert robust_z([5.0] * 20, 5.0)[0] == 0
    assert robust_z([0.0] * 20, 0.2)[0] > 3.5       # spike from an all-zero baseline (e.g. refunds)


def test_spend_spike_detected_with_mer_and_cp():
    inp = build(spike=50)
    rep = compute_profit(inp, START, START + timedelta(59))
    eps = episodes(detect(rep))
    metrics = {e.metric for e in eps}
    assert {"ad_spend", "mer", "contribution_profit"} <= metrics
    sp = [e for e in eps if e.metric == "ad_spend"][0]
    assert sp.start == START + timedelta(50) and sp.direction == "up"


def test_no_anomaly_on_stable_series():
    rep = compute_profit(build(), START, START + timedelta(59))
    assert detect(rep) == []


def test_direction_matters_spend_drop_not_flagged():
    inp = build()
    for a in inp.ad_spend[-3:]:
        a.spend = Dd("1")
    rep = compute_profit(inp, START, START + timedelta(59))
    assert "ad_spend" not in {a.metric for a in detect(rep)}


def test_min_relative_change_guard():
    inp = build()
    inp.ad_spend[-1].spend += Dd("8")      # tiny absolute but maybe big z on tight series
    rep = compute_profit(inp, START, START + timedelta(59))
    assert all(a.metric != "ad_spend" for a in detect(rep, min_rel_change=0.5))


def test_lookback_limits_flags():
    rep = compute_profit(build(spike=20), START, START + timedelta(59))
    assert all(a.day >= START + timedelta(55) for a in detect(rep, lookback_days=5))


def test_refund_rate_anomaly():
    inp = build()
    inp.refunds = [refund(f"r{i}", str(i + 1), START + timedelta(59), "30.00") for i in range(8)]
    rep = compute_profit(inp, START, START + timedelta(59))
    assert "refund_rate" in {a.metric for a in detect(rep)}


def test_cac_anomaly_when_new_customers_collapse():
    inp = build()
    for o in inp.orders:
        if o.day >= START + timedelta(55):
            o.customer_source_id = "repeat-customer"
    rep = compute_profit(inp, START, START + timedelta(59))
    assert "cac" in {a.metric for a in detect(rep)}


def test_episodes_merge_consecutive_days():
    rep = compute_profit(build(spike=50), START, START + timedelta(59))
    sp = [e for e in episodes(detect(rep)) if e.metric == "ad_spend"]
    assert len(sp) == 1 and sp[0].days >= 2


# ---------------- data-quality anomalies
def dq(inp, end=59):
    rep = compute_profit(inp, START, START + timedelta(end))
    return {i.kind: i for i in data_quality(inp, rep, reconcile(inp, START, START + timedelta(end)))}


def test_dq_missing_cogs_and_recon_unavailable():
    inp = build(); inp.orders.append(order("999", START + timedelta(3), cust="zz", lines=(("MISSING", 2, "10.00"),)))
    k = dq(inp)
    assert k["missing_cogs"].keys == ["MISSING"] and k["missing_cogs"].count == 2 and k["missing_cogs"].severity == "high"
    assert "reconciliation_unavailable" in k


def test_dq_order_gap_and_spend_gap():
    k = dq(build(drop_days={START + timedelta(10)}, no_spend_days={START + timedelta(20), START + timedelta(21)}))
    assert k["order_gap_days"].count == 1 and k["ad_spend_gap_days"].count == 3  # 2 explicit + the zero-order day (build() skips both)


def test_dq_orphan_refund_line_mismatch_unknown_sku_guest_negative():
    inp = build()
    inp.refunds = [refund("r1", "no-such-order", START, "5")]
    bad = order("b1", START, cust=None, gross="99.00"); inp.orders.append(bad)
    neg = order("n1", START, cust="x", disc="-1"); inp.orders.append(neg)
    from capraiq.models import Product
    from .helpers import prov
    inp.products = [Product(**prov("A"), sku="A")]
    inp.orders.append(order("u1", START, cust="y", lines=(("UNK", 1, "5"),)))
    k = dq(inp)
    for key in ("orphan_refunds", "order_line_total_mismatch", "unknown_sku", "guest_orders", "negative_values"):
        assert key in k, key


def test_dq_clean_data_has_only_recon_notice():
    assert set(dq(build())) == {"reconciliation_unavailable"}


def test_synthetic_injected_anomalies_are_found(synth):
    a = synth["runs"]["synth_billy"].ctx
    assert any(e.metric == "ad_spend" and e.end >= a.recent.start for e in a.episodes)
    assert any(i.kind == "missing_cogs" and i.keys == ["BB-07"] for i in a.dq)
    b = synth["runs"]["synth_nanny"].ctx
    assert any(e.metric == "refund_rate" and e.end >= b.recent.start for e in b.episodes)
    assert {i.kind for i in b.dq} >= {"missing_cogs"}
    assert sorted(k for i in b.dq if i.kind == "missing_cogs" for k in i.keys) == ["NH-07", "NH-08"]
