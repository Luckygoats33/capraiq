import sqlite3

import pytest

from capraiq.ledger import GENESIS, Ledger
from capraiq.repo import IsolationError
from .conftest import reg


def mk(repo):
    reg(repo, "tA", "s"); reg(repo, "tB", "s")
    return Ledger(repo.conn)


def dec(i="d1"):
    return {"decision_id": i, "brief_date": "2026-01-01", "rank": 1, "kind": "k", "title": "t", "grade": "A",
            "impact_amount": "10.00", "impact_kind": "profit_at_stake", "falsifier": "f", "authority_level": "draft"}


def test_chain_links_and_verifies(repo):
    L = mk(repo)
    a = L.append("tA", "x", {"n": 1}); b = L.append("tA", "x", {"n": 2})
    assert a["prev_hash"] == GENESIS and b["prev_hash"] == a["hash"] and b["seq"] == 2
    assert L.verify("tA").ok and L.verify("tA").entries == 2


def test_chains_are_per_tenant(repo):
    L = mk(repo)
    L.append("tA", "x", {"n": 1}); b = L.append("tB", "x", {"n": 1})
    assert b["seq"] == 1 and b["prev_hash"] == GENESIS
    assert len(L.entries("tA")) == 1 and L.verify("tB").ok


def test_update_and_delete_blocked_by_db(repo):
    L = mk(repo); L.append("tA", "x", {"n": 1})
    with pytest.raises(sqlite3.DatabaseError):
        repo.conn.execute("UPDATE ledger SET payload_json='{}'")
    with pytest.raises(sqlite3.DatabaseError):
        repo.conn.execute("DELETE FROM ledger")


def test_tampering_detected_even_if_triggers_bypassed(repo):
    L = mk(repo)
    for i in range(3):
        L.append("tA", "x", {"n": i})
    repo.conn.execute("DROP TRIGGER ledger_no_update")
    repo.conn.execute("UPDATE ledger SET payload_json='{\"n\": 99}' WHERE seq=2")
    v = L.verify("tA")
    assert not v.ok and v.broken_at == 2 and v.reason == "hash mismatch"


def test_deleted_middle_entry_detected(repo):
    L = mk(repo)
    for i in range(3):
        L.append("tA", "x", {"n": i})
    repo.conn.execute("DROP TRIGGER ledger_no_delete")
    repo.conn.execute("DELETE FROM ledger WHERE seq=2")
    assert not L.verify("tA").ok


def test_relinked_chain_detected(repo):
    L = mk(repo)
    for i in range(2):
        L.append("tA", "x", {"n": i})
    repo.conn.execute("DROP TRIGGER ledger_no_update")
    repo.conn.execute("UPDATE ledger SET prev_hash='abc' WHERE seq=2")
    assert L.verify("tA").reason == "prev_hash mismatch"


def test_record_decision_and_approval(repo):
    L = mk(repo)
    L.record_decision("tA", "s", dec())
    assert repo.count("tA", "decision") == 1 and repo.count("tB", "decision") == 0
    L.record_approval("tA", "s", "d1", "a1", "approved", "Will W.", "ok")
    es = L.entries("tA")
    assert [e["entry_type"] for e in es] == ["decision_proposed", "approval_recorded"] and '"executed": false' in es[1]["payload_json"]
    assert L.verify("tA").ok


def test_approval_requires_human_and_existing_decision(repo):
    L = mk(repo); L.record_decision("tA", "s", dec())
    with pytest.raises(ValueError):
        L.record_approval("tA", "s", "d1", "a1", "approved", "agent")
    with pytest.raises(sqlite3.IntegrityError):
        L.record_approval("tA", "s", "nope", "a2", "approved", "Will")
    with pytest.raises(sqlite3.IntegrityError):   # cross-tenant approval of tA's decision
        L.record_approval("tB", "s", "d1", "a3", "approved", "Will")


def test_decision_requires_registered_store(repo):
    L = mk(repo)
    with pytest.raises(IsolationError):
        L.record_decision("tA", "ghost-store", dec())
    with pytest.raises(IsolationError):
        L.append("", "x", {})


def test_pipeline_ledgers_verify(synth):
    for t in synth["runs"]:
        v = synth["ledger"].verify(t)
        assert v.ok and v.entries >= 2
