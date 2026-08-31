"""
tests/test_settlement_tamper_resistance.py
==============================================
يثبت مناعة Settlement (WORKFLOW.md §49) — 7 محاولات تلاعب صريحة قبل
اعتبار الطبقة مستقرة تمامًا.
"""
import os, sys, datetime, inspect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, CostMethod, Settlement
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_sales_invoice, post_purchase_invoice
import app.services.settlements as settlements_module
from app.services.settlements import post_receipt, post_payment, get_invoice_balance_due, SettlementError

today = datetime.date.today()
results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
s = sessionmaker(bind=engine)()
coa = create_default_chart_of_accounts(s)
item = create_item(s, sku="TAMPER-1", name_ar="مادة اختبار مناعة", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
s.commit()

# ======================================================================
# 1 و2) لا دالة تعديل ولا دالة حذف مُصدَّرة إطلاقاً من settlements.py
# ======================================================================
print("== 1-2) لا مسار خدمة رسمي لتعديل أو حذف Settlement ==")
exported_names = [name for name, obj in inspect.getmembers(settlements_module) if inspect.isfunction(obj)]
check("1) لا توجد دالة update_settlement أو ما يشبهها", not any("update" in n.lower() for n in exported_names),
      f"exported={exported_names}")
check("2) لا توجد دالة delete_settlement أو ما يشبهها", not any("delete" in n.lower() for n in exported_names),
      f"exported={exported_names}")

sale1 = Invoice(invoice_no="TMP-S1", kind=InvoiceKind.SALES, party_name="عميل 1", invoice_date=today,
                currency_code="USD", exchange_rate=D_("15000"), status=InvoiceStatus.DRAFT)
sale1.lines = [InvoiceLine(item_id=item.id, quantity=D_("10"), unit_price=D_("100"))]
s.add(sale1); s.commit(); post_sales_invoice(s, sale1, is_cash=False); s.commit()
post_receipt(s, sale1, D_("400"), today, D_("15000"), coa["cash"].id)
s.commit()
settlement_row = s.query(Settlement).filter_by(invoice_id=sale1.id).first()
original_amount = settlement_row.amount_foreign
original_fx = settlement_row.fx_amount
check("1-2) التسوية الموجودة لم تتغيّر بعد أي عملية أخرى بالنظام (قيمة مرجعية للمقارنة لاحقاً)",
      original_amount == D_("400"))

# ======================================================================
# 3) تجاوز الرصيد المستحق (تأكيد إضافي، مُختبَر سابقاً بـtest_settlement_fx.py)
# ======================================================================
print("== 3) تجاوز الرصيد المستحق (الرصيد المتبقي = 600 فقط) ==")
remaining = get_invoice_balance_due(s, sale1)
check("3) الرصيد المتبقي = 600 كما متوقَّع", remaining == D_("600"))
try:
    post_receipt(s, sale1, D_("700"), today, D_("15000"), coa["cash"].id)  # يتجاوز 600 المتبقية
    check("3) رفض تجاوز الرصيد المستحق", False, "لم يُرفع استثناء!")
except SettlementError:
    check("3) رفض تجاوز الرصيد المستحق", True)

# ======================================================================
# 4) تسوية فاتورة DRAFT (لم تُختبَر صراحة من قبل — كل الاختبارات رحّلت أولاً)
# ======================================================================
print("== 4) محاولة تسوية فاتورة لم تُرحَّل بعد (DRAFT) — لم تُختبَر من قبل ==")
draft_sale = Invoice(invoice_no="TMP-DRAFT", kind=InvoiceKind.SALES, party_name="عميل مسودة",
                      invoice_date=today, currency_code="USD", exchange_rate=D_("15000"),
                      status=InvoiceStatus.DRAFT)
draft_sale.lines = [InvoiceLine(item_id=item.id, quantity=D_("5"), unit_price=D_("100"))]
s.add(draft_sale); s.commit()
# عمداً: لا نستدعي post_sales_invoice — تبقى DRAFT فعلياً
try:
    post_receipt(s, draft_sale, D_("100"), today, D_("15000"), coa["cash"].id)
    check("4) رفض تسوية فاتورة DRAFT غير مرحّلة", False, "لم يُرفع استثناء! — هذا خلل حقيقي محتمل")
except SettlementError:
    check("4) رفض تسوية فاتورة DRAFT غير مرحّلة", True)

# ======================================================================
# 5) تسوية فاتورة CANCELLED (تأكيد إضافي)
# ======================================================================
print("== 5) محاولة تسوية فاتورة ملغاة (تأكيد إضافي) ==")
cancelled_sale = Invoice(invoice_no="TMP-CXL", kind=InvoiceKind.SALES, party_name="عميل ملغى",
                          invoice_date=today, currency_code="SYP", exchange_rate=D_("1"),
                          status=InvoiceStatus.DRAFT)
cancelled_sale.lines = [InvoiceLine(item_id=item.id, quantity=D_("5"), unit_price=D_("1000"))]
s.add(cancelled_sale); s.commit(); post_sales_invoice(s, cancelled_sale, is_cash=False); s.commit()
cancelled_sale.status = InvoiceStatus.CANCELLED
s.commit()
try:
    post_receipt(s, cancelled_sale, D_("100"), today, D_("1"), coa["cash"].id)
    check("5) رفض تسوية فاتورة ملغاة", False, "لم يُرفع استثناء!")
except SettlementError:
    check("5) رفض تسوية فاتورة ملغاة", True)

# ======================================================================
# 6) إنشاء Settlement بربط غير صحيح (نوع الفاتورة الخطأ للدالة المُستخدَمة)
# ======================================================================
print("== 6) استخدام post_payment على فاتورة بيع (ربط غير صحيح للنوع) ==")
try:
    post_payment(s, sale1, D_("50"), today, D_("15000"), coa["cash"].id)
    check("6) رفض post_payment على فاتورة SALES", False, "لم يُرفع استثناء!")
except SettlementError:
    check("6) رفض post_payment على فاتورة SALES", True)

# ======================================================================
# 7) لا تأثير على فاتورة/عميل آخر غير معني بالتسوية
# ======================================================================
print("== 7) التأكد أن تسوية فاتورة لا تؤثر على فاتورة أخرى لعميل مختلف ==")
sale2 = Invoice(invoice_no="TMP-S2", kind=InvoiceKind.SALES, party_name="عميل 2", invoice_date=today,
                 currency_code="USD", exchange_rate=D_("15000"), status=InvoiceStatus.DRAFT)
sale2.lines = [InvoiceLine(item_id=item.id, quantity=D_("20"), unit_price=D_("100"))]
s.add(sale2); s.commit(); post_sales_invoice(s, sale2, is_cash=False); s.commit()
balance_sale2_before = get_invoice_balance_due(s, sale2)

# تسوية إضافية على sale1 فقط
post_receipt(s, sale1, D_("200"), today, D_("15200"), coa["cash"].id)
s.commit()

balance_sale2_after = get_invoice_balance_due(s, sale2)
check("7) رصيد فاتورة عميل آخر (sale2) لم يتأثر إطلاقاً بتسوية sale1",
      balance_sale2_after == balance_sale2_before == D_("2000"),
      f"before={balance_sale2_before} after={balance_sale2_after}")

settlement_row_recheck = s.query(Settlement).filter_by(id=settlement_row.id).first()
check("7) التسوية الأصلية (من الخطوة 1-2) لم تتغيّر عبر كل ما جرى لاحقاً",
      settlement_row_recheck.amount_foreign == original_amount and
      settlement_row_recheck.fx_amount == original_fx)

print()
print("=" * 70)
print(f"✅ اختبار مناعة Settlement نجح بالكامل ({len(results)} تحقّقاً)")
print("=" * 70)
