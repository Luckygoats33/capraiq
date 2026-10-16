import re
import sqlite3
from pathlib import Path

import pytest

from capraiq import db
from capraiq.ledger import Ledger
from .conftest import reg

PKG = Path(db.__file__).parent


def tables(sql):
    return set(re.findall(r"CREATE TABLE (\w+)", sql))


def test_postgres_twin_has_same_tables_and_columns():
    s, p = (PKG / "schema.sql").read_text(), (PKG / "schema_postgres.sql").read_text()
    assert tables(s) == tables(p)
    for t in tables(s):
        cols = lambda sql: set(re.findall(r"^\s*(?:CREATE TABLE \w+ \()?\s*(\w+) [A-Z]", re.search(rf"CREATE TABLE {t} \((.*?)\n\);", sql, re.S).group(0), re.M)) 
        assert cols(s) == cols(p), t


def test_required_entities_exist():
    need = {"tenant", "store", "orders", "order_line", "refund", "customer", "product", "cost", "ad_spend",
            "attribution_evidence", "decision", "approval", "metric_definition_version", "ledger"}
    assert need <= tables((PKG / "schema.sql").read_text())


def test_provenance_columns_on_data_tables(repo):
    for t in ("product", "customer", "orders", "order_line", "refund", "cost", "ad_spend", "attribution_evidence",
              "source_total", "decision", "approval"):
        cols = {r["name"] for r in repo.conn.execute(f"PRAGMA table_info({t})")}
        assert {"tenant_id", "store_id", "source_platform", "source_id", "ingested_at", "raw_json"} <= cols, t


def test_cost_check_constraints(repo):
    reg(repo)
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute("INSERT INTO cost VALUES ('t1','s1','x','1','t','{}','bogus','per_unit','','1','2026-01-01',1,'')")


def test_decision_table_append_only(synth):
    c = synth["conn"]
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE decision SET grade='A'")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM decision")
