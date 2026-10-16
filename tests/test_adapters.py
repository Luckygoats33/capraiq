from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from capraiq.adapters import CsvAdapter, DriveFolderAdapter, SourceAdapter, SyntheticAdapter, ingest
from capraiq.adapters.csv_adapter import classify
from capraiq.repo import IsolationError
from .conftest import reg


def test_abstract_contract_cannot_instantiate():
    with pytest.raises(TypeError):
        SourceAdapter("t", "s")


def test_adapter_requires_scope():
    with pytest.raises(IsolationError):
        CsvAdapter("", "s", {})


def w(p: Path, text):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text.strip() + "\n")


def make_folder(root: Path):
    w(root / "Orders - Oct 2026.csv", "order_id,customer_id,ordered_at,status,gross,discounts\n1,c1,2026-10-01T10:00:00,paid,30.00,3.00\n2,c2,2026-10-02T11:00:00,paid,notanumber,0\n3,,2026-10-02T11:00:00,paid,20,0")
    w(root / "sub" / "order_lines_oct.csv", "order_id,sku,quantity,unit_price\n1,A,2,15.00\n3,B,1,20.00\n3,B,0,20.00")
    w(root / "Refunds.csv", "refund_id,order_id,refunded_at,amount,reason\nr1,1,2026-10-03T00:00:00,5.00,damaged")
    w(root / "costs (COGS).csv", "cost_type,basis,sku,amount,effective_from,version\ncogs,per_unit,A,4.00,2026-01-01,1\ncogs,per_unit,A,5.00,2026-10-01,2\nweird,x,,1,2026-01-01,1")
    w(root / "ad_spend_meta.csv", "platform,campaign_id,campaign_name,day,spend\nmeta,m1,Prospecting,2026-10-01,12.50\nmeta,m1,Prospecting,2026-10-02,-3")
    w(root / "Source Totals.csv", "day,gross,discounts,refunds,order_count\n2026-10-01,30.00,3.00,0,1")
    w(root / "notes.txt", "ignore me")
    w(root / ".hidden.csv", "a\n1")
    w(root / "mystery.csv", "a\n1")


def test_classify():
    assert classify("Order Lines Sep.csv") == "order_lines" and classify("Orders - Oct.csv") == "orders"
    assert classify("Source Totals.csv") == "source_totals" and classify("Ad Spend.csv") == "ad_spend"
    assert classify("random.csv") is None


def test_drive_folder_ingest(tmp_path, repo):
    make_folder(tmp_path)
    reg(repo)
    ad = DriveFolderAdapter("t1", "s1", tmp_path)
    assert set(ad.skipped_files) == {"notes.txt", ".hidden.csv", "mystery.csv"}
    s = ingest(ad, repo)
    assert s.counts["orders"] == 2          # bad-decimal row skipped
    assert s.counts["ad_spend"] == 1        # negative spend skipped
    assert s.counts["costs"] == 2           # unknown cost type skipped
    assert any("notanumber" in e or "InvalidOperation" in e for e in ad.errors)
    assert len(ad.errors) >= 4
    o = {x.source_id: x for x in repo.list_orders("t1")}
    assert o["1"].lines[0].quantity == 2 and o["1"].raw["_file"].startswith("Orders")
    assert [l.sku for l in o["3"].lines] == ["B"] and o["3"].customer_source_id is None   # zero-qty line skipped
    assert [c.version for c in repo.list_costs("t1")] == [1, 2]
    assert repo.list_ad_spend("t1")[0].spend == Decimal("12.50")


def test_drive_folder_missing_dir():
    with pytest.raises(FileNotFoundError):
        DriveFolderAdapter("t", "s", Path("/nonexistent/xyz"))


def test_ingest_rejects_unregistered_store(repo, tmp_path):
    reg(repo)
    ad = CsvAdapter("t1", "other-store", {})
    with pytest.raises(IsolationError):
        ingest(ad, repo)


def test_ingest_rejects_adapter_emitting_foreign_rows(repo):
    reg(repo)
    ad = CsvAdapter("t1", "s1", {})
    from .helpers import order
    ad.fetch_orders = lambda: [order("1", "2026-01-01", tenant="evil")]
    with pytest.raises(IsolationError):
        ingest(ad, repo)


def test_synthetic_shape_and_label():
    a = SyntheticAdapter("synth_billy")
    assert a.profile.name.startswith("SYNTHETIC")
    assert a.tenant().synthetic and a.store().synthetic
    days = {o.day for o in a.fetch_orders()}
    assert len(days) == 90
    assert all(o.raw.get("SYNTHETIC") for o in a.fetch_orders()[:50])
    assert all(c.email_hash and "@" not in c.email_hash for c in a.fetch_customers())


def test_synthetic_deterministic_and_seedable():
    a1, a2 = SyntheticAdapter("synth_billy"), SyntheticAdapter("synth_billy")
    assert [(o.source_id, o.gross) for o in a1.fetch_orders()] == [(o.source_id, o.gross) for o in a2.fetch_orders()]
    a3 = SyntheticAdapter("synth_billy", seed_offset=1)
    assert [o.gross for o in a3.fetch_orders()] != [o.gross for o in a1.fetch_orders()]


def test_synthetic_injected_anomalies_recorded():
    a, b = SyntheticAdapter("synth_billy"), SyntheticAdapter("synth_nanny")
    a.fetch_orders(); b.fetch_orders()
    ta, tb = {g["type"] for g in a.ground_truth}, {g["type"] for g in b.ground_truth}
    assert ta | tb == {"spend_spike", "margin_drop", "refund_spike", "missing_cogs"}
    # missing COGS truly absent from cost rows
    miss = [g["sku"] for g in a.ground_truth if g["type"] == "missing_cogs"][0]
    assert all(c.sku != miss for c in a.fetch_costs() if c.cost_type == "cogs")
    # margin drop is a real versioned cost row
    assert any(c.version == 2 for c in b.fetch_costs())


def test_synthetic_source_ids_overlap_across_tenants():
    a, b = SyntheticAdapter("synth_billy"), SyntheticAdapter("synth_nanny")
    assert {o.source_id for o in a.fetch_orders()} & {o.source_id for o in b.fetch_orders()}
    assert a.store_id == b.store_id
