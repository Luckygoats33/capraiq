"""Append-only decision/approval ledger with a per-tenant SHA-256 hash chain, stored in the DB."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Optional

from .repo import IsolationError, _tid, now_iso

GENESIS = "0" * 64


def _hash(prev: str, tenant_id: str, seq: int, ts: str, entry_type: str, payload_json: str) -> str:
    blob = json.dumps([prev, tenant_id, seq, ts, entry_type, payload_json], separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class VerifyResult:
    ok: bool
    entries: int
    broken_at: Optional[int] = None
    reason: str = ""


class Ledger:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def append(self, tenant_id: str, entry_type: str, payload: dict, ts: Optional[str] = None) -> dict:
        _tid(tenant_id)
        ts = ts or now_iso()
        payload_json = json.dumps(payload, sort_keys=True, default=str)
        cur = self.conn.cursor()
        row = cur.execute("SELECT seq, hash FROM ledger WHERE tenant_id=? ORDER BY seq DESC LIMIT 1", (tenant_id,)).fetchone()
        seq, prev = (row["seq"] + 1, row["hash"]) if row else (1, GENESIS)
        h = _hash(prev, tenant_id, seq, ts, entry_type, payload_json)
        cur.execute("INSERT INTO ledger (tenant_id,seq,ts,entry_type,payload_json,prev_hash,hash) VALUES (?,?,?,?,?,?,?)",
                    (tenant_id, seq, ts, entry_type, payload_json, prev, h))
        self.conn.commit()
        return {"seq": seq, "hash": h, "prev_hash": prev}

    def entries(self, tenant_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM ledger WHERE tenant_id=? ORDER BY seq", (_tid(tenant_id),))
        return [dict(r) for r in rows]

    def verify(self, tenant_id: str) -> VerifyResult:
        prev, n = GENESIS, 0
        for e in self.entries(tenant_id):
            n += 1
            if e["seq"] != n:
                return VerifyResult(False, n, e["seq"], "sequence gap")
            if e["prev_hash"] != prev:
                return VerifyResult(False, n, e["seq"], "prev_hash mismatch")
            if _hash(prev, tenant_id, e["seq"], e["ts"], e["entry_type"], e["payload_json"]) != e["hash"]:
                return VerifyResult(False, n, e["seq"], "hash mismatch")
            prev = e["hash"]
        return VerifyResult(True, n)

    # ---- typed helpers: write the row AND the chained ledger entry
    def record_decision(self, tenant_id: str, store_id: str, d: dict) -> dict:
        _tid(tenant_id)
        if not self.conn.execute("SELECT 1 FROM store WHERE tenant_id=? AND store_id=?", (tenant_id, store_id)).fetchone():
            raise IsolationError("store not registered for tenant")
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO decision (tenant_id,store_id,source_platform,source_id,ingested_at,raw_json,decision_id,brief_date,"
            "rank,kind,title,grade,impact_amount,impact_kind,falsifier,authority_level,status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tenant_id, store_id, "capraiq", d["decision_id"], ts, json.dumps(d, sort_keys=True, default=str),
             d["decision_id"], d["brief_date"], d["rank"], d["kind"], d["title"], d["grade"],
             None if d.get("impact_amount") is None else str(d["impact_amount"]), d["impact_kind"], d["falsifier"],
             d["authority_level"], "proposed"))
        self.conn.commit()
        return self.append(tenant_id, "decision_proposed", d, ts)

    def record_approval(self, tenant_id: str, store_id: str, decision_id: str, approval_id: str, status: str,
                        approver: str, note: str = "") -> dict:
        """Records a HUMAN approval decision. In v1 it never triggers any execution."""
        _tid(tenant_id)
        if not approver or approver.strip().lower() in {"agent", "system", "bot"}:
            raise ValueError("approver must be a named human")
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO approval (tenant_id,store_id,source_platform,source_id,ingested_at,raw_json,approval_id,decision_id,"
            "status,approver,decided_at,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tenant_id, store_id, "capraiq", approval_id, ts, "{}", approval_id, decision_id, status, approver, ts, note))
        self.conn.commit()
        return self.append(tenant_id, "approval_recorded",
                           {"approval_id": approval_id, "decision_id": decision_id, "status": status,
                            "approver": approver, "note": note, "executed": False}, ts)
