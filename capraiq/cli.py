"""CapraIQ CLI. `python -m capraiq demo` runs end-to-end on SYNTHETIC data. No network."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import db
from .adapters import DriveFolderAdapter, ingest, synthetic_adapters
from .ledger import Ledger
from .models import Store, Tenant
from .pipeline import run_tenant, write_brief
from .policy import PolicyEngine
from .repo import Repo


def cmd_demo(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dbp = out / "capraiq_demo.db"
    if dbp.exists():
        dbp.unlink()
    conn = db.connect(str(dbp))
    db.init_schema(conn)
    repo, ledger, policy = Repo(conn), Ledger(conn), PolicyEngine()
    print("CapraIQ v1 demo - SYNTHETIC DATA ONLY - read-only, no network\n")
    end = date.fromisoformat(args.end_date)
    adapters = synthetic_adapters(days=args.days, end_date=end)
    for a in adapters:
        repo.upsert_tenant(a.tenant()); repo.upsert_store(a.store())
        s = ingest(a, repo)
        print(f"ingested {a.tenant_id}: {s.counts}")
    for a in adapters:
        run = run_tenant(repo, a.tenant_id, a.store_id, policy, ledger)
        path = write_brief(policy, out, a.tenant_id, run.brief_markdown)
        v = ledger.verify(a.tenant_id)
        print("\n" + "=" * 78 + f"\n{run.brief_markdown}")
        print(f"[brief written to {path}; ledger entries={v.entries} chain_ok={v.ok}]")
    from .policy import Action, PolicyViolation, Role
    for role, action in ((Role.ADS_AUDITOR, Action.CHANGE_CAMPAIGN_BUDGET), (Role.PROFIT_ANALYST, Action.CHANGE_PRICE),
                         (Role.CHIEF_OF_STAFF, Action.SPEND_MONEY)):
        try:
            policy.enforce(role, action)
        except PolicyViolation as e:
            print(f"write attempt BLOCKED by policy: {e}")
    print(f"\npolicy audit: {len(policy.audit)} checks, {policy.denied_count()} denied; db: {dbp}")
    return 0


def cmd_ingest_dir(args) -> int:
    conn = db.connect(args.db)
    db.init_schema(conn)
    repo = Repo(conn)
    repo.upsert_tenant(Tenant(tenant_id=args.tenant, name=args.name or args.tenant, synthetic=args.synthetic))
    repo.upsert_store(Store(tenant_id=args.tenant, store_id=args.store, platform="drive_folder",
                            name=args.store, synthetic=args.synthetic))
    ad = DriveFolderAdapter(args.tenant, args.store, Path(args.dir))
    s = ingest(ad, repo)
    print(f"ingested {s.counts}; skipped files: {ad.skipped_files}; row errors: {len(ad.errors)}")
    for e in ad.errors[:20]:
        print("  ", e)
    if args.brief:
        run = run_tenant(repo, args.tenant, args.store, PolicyEngine(), Ledger(conn))
        print(run.brief_markdown)
    return 0


def cmd_verify(args) -> int:
    conn = db.connect(args.db)
    led = Ledger(conn)
    ok = True
    for (tid,) in conn.execute("SELECT tenant_id FROM tenant ORDER BY tenant_id"):
        v = led.verify(tid)
        ok &= v.ok
        print(f"{tid}: entries={v.entries} ok={v.ok} {v.reason}")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="capraiq", description="CapraIQ v1 (read-only governed decision system)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="end-to-end on SYNTHETIC data")
    d.add_argument("--out", default="out"); d.add_argument("--days", type=int, default=90)
    d.add_argument("--end-date", default="2026-10-14")
    d.set_defaults(fn=cmd_demo)
    i = sub.add_parser("ingest-dir", help="ingest a local Drive-style folder of CSVs")
    i.add_argument("dir"); i.add_argument("--tenant", required=True); i.add_argument("--store", required=True)
    i.add_argument("--name"); i.add_argument("--db", default="capraiq.db"); i.add_argument("--synthetic", action="store_true")
    i.add_argument("--brief", action="store_true"); i.set_defaults(fn=cmd_ingest_dir)
    v = sub.add_parser("verify-ledger", help="verify ledger hash chains in a DB")
    v.add_argument("--db", default="out/capraiq_demo.db"); v.set_defaults(fn=cmd_verify)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
