"""
tests/test_warehouse_aggregate_view.py
==========================================
يوثّق سلوكاً **مقصوداً**، لا خللاً (بعد إصلاح §46): استدعاء
`get_item_stock_summary(session, item_id)` **بلا** `warehouse_id` يبقى
يُرجع الإجمالي المُختلَط عبر كل المستودعات — عرض تجميعي شرعي لأغراض
تقرير ("إجمالي ما تملكه الشركة")، وليس للاستخدام بأي قرار تسعير أو
ترحيل فعلي (تلك تستخدم `_average_cost()` التي تفرض `warehouse_id`
إلزامياً الآن — راجع tests/test_warehouse_cost_isolation.py للإصلاح
الفعلي على مسار التكلفة).
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, CostMethod, Warehouse
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice
from app.services.item_queries import get_item_stock_summary

today = datetime.date.today()

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
s = sessionmaker(bind=engine)()
coa = create_default_chart_of_accounts(s)
item = create_item(s, sku="AGGVIEW-1", name_ar="مادة عرض تجميعي", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
wh_a = Warehouse(name_ar="مستودع A", is_active=True)
wh_b = Warehouse(name_ar="مستودع B", is_active=True)
s.add_all([wh_a, wh_b]); s.commit()

pa = Invoice(invoice_no="AV-PA", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
             currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT, warehouse_id=wh_a.id)
pa.lines = [InvoiceLine(item_id=item.id, quantity=D_("100"), unit_price=D_("1000"))]
s.add(pa); s.commit(); post_purchase_invoice(s, pa, is_cash=True); s.commit()

pb = Invoice(invoice_no="AV-PB", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
             currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT, warehouse_id=wh_b.id)
pb.lines = [InvoiceLine(item_id=item.id, quantity=D_("100"), unit_price=D_("9000"))]
s.add(pb); s.commit(); post_purchase_invoice(s, pb, is_cash=True); s.commit()

# بلا warehouse_id — إجمالي تجميعي، مقصود
aggregate = get_item_stock_summary(s, item.id)
expected_blended = (D_("1000") * D_("100") + D_("9000") * D_("100")) / D_("200")
assert aggregate.average_cost == expected_blended, \
    f"العرض التجميعي يجب أن يبقى مختلطاً عمداً: actual={aggregate.average_cost} expected={expected_blended}"
assert aggregate.quantity == D_("200")

# بتحديد المستودع — منفصل تماماً (الإصلاح الفعلي)
per_a = get_item_stock_summary(s, item.id, warehouse_id=wh_a.id)
per_b = get_item_stock_summary(s, item.id, warehouse_id=wh_b.id)
assert per_a.average_cost == D_("1000"), f"actual={per_a.average_cost}"
assert per_b.average_cost == D_("9000"), f"actual={per_b.average_cost}"

print("✅ العرض التجميعي (warehouse_id=None) يبقى مختلطاً عمداً — سليم للتقارير")
print("✅ التحديد الصريح لكل مستودع يُرجع تكلفته المنفصلة تماماً — الإصلاح فعّال")
