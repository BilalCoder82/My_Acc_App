"""
tests/test_cancel_invoice.py
===============================
يثبت قاعدة Cancel/Reverse الموثَّقة بـWORKFLOW.md §44: عكس حرفي بالقيم
التاريخية نفسها، لا إعادة حساب، رفض الإلغاء مع وجود تسويات، ورفض
الإلغاء المزدوج.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, InventoryMovement,
    JournalLine, CostMethod,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice, post_sales_invoice
from app.services.settlements import post_receipt
from app.services.invoice_cancel import cancel_invoice, CancelNotAllowedError
from app.services.item_queries import get_item_stock_summary

today = datetime.date.today()
results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def fresh_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def account_balance(session, account_id):
    lines = session.query(JournalLine).filter_by(account_id=account_id).all()
    return sum(D_(str(l.debit_base)) - D_(str(l.credit_base)) for l in lines)


# ======================================================================
# 1) إلغاء فاتورة شراء — عكس حرفي للمخزون والقيد
# ======================================================================
print("== 1) إلغاء فاتورة شراء — يجب أن يعيد الكمية والقيمة لصفر تماماً ==")
s = fresh_session()
coa = create_default_chart_of_accounts(s)
item = create_item(s, sku="CXL-1", name_ar="مادة إلغاء", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
s.commit()

pinv = Invoice(invoice_no="CXL-P1", kind=InvoiceKind.PURCHASE, party_name="مورد",
               invoice_date=today, currency_code="USD", exchange_rate=D_("15000"), status=InvoiceStatus.DRAFT)
pinv.lines = [InvoiceLine(item_id=item.id, quantity=D_("50"), unit_price=D_("10"))]
s.add(pinv); s.commit()
purchase_entry = post_purchase_invoice(s, pinv, is_cash=True)
s.commit()
inventory_balance_before_cancel = account_balance(s, coa["inventory"].id)
check("1) قبل الإلغاء: قيمة المخزون = 50×10×15,000", inventory_balance_before_cancel == D_("7500000"))

reversal = cancel_invoice(s, pinv, today)
s.commit()
check("1) الفاتورة أصبحت CANCELLED", pinv.status == InvoiceStatus.CANCELLED)
check("1) القيد العكسي متوازن", reversal.is_balanced())
check("1) قيمة حساب المخزون بدفتر الأستاذ = صفر بعد الإلغاء (عكس حرفي)",
      account_balance(s, coa["inventory"].id) == D_("0"))
summary = get_item_stock_summary(s, item.id)
check("1) get_item_stock_summary: الكمية = صفر بعد الإلغاء", summary.quantity == D_("0"))
check("1) get_item_stock_summary: القيمة = صفر بعد الإلغاء", summary.inventory_value == D_("0"))

# التحقق أن القيد الأصلي وحركاته لم تُحذف (سجل تاريخي محفوظ)
check("1) القيد الأصلي لا يزال موجوداً (لم يُحذف)", purchase_entry.id is not None)
original_movements_still_exist = s.query(InventoryMovement).filter_by(
    source_type="purchase_invoice", source_id=pinv.id
).count()
check("1) حركات المخزون الأصلية لا تزال موجودة (لم تُحذف، فقط عُكست)",
      original_movements_still_exist == 1, f"count={original_movements_still_exist}")

# ======================================================================
# 2) إلغاء فاتورة بيع — نفس التحقق
# ======================================================================
print("== 2) إلغاء فاتورة بيع ==")
s2 = fresh_session()
coa2 = create_default_chart_of_accounts(s2)
item2 = create_item(s2, sku="CXL-2", name_ar="مادة إلغاء بيع", unit="قطعة",
                     inventory_account_id=coa2["inventory"].id, cogs_account_id=coa2["cogs"].id,
                     cost_method=CostMethod.AVERAGE)
s2.commit()
p2 = Invoice(invoice_no="CXL-P2", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
             currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
p2.lines = [InvoiceLine(item_id=item2.id, quantity=D_("100"), unit_price=D_("5000"))]
s2.add(p2); s2.commit(); post_purchase_invoice(s2, p2, is_cash=True); s2.commit()

s2inv = Invoice(invoice_no="CXL-S2", kind=InvoiceKind.SALES, party_name="زبون", invoice_date=today,
                 currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
s2inv.lines = [InvoiceLine(item_id=item2.id, quantity=D_("30"), unit_price=D_("8000"))]
s2.add(s2inv); s2.commit(); post_sales_invoice(s2, s2inv, is_cash=True); s2.commit()

qty_before = get_item_stock_summary(s2, item2.id).quantity
check("2) الكمية قبل إلغاء البيع = 70", qty_before == D_("70"))
reversal2 = cancel_invoice(s2, s2inv, today)
s2.commit()
qty_after = get_item_stock_summary(s2, item2.id).quantity
check("2) الكمية عادت لـ100 بعد إلغاء البيع (استُرجعت الـ30 المباعة)", qty_after == D_("100"),
      f"actual={qty_after}")
check("2) القيد العكسي متوازن", reversal2.is_balanced())

# ======================================================================
# 3) رفض إلغاء فاتورة لها تسوية مرتبطة
# ======================================================================
print("== 3) رفض إلغاء فاتورة لها قبض مرتبط ==")
s3 = fresh_session()
coa3 = create_default_chart_of_accounts(s3)
item3 = create_item(s3, sku="CXL-3", name_ar="مادة إلغاء مع تسوية", unit="قطعة",
                     inventory_account_id=coa3["inventory"].id, cogs_account_id=coa3["cogs"].id,
                     cost_method=CostMethod.AVERAGE)
s3.commit()
s3inv = Invoice(invoice_no="CXL-S3", kind=InvoiceKind.SALES, party_name="زبون", invoice_date=today,
                 currency_code="USD", exchange_rate=D_("15000"), status=InvoiceStatus.DRAFT)
s3inv.lines = [InvoiceLine(item_id=item3.id, quantity=D_("10"), unit_price=D_("100"))]
s3.add(s3inv); s3.commit()
post_sales_invoice(s3, s3inv, is_cash=False)
s3.commit()
post_receipt(s3, s3inv, D_("500"), today, D_("15000"), coa3["cash"].id)
s3.commit()
try:
    cancel_invoice(s3, s3inv, today)
    check("3) رفض الإلغاء مع وجود تسوية", False, "لم يُرفع استثناء!")
except CancelNotAllowedError:
    check("3) رفض الإلغاء مع وجود تسوية", True)

# ======================================================================
# 4) رفض الإلغاء المزدوج
# ======================================================================
print("== 4) رفض إلغاء فاتورة ملغاة أصلاً ==")
try:
    cancel_invoice(s, pinv, today)  # pinv أصلاً أُلغيت بحالة (1)
    check("4) رفض الإلغاء المزدوج", False, "لم يُرفع استثناء!")
except CancelNotAllowedError:
    check("4) رفض الإلغاء المزدوج", True)

# ======================================================================
# 5) إلغاء فاتورة لا يؤثر على فاتورة أخرى لنفس المادة
# ======================================================================
print("== 5) التأكد أن إلغاء فاتورة واحدة لا يؤثر على فواتير أخرى لنفس المادة ==")
s5 = fresh_session()
coa5 = create_default_chart_of_accounts(s5)
item5 = create_item(s5, sku="CXL-5", name_ar="مادة إلغاء متعدد", unit="قطعة",
                     inventory_account_id=coa5["inventory"].id, cogs_account_id=coa5["cogs"].id,
                     cost_method=CostMethod.AVERAGE)
s5.commit()
p5a = Invoice(invoice_no="CXL-P5A", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
              currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
p5a.lines = [InvoiceLine(item_id=item5.id, quantity=D_("40"), unit_price=D_("1000"))]
s5.add(p5a); s5.commit(); post_purchase_invoice(s5, p5a, is_cash=True); s5.commit()

p5b = Invoice(invoice_no="CXL-P5B", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
              currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
p5b.lines = [InvoiceLine(item_id=item5.id, quantity=D_("60"), unit_price=D_("1200"))]
s5.add(p5b); s5.commit(); post_purchase_invoice(s5, p5b, is_cash=True); s5.commit()

cancel_invoice(s5, p5a, today)
s5.commit()
summary5 = get_item_stock_summary(s5, item5.id)
check("5) بعد إلغاء الفاتورة الأولى فقط: الكمية = 60 (فاتورة b سليمة)", summary5.quantity == D_("60"),
      f"actual={summary5.quantity}")
check("5) القيمة = 60×1,200 (لم تتأثر بإلغاء الأولى)", summary5.inventory_value == D_("72000"),
      f"actual={summary5.inventory_value}")

print()
print("=" * 70)
print(f"✅ كل اختبارات cancel_invoice نجحت ({len(results)} تحقّقاً)")
print("=" * 70)
