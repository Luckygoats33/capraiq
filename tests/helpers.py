from datetime import date
from decimal import Decimal as Dd

from capraiq.models import (AdSpend, CostEntry, Customer, Order, OrderLine, Refund, SourceTotal, Product)

T, S, P = "t1", "s1", "unit"


def prov(sid, tenant=T, store=S, plat=P):
    return dict(tenant_id=tenant, store_id=store, source_platform=plat, source_id=str(sid), raw={"SYNTHETIC": True})


def order(oid, day, cust="c1", lines=(("A", 1, "10.00"),), disc="0", status="paid", gross=None, tenant=T, store=S, hour=10):
    ls = [OrderLine(line_no=i + 1, sku=s, quantity=q, unit_price=Dd(p)) for i, (s, q, p) in enumerate(lines)]
    g = Dd(gross) if gross is not None else sum((l.unit_price * l.quantity for l in ls), Dd(0))
    return Order(**prov(oid, tenant, store), customer_source_id=cust, ordered_at=f"{day}T{hour:02d}:00:00",
                 status=status, gross=g, discounts=Dd(disc), lines=ls)


def cost(ctype, basis, amount, eff="2026-01-01", sku="", version=1, tenant=T, store=S):
    return CostEntry(**prov(f"{ctype}:{basis}:{sku}:{eff}:{version}", tenant, store), cost_type=ctype, basis=basis, sku=sku,
                     amount=Dd(amount), effective_from=eff, version=version)


def refund(rid, oid, day, amount, reason="", tenant=T, store=S):
    return Refund(**prov(rid, tenant, store), order_source_id=str(oid), refunded_at=f"{day}T12:00:00", amount=Dd(amount), reason=reason)


def spend(day, amount, camp="c1", plat="meta", tenant=T, store=S):
    return AdSpend(**prov(f"{plat}:{camp}:{day}", tenant, store), platform=plat, campaign_id=camp, campaign_name=camp,
                   day=str(day), spend=Dd(amount))


def full_costs():
    return [cost("cogs", "per_unit", "4.00", sku="A"), cost("shipping", "per_order", "3.00"),
            cost("fees", "pct_revenue", "0.03"), cost("fees", "per_order", "0.30")]
