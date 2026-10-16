import pytest

from capraiq.models import Store
from capraiq.repo import IsolationError
from .conftest import reg
from .helpers import cost, order, refund, spend


def two_tenants(repo):
    reg(repo, "tA", "main"); reg(repo, "tB", "main")  # SAME store_id on purpose
    # identical source ids in both tenants
    repo.save_orders("tA", [order("1001", "2026-01-01", tenant="tA", store="main", lines=(("A", 1, "10.00"),))])
    repo.save_orders("tB", [order("1001", "2026-01-01", tenant="tB", store="main", lines=(("A", 5, "99.00"),))])
    repo.save_refunds("tA", [refund("r1", "1001", "2026-01-02", "5", tenant="tA", store="main")])
    repo.save_costs("tB", [cost("cogs", "per_unit", "7", sku="A", tenant="tB", store="main")])
    repo.save_ad_spend("tB", [spend("2026-01-01", "50", tenant="tB", store="main")])


def test_no_cross_tenant_leakage(repo):
    two_tenants(repo)
    a, b = repo.list_orders("tA"), repo.list_orders("tB")
    assert len(a) == len(b) == 1
    assert a[0].gross == 10 and b[0].gross == 495
    assert [l.quantity for l in a[0].lines] == [1] and [l.quantity for l in b[0].lines] == [5]
    assert len(repo.list_refunds("tA")) == 1 and repo.list_refunds("tB") == []
    assert repo.list_costs("tA") == [] and len(repo.list_costs("tB")) == 1
    assert repo.list_ad_spend("tA") == [] and len(repo.list_ad_spend("tB")) == 1
    assert repo.count("tA", "orders") == 1 and repo.count("tB", "order_line") == 1
    assert repo.list_orders("nonexistent") == []


def test_every_list_method_scoped(repo):
    two_tenants(repo)
    for fn in ("list_orders", "list_refunds", "list_customers", "list_products", "list_costs", "list_ad_spend",
               "list_attribution", "list_source_totals", "list_stores", "get_tenant"):
        with pytest.raises((IsolationError, TypeError)):
            getattr(repo, fn)()          # no tenant -> TypeError
        with pytest.raises(IsolationError):
            getattr(repo, fn)(None)
        with pytest.raises(IsolationError):
            getattr(repo, fn)("  ")


def test_write_with_wrong_tenant_rejected(repo):
    two_tenants(repo)
    with pytest.raises(IsolationError):
        repo.save_orders("tA", [order("9", "2026-01-01", tenant="tB", store="main")])
    assert repo.count("tB", "orders") == 1


def test_write_to_other_tenants_store_rejected(repo):
    reg(repo, "tA", "only-a"); reg(repo, "tB", "only-b")
    with pytest.raises(IsolationError):
        repo.save_orders("tA", [order("9", "2026-01-01", tenant="tA", store="only-b")])


def test_db_foreign_key_blocks_mismatched_pair(repo):
    import sqlite3
    reg(repo, "tA", "only-a"); reg(repo, "tB", "only-b")
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute("INSERT INTO product (tenant_id,store_id,source_platform,source_id,ingested_at,raw_json,sku) "
                          "VALUES ('tA','only-b','x','1','t','{}','S')")


def test_upsert_is_idempotent_and_updates(repo):
    reg(repo)
    repo.save_orders("t1", [order("1", "2026-01-01", lines=(("A", 1, "10.00"),))])
    repo.save_orders("t1", [order("1", "2026-01-01", lines=(("A", 2, "10.00"),))])
    o = repo.list_orders("t1")
    assert len(o) == 1 and o[0].lines[0].quantity == 2 and repo.count("t1", "order_line") == 1


def test_provenance_and_raw_kept(repo):
    reg(repo)
    o = order("1", "2026-01-01"); o.raw = {"payload": {"x": 1}, "SYNTHETIC": True}
    repo.save_orders("t1", [o])
    got = repo.list_orders("t1")[0]
    assert got.raw["payload"] == {"x": 1} and got.ingested_at and got.source_platform == "unit" and got.store_id == "s1"


def test_synthetic_pipeline_tenants_isolated(synth):
    repo = synth["repo"]
    ids_a = {o.source_id for o in repo.list_orders("synth_billy")}
    ids_b = {o.source_id for o in repo.list_orders("synth_nanny")}
    assert ids_a & ids_b, "fixture should deliberately overlap source ids"
    assert all(o.tenant_id == "synth_billy" for o in repo.list_orders("synth_billy"))
    assert {p.sku for p in repo.list_products("synth_billy")}.isdisjoint({p.sku for p in repo.list_products("synth_nanny")})
    assert synth["runs"]["synth_billy"].brief_markdown.count("Nanny") == 0
    assert synth["runs"]["synth_nanny"].brief_markdown.count("Billy") == 0
