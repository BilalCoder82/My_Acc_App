"""
tests/test_app_path_after_alembic.py
=======================================
اختبار نقطة 6 من قائمة المراجعة حرفياً: بعد ربط Alembic بمسار التطبيق
الحقيقي (app/db.py::open_company_db)، هل الجلسة الناتجة تعمل بشكل سليم مع
كل العمليات؟ يستخدم open_company_db() و create_company() الحقيقيتين —
لا محاكاة، بل نفس المسار الذي main.py يستخدمه فعلياً — مع DATA_DIR مؤقت
معزول عن أي بيانات حقيقية.
"""
import os, sys, shutil, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_DATA_DIR = "/tmp/app_path_test_data"
if os.path.exists(TEST_DATA_DIR):
    shutil.rmtree(TEST_DATA_DIR)

import app.db as db_module
db_module.DATA_DIR = TEST_DATA_DIR
db_module.REGISTRY_PATH = os.path.join(TEST_DATA_DIR, "registry.db")

from decimal import Decimal as D_
from app.db import get_registry_session, open_company_db, create_company
from app.models import Account, JournalEntry, JournalEntryStatus, Item, CostMethod
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice, post_sales_invoice
from app.services.returns import post_sales_return
from app.services.journal_edit import add_manual_line, post_manual_entry
from app.models import Invoice, InvoiceLine, InvoiceKind, InvoiceStatus
from app.services.item_queries import get_item_stock_summary
from app.reports.trial_balance import get_trial_balance

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


# --- بالضبط مسار main.py الحقيقي ---
registry = get_registry_session()
company = create_company(registry, name="شركة اختبار ما بعد Alembic", db_filename="post_alembic_test.db", base_currency="SYP")
session = open_company_db(company.db_filename)
check("فتح قاعدة عميل جديد عبر المسار الحقيقي: نجح دون استثناء", True)

coa = create_default_chart_of_accounts(session)

item = create_item(session, sku="POST-ALEMBIC-1", name_ar="مادة اختبار", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
session.commit()
today = datetime.date.today()

# 1) فاتورة شراء
p_inv = Invoice(invoice_no="PA-P-1", kind=InvoiceKind.PURCHASE, party_name="مورد",
                 invoice_date=today, currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
p_inv.lines = [InvoiceLine(item_id=item.id, quantity=D_("100"), unit_price=D_("500"))]
session.add(p_inv); session.commit()
p_entry = post_purchase_invoice(session, p_inv, is_cash=True); session.commit()
check("فاتورة شراء: تُرحَّل وتتوازن", p_entry.is_balanced())

# 2) فاتورة بيع
s_inv = Invoice(invoice_no="PA-S-1", kind=InvoiceKind.SALES, party_name="زبون",
                 invoice_date=today, currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
s_inv.lines = [InvoiceLine(item_id=item.id, quantity=D_("30"), unit_price=D_("800"))]
session.add(s_inv); session.commit()
s_entry = post_sales_invoice(session, s_inv, is_cash=True); session.commit()
check("فاتورة بيع: تُرحَّل وتتوازن", s_entry.is_balanced())

# 3) مرتجع بيع
r_inv = Invoice(invoice_no="PA-SR-1", kind=InvoiceKind.SALES_RETURN, party_name="زبون",
                 invoice_date=today, currency_code="SYP", exchange_rate=D_("1"),
                 status=InvoiceStatus.DRAFT, original_invoice_id=s_inv.id)
r_inv.lines = [InvoiceLine(item_id=item.id, quantity=D_("5"), unit_price=D_("800"))]
session.add(r_inv); session.commit()
r_entry = post_sales_return(session, r_inv, is_cash=True); session.commit()
check("مرتجع بيع: يُرحَّل ويتوازن", r_entry.is_balanced())

# 4) سند قيد يدوي
manual_entry = JournalEntry(
    entry_date=today, ref_no="PA-MJ-1", description="سند اختبار يدوي",
    currency_code="SYP", exchange_rate=D_("1"), source_type="manual",
    status=JournalEntryStatus.DRAFT,
)
session.add(manual_entry); session.flush()
add_manual_line(session, manual_entry, coa["cash"].id, debit=D_("1000"))
add_manual_line(session, manual_entry, coa["sales"].id, credit=D_("1000"))
post_manual_entry(session, manual_entry)
session.commit()
check("سند قيد يدوي: يُرحَّل ويتوازن", manual_entry.is_balanced())

# 5) استعلام مخزون
summary = get_item_stock_summary(session, item.id)
check("استعلام المخزون: يعمل ويُرجع كمية منطقية", summary.quantity == D_("75"),
      f"الكمية={summary.quantity}")

# 6) تقرير (ميزان المراجعة)
tb = get_trial_balance(session, today)
check("تقرير ميزان المراجعة: يعمل ومتوازن فعلياً", tb.is_balanced,
      f"مدين={tb.total_debit} دائن={tb.total_credit}")

session.close()

print()
print("=" * 70)
print(f"✅ كل العمليات تعمل عبر المسار الحقيقي بعد ربط Alembic ({len(results)} تحقّقاً)")
print("=" * 70)
