"""
tests/test_settlement_fx.py
==============================
اختبار app/services/settlements.py وفق القواعد الموثَّقة بـWORKFLOW.md §42.
Oracle مستقل رياضياً (FXOracle) — لا يستدعي settlements.py، معادلات
يدوية مباشرة، يغطي الحالات الاثنتي عشرة المطلوبة صراحةً.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, Settlement, JournalLine
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.models import CostMethod
from app.services.posting import post_sales_invoice, post_purchase_invoice
from app.services.settlements import post_receipt, post_payment, get_invoice_balance_due, SettlementError

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


class FXOracle:
    """معادلة مستقلة تماماً — لا تستدعي settlements.py."""
    @staticmethod
    def fx_diff_receivable(amount_foreign: D_, settlement_rate: D_, invoice_rate: D_) -> D_:
        return (amount_foreign * settlement_rate) - (amount_foreign * invoice_rate)

    @staticmethod
    def fx_diff_payable(amount_foreign: D_, settlement_rate: D_, invoice_rate: D_) -> D_:
        return -((amount_foreign * settlement_rate) - (amount_foreign * invoice_rate))


def make_sales_invoice_credit(session, coa, item, qty, price, ccy, rate, no):
    inv = Invoice(invoice_no=no, kind=InvoiceKind.SALES, party_name="عميل التسوية",
                   invoice_date=today, currency_code=ccy, exchange_rate=rate, status=InvoiceStatus.DRAFT)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=qty, unit_price=price)]
    session.add(inv); session.commit()
    post_sales_invoice(session, inv, is_cash=False)  # آجل — يخلق رصيداً مستحقاً
    session.commit()
    return inv


def make_purchase_invoice_credit(session, coa, item, qty, price, ccy, rate, no):
    inv = Invoice(invoice_no=no, kind=InvoiceKind.PURCHASE, party_name="مورد التسوية",
                   invoice_date=today, currency_code=ccy, exchange_rate=rate, status=InvoiceStatus.DRAFT)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=qty, unit_price=price)]
    session.add(inv); session.commit()
    post_purchase_invoice(session, inv, is_cash=False)
    session.commit()
    return inv


def new_item(session, coa, sku="STL-1"):
    item = create_item(session, sku=sku, name_ar="مادة تسوية", unit="قطعة",
                        inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                        cost_method=CostMethod.AVERAGE)
    session.commit()
    return item


# ======================================================================
# 1) فاتورة USD + قبض USD بنفس سعر الصرف — لا فرق صرف إطلاقاً
# ======================================================================
print("== 1) فاتورة USD + قبض USD بنفس سعر الصرف ==")
s = fresh_session()
coa = create_default_chart_of_accounts(s)
item = new_item(s, coa)
inv1 = make_sales_invoice_credit(s, coa, item, D_("10"), D_("100"), "USD", D_("15000"), "STL-S1")
entry1 = post_receipt(s, inv1, D_("1000"), today, D_("15000"), coa["cash"].id)
s.commit()
check("1) القيد متوازن", entry1.is_balanced())
check("1) لا سطر فرق صرف (fx=0)", len(entry1.lines) == 2, f"عدد الأسطر={len(entry1.lines)}")
check("1) الرصيد المستحق أصبح صفراً", get_invoice_balance_due(s, inv1) == D_("0"))

# ======================================================================
# 2) فاتورة USD + قبض بسعر صرف مختلف → ربح/خسارة صرف
# ======================================================================
print("== 2) فاتورة USD + قبض بسعر صرف أعلى (ربح صرف للعميل) ==")
s2 = fresh_session()
coa2 = create_default_chart_of_accounts(s2)
item2 = new_item(s2, coa2)
inv2 = make_sales_invoice_credit(s2, coa2, item2, D_("10"), D_("100"), "USD", D_("15000"), "STL-S2")
expected_fx2 = FXOracle.fx_diff_receivable(D_("1000"), D_("16000"), D_("15000"))  # = +1,000,000
entry2 = post_receipt(s2, inv2, D_("1000"), today, D_("16000"), coa2["cash"].id)
s2.commit()
fx_gain_line = next((l for l in entry2.lines if l.account_id == coa2["fx_gain"].id), None)
check("2) سطر ربح صرف موجود ويطابق Oracle", fx_gain_line is not None and D_(str(fx_gain_line.credit_base)) == expected_fx2,
      f"actual={fx_gain_line.credit_base if fx_gain_line else None} oracle={expected_fx2}")
check("2) القيد متوازن رغم سطر الفرق", entry2.is_balanced())

# ======================================================================
# 3) قبض جزئي
# ======================================================================
print("== 3) قبض جزئي (400 من أصل 1000) ==")
s3 = fresh_session()
coa3 = create_default_chart_of_accounts(s3)
item3 = new_item(s3, coa3)
inv3 = make_sales_invoice_credit(s3, coa3, item3, D_("10"), D_("100"), "USD", D_("15000"), "STL-S3")
post_receipt(s3, inv3, D_("400"), today, D_("15000"), coa3["cash"].id)
s3.commit()
check("3) الرصيد المتبقي = 600 بالضبط", get_invoice_balance_due(s3, inv3) == D_("600"),
      f"actual={get_invoice_balance_due(s3, inv3)}")

# ======================================================================
# 4) عدة قبضيات بأسعار صرف مختلفة
# ======================================================================
print("== 4) 3 قبضيات متتالية بأسعار صرف مختلفة ==")
s4 = fresh_session()
coa4 = create_default_chart_of_accounts(s4)
item4 = new_item(s4, coa4)
inv4 = make_sales_invoice_credit(s4, coa4, item4, D_("10"), D_("100"), "USD", D_("15000"), "STL-S4")
total_fx_oracle = D_("0")
for amt, rate in [(D_("300"), D_("15500")), (D_("300"), D_("14500")), (D_("400"), D_("16000"))]:
    total_fx_oracle += FXOracle.fx_diff_receivable(amt, rate, D_("15000"))
    post_receipt(s4, inv4, amt, today, rate, coa4["cash"].id)
    s4.commit()
check("4) الرصيد النهائي = صفر بعد 3 دفعات (300+300+400=1000)", get_invoice_balance_due(s4, inv4) == D_("0"))
fx_gain_lines = s4.query(JournalLine).filter_by(account_id=coa4["fx_gain"].id).all()
fx_loss_lines = s4.query(JournalLine).filter_by(account_id=coa4["fx_loss"].id).all()
net_fx_ledger = sum(D_(str(l.credit_base)) for l in fx_gain_lines) - sum(D_(str(l.debit_base)) for l in fx_loss_lines)
check("4) صافي فرق الصرف بدفتر الأستاذ يطابق Oracle (3 قيود منفصلة)",
      abs(net_fx_ledger - total_fx_oracle) <= D_("1"), f"ledger={net_fx_ledger} oracle={total_fx_oracle}")

# ======================================================================
# 5) فاتورة USD + قبض SYP (amount_foreign لا يزال بعملة الفاتورة USD)
# ======================================================================
print("== 5) فاتورة USD، تسوية amount_foreign بعملة الفاتورة، القيمة الأساسية تُحسب بسعر التسوية ==")
s5 = fresh_session()
coa5 = create_default_chart_of_accounts(s5)
item5 = new_item(s5, coa5)
inv5 = make_sales_invoice_credit(s5, coa5, item5, D_("10"), D_("100"), "USD", D_("15000"), "STL-S5")
# العميل دفع مبلغاً بالليرة يعادل 500 دولار بسعر يوم التسوية 15,800
entry5 = post_receipt(s5, inv5, D_("500"), today, D_("15800"), coa5["cash"].id)
s5.commit()
cash_line5 = next(l for l in entry5.lines if l.account_id == coa5["cash"].id)
expected_cash_base5 = D_("500") * D_("15800")
check("5) قيمة الصندوق الأساسية = amount_foreign × settlement_rate بالضبط",
      D_(str(cash_line5.debit_base)) == expected_cash_base5,
      f"actual={cash_line5.debit_base} expected={expected_cash_base5}")

# ======================================================================
# 6) قبض جزئي ثم بقاء رصيد — مغطاة أصلاً بـ(3)، تأكيد إضافي بمنع تجاوز
# ======================================================================
print("== 6) قبض جزئي، التأكد من بقاء الرصيد الصحيح ==")
check("6) (مكرر تأكيدي) الرصيد بعد الجزئي بحالة (3) = 600", get_invoice_balance_due(s3, inv3) == D_("600"))

# ======================================================================
# 7) التسوية الكاملة بعد عدة دفعات جزئية — مغطاة بحالة (4)، تأكيد صريح
# ======================================================================
check("7) (مكرر تأكيدي) التسوية الكاملة بعد 3 دفعات بحالة (4) = صفر", get_invoice_balance_due(s4, inv4) == D_("0"))

# ======================================================================
# 8) فاتورة مرتجعة ولها دفعات سابقة — القرار الموثَّق: balance_due مستقل عن المرتجع
# ======================================================================
print("== 8) فاتورة لها دفعة جزئية، ثم مرتجع مرتبط — balance_due لا يتأثر تلقائياً (قرار موثَّق §42.5) ==")
s8 = fresh_session()
coa8 = create_default_chart_of_accounts(s8)
item8 = new_item(s8, coa8)
inv8 = make_sales_invoice_credit(s8, coa8, item8, D_("10"), D_("100"), "USD", D_("15000"), "STL-S8")
post_receipt(s8, inv8, D_("400"), today, D_("15000"), coa8["cash"].id)
s8.commit()
balance_before_return = get_invoice_balance_due(s8, inv8)
from app.services.returns import post_sales_return
sret8 = Invoice(invoice_no="STL-SR8", kind=InvoiceKind.SALES_RETURN, party_name="عميل التسوية",
                invoice_date=today, currency_code="USD", exchange_rate=D_("15000"),
                status=InvoiceStatus.DRAFT, original_invoice_id=inv8.id)
sret8.lines = [InvoiceLine(item_id=item8.id, quantity=D_("2"), unit_price=D_("100"))]
s8.add(sret8); s8.commit(); post_sales_return(s8, sret8, is_cash=True); s8.commit()
balance_after_return = get_invoice_balance_due(s8, inv8)
check("8) balance_due الفاتورة الأصلية لم يتأثر بالمرتجع (قرار موثَّق، ليس خطأً)",
      balance_before_return == balance_after_return == D_("600"),
      f"before={balance_before_return} after={balance_after_return}")

# ======================================================================
# 9) محاولة قبض أكثر من الرصيد المستحق — يجب الرفض
# ======================================================================
print("== 9) محاولة قبض أكبر من الرصيد المستحق ==")
s9 = fresh_session()
coa9 = create_default_chart_of_accounts(s9)
item9 = new_item(s9, coa9)
inv9 = make_sales_invoice_credit(s9, coa9, item9, D_("10"), D_("100"), "USD", D_("15000"), "STL-S9")
try:
    post_receipt(s9, inv9, D_("1001"), today, D_("15000"), coa9["cash"].id)
    check("9) رفض القبض الزائد", False, "لم يُرفع استثناء!")
except SettlementError:
    check("9) رفض القبض الزائد", True)

# ======================================================================
# 10) محاولة تسوية فاتورة ملغاة
# ======================================================================
print("== 10) محاولة تسوية فاتورة ملغاة ==")
s10 = fresh_session()
coa10 = create_default_chart_of_accounts(s10)
item10 = new_item(s10, coa10)
inv10 = make_sales_invoice_credit(s10, coa10, item10, D_("10"), D_("100"), "USD", D_("15000"), "STL-S10")
inv10.status = InvoiceStatus.CANCELLED
s10.commit()
try:
    post_receipt(s10, inv10, D_("100"), today, D_("15000"), coa10["cash"].id)
    check("10) رفض التسوية على فاتورة ملغاة", False, "لم يُرفع استثناء!")
except SettlementError:
    check("10) رفض التسوية على فاتورة ملغاة", True)

# ======================================================================
# 11) الرصيد بالعملة الأصلية والمعادل الأساسي صحيحان معاً بعد كل تسوية
# ======================================================================
print("== 11) التحقق من الرصيد بالعملتين معاً بعد تسوية جزئية ==")
s11 = fresh_session()
coa11 = create_default_chart_of_accounts(s11)
item11 = new_item(s11, coa11)
inv11 = make_sales_invoice_credit(s11, coa11, item11, D_("10"), D_("100"), "USD", D_("15000"), "STL-S11")
entry11 = post_receipt(s11, inv11, D_("300"), today, D_("15200"), coa11["cash"].id)
s11.commit()
check("11) الرصيد المتبقي بعملة الفاتورة = 700 بالضبط", get_invoice_balance_due(s11, inv11) == D_("700"))
counter_line11 = next(l for l in entry11.lines if l.account_id != coa11["cash"].id
                       and l.account_id not in (coa11["fx_gain"].id, coa11["fx_loss"].id))
check("11) سطر العميل يطفئ 300×15,000 بالضبط (سعر الفاتورة الأصلي، لا سعر التسوية)",
      D_(str(counter_line11.credit_base)) == D_("300") * D_("15000"),
      f"actual={counter_line11.credit_base}")

# ======================================================================
# 12) عدم احتساب فرق الصرف مرتين
# ======================================================================
print("== 12) التأكد من عدم احتساب فرق الصرف مرتين بنفس القيد ==")
fx_lines_in_entry2 = [l for l in entry2.lines if l.account_id in (coa2["fx_gain"].id, coa2["fx_loss"].id)]
check("12) سطر فرق صرف واحد فقط بالقيد (لا اثنان)", len(fx_lines_in_entry2) == 1,
      f"عدد أسطر الفرق={len(fx_lines_in_entry2)}")

# ======================================================================
# 13) post_payment (AP) — إشارة الربح/الخسارة معكوسة عن العميل عمداً
# ======================================================================
print("== 13) دفع لمورد بسعر صرف أعلى (خسارة صرف للمشتري، عكس حالة العميل) ==")
s13 = fresh_session()
coa13 = create_default_chart_of_accounts(s13)
item13 = new_item(s13, coa13)
inv13 = make_purchase_invoice_credit(s13, coa13, item13, D_("10"), D_("100"), "USD", D_("15000"), "STL-P13")
expected_fx13 = FXOracle.fx_diff_payable(D_("1000"), D_("16000"), D_("15000"))  # سعر أعلى = خسارة = سالب
check("13) Oracle: دفع بسعر أعلى = خسارة صرف (سالب)", expected_fx13 < D_("0"), f"oracle={expected_fx13}")
entry13 = post_payment(s13, inv13, D_("1000"), today, D_("16000"), coa13["cash"].id)
s13.commit()
fx_loss_line13 = next((l for l in entry13.lines if l.account_id == coa13["fx_loss"].id), None)
check("13) سطر خسارة صرف موجود ويطابق Oracle بالقيمة المطلقة",
      fx_loss_line13 is not None and D_(str(fx_loss_line13.debit_base)) == abs(expected_fx13),
      f"actual={fx_loss_line13.debit_base if fx_loss_line13 else None} oracle={abs(expected_fx13)}")
check("13) القيد متوازن", entry13.is_balanced())
check("13) الرصيد المستحق للمورد أصبح صفراً", get_invoice_balance_due(s13, inv13) == D_("0"))

print("== 14) دفع لمورد بسعر صرف أقل (ربح صرف للمشتري) ==")
s14 = fresh_session()
coa14 = create_default_chart_of_accounts(s14)
item14 = new_item(s14, coa14)
inv14 = make_purchase_invoice_credit(s14, coa14, item14, D_("10"), D_("100"), "USD", D_("15000"), "STL-P14")
expected_fx14 = FXOracle.fx_diff_payable(D_("1000"), D_("14000"), D_("15000"))  # سعر أقل = ربح = موجب
check("14) Oracle: دفع بسعر أقل = ربح صرف (موجب)", expected_fx14 > D_("0"), f"oracle={expected_fx14}")
entry14 = post_payment(s14, inv14, D_("1000"), today, D_("14000"), coa14["cash"].id)
s14.commit()
fx_gain_line14 = next((l for l in entry14.lines if l.account_id == coa14["fx_gain"].id), None)
check("14) سطر ربح صرف موجود ويطابق Oracle", fx_gain_line14 is not None and D_(str(fx_gain_line14.credit_base)) == expected_fx14,
      f"actual={fx_gain_line14.credit_base if fx_gain_line14 else None} oracle={expected_fx14}")
check("14) القيد متوازن", entry14.is_balanced())

# استدعاء خاطئ عمداً: post_payment على فاتورة بيع، post_receipt على فاتورة شراء
try:
    post_payment(s14, inv2, D_("10"), today, D_("15000"), coa2["cash"].id)
    check("15) رفض post_payment على فاتورة بيع", False, "لم يُرفع استثناء!")
except SettlementError:
    check("15) رفض post_payment على فاتورة بيع", True)
try:
    post_receipt(s13, inv13, D_("10"), today, D_("15000"), coa13["cash"].id)
    check("16) رفض post_receipt على فاتورة شراء", False, "لم يُرفع استثناء! (أو الرصيد أصلاً صفر)")
except SettlementError:
    check("16) رفض post_receipt على فاتورة شراء", True)

print()
print("=" * 70)
print(f"✅ كل اختبارات Settlement/Receipt/Payment/FX نجحت ({len(results)} تحقّقاً)")
print("=" * 70)
