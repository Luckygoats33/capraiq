import pytest

from capraiq import db
from capraiq.adapters import ingest, synthetic_adapters
from capraiq.ledger import Ledger
from capraiq.pipeline import run_tenant
from capraiq.policy import PolicyEngine
from capraiq.repo import Repo


@pytest.fixture
def repo():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    return Repo(conn)


@pytest.fixture(scope="module")
def synth():
    """Two SYNTHETIC tenants fully ingested and run through the pipeline."""
    conn = db.connect(":memory:")
    db.init_schema(conn)
    repo, ledger, policy = Repo(conn), Ledger(conn), PolicyEngine()
    adapters = synthetic_adapters()
    for a in adapters:
        repo.upsert_tenant(a.tenant()); repo.upsert_store(a.store()); ingest(a, repo)
    runs = {a.tenant_id: run_tenant(repo, a.tenant_id, a.store_id, policy, ledger) for a in adapters}
    return dict(conn=conn, repo=repo, ledger=ledger, policy=policy, adapters={a.tenant_id: a for a in adapters}, runs=runs)


def reg(repo, tenant="t1", store="s1"):
    from capraiq.models import Store, Tenant
    repo.upsert_tenant(Tenant(tenant_id=tenant, name=tenant)); repo.upsert_store(Store(tenant_id=tenant, store_id=store, platform="x", name=store))
