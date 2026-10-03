"""
tests/test_real_db_path_no_lock_on_first_post.py
=====================================================
اختبار دائم لخلل حقيقي اكتُشف أثناء Group 3-B: أول ترحيل فاتورة (بيع أو
شراء) لشركة جديدة تماماً — حيث get_default_warehouse()/get_or_create_
party_account() تُنشئان صفاً جديداً عبر session.flush() بلا commit —
كان يُصادِم مع الاتصال المستقل لـ_reserve_ref_no (BEGIN IMMEDIATE يفشل
بـ"database is locked"). لا يظهر هذا الخلل مع sqlite:///:memory: أو مع
بيئة اختبار تُنشئ المستودع/الحساب مسبقاً — فقط عبر المسار الحقيقي
open_company_db() مع بيانات جديدة كلياً. مُصلَح بـcommit صريح بعد إعداد
الحسابات/المستودع وقبل بناء AccountingIntent (posting.py).
"""
import os, sys, shutil, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_DATA_DIR = "/tmp/test_real_db_path_no_lock"
if os.path.exists(TEST_DATA_DIR):
    shutil.rmtree(TEST_DATA_DIR)

import app.db as db_module
db_module.DATA_DIR = TEST_DATA_DIR
db_module.REGISTRY_PATH = os.path.join(TEST_DATA_DIR, "registry.db")

from decimal import Decimal as D_
from app.db import get_registry_session, open_company_db, create_company
from app.models import CostMethod, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice, post_sales_invoice

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


registry = get_registry_session()
company = create_company(registry, name="شركة اختبار جديدة كلياً", db_filename="brand_new.db", base_currency="SYP")
session = open_company_db(company.db_filename)
coa = create_default_chart_of_accounts(session)
item = create_item(session, sku="FIRST-1", name_ar="أول مادة", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
session.commit()
today = datetime.date.today()

print("== أول فاتورة شراء نقدية لشركة جديدة كلياً — لا مستودع موجود مسبقاً ==")
p_inv = Invoice(invoice_no="FIRST-P", kind=InvoiceKind.PURCHASE, party_name="مورد أول",
                 invoice_date=today, currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
p_inv.lines = [InvoiceLine(item_id=item.id, quantity=D_("10"), unit_price=D_("100"))]
session.add(p_inv); session.commit()
p_entry = post_purchase_invoice(session, p_inv, is_cash=True)
check("لا 'database is locked' — أول ترحيل شراء عبر المسار الحقيقي نجح", p_entry.is_balanced())

print("\n== أول فاتورة بيع نقدية — نفس المستودع الآن موجود ==")
s_inv = Invoice(invoice_no="FIRST-S", kind=InvoiceKind.SALES, party_name="عميل أول",
                 invoice_date=today, currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
s_inv.lines = [InvoiceLine(item_id=item.id, quantity=D_("1"), unit_price=D_("50"))]
session.add(s_inv); session.commit()
s_entry = post_sales_invoice(session, s_inv, is_cash=True)
check("لا 'database is locked' — أول ترحيل بيع عبر المسار الحقيقي نجح", s_entry.is_balanced())

print("\n== فاتورة شراء آجل لشركة أخرى جديدة — أول عميل/مورد يُنشأ تلقائياً أيضاً ==")
company2 = create_company(registry, name="شركة ثانية", db_filename="brand_new2.db", base_currency="SYP")
session2 = open_company_db(company2.db_filename)
coa2 = create_default_chart_of_accounts(session2)
item2 = create_item(session2, sku="X", name_ar="مادة", unit="قطعة",
                     inventory_account_id=coa2["inventory"].id, cogs_account_id=coa2["cogs"].id,
                     cost_method=CostMethod.AVERAGE)
session2.commit()
p_inv2 = Invoice(invoice_no="P2", kind=InvoiceKind.PURCHASE, party_name="مورد جديد كلياً",
                  invoice_date=today, currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
p_inv2.lines = [InvoiceLine(item_id=item2.id, quantity=D_("5"), unit_price=D_("40"))]
session2.add(p_inv2); session2.commit()
p_entry2 = post_purchase_invoice(session2, p_inv2, is_cash=False)  # is_cash=False → get_or_create_party_account أيضاً
check("لا 'database is locked' — إنشاء حساب طرف جديد + ترحيل آجل عبر المسار الحقيقي", p_entry2.is_balanced())

print("\n" + "=" * 70)
print(f"النتيجة: {sum(1 for _, c in results if c)}/{len(results)} نجح")
print("=" * 70)
