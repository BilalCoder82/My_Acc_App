"""
tests/test_phase3b3_settlement_allocation.py
================================================
Acceptance Gate لـPhase 3B-3 — راجع PHASE3B3_DESIGN_SPEC.md §9 للقائمة
الكاملة. يغطي: تصنيف الحساب، OpeningPartyEntry (إنشاء/reverse/عزل)،
SettlementAllocation (Exclusive Arc، تعدد الأهداف، تسوية جزئية)، FX
لكل تخصيص، الدفعة الزائدة (سطر GL صافٍ واحد)، Refund (القيمة الدفترية
+ فحص الإشارة)، البيانات التاريخية (Migration مُختبَرة بملف منفصل)،
Append-Only.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Account, AccountSubtype, Warehouse, CostMethod, Setting,
    Invoice, InvoiceLine, InvoiceKind, InvoiceStatus,
    JournalEntry, JournalEntryStatus, OpeningPartyEntry, OpeningPartyKind,
    Settlement, SettlementAllocation,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.parties import get_or_create_party_account
from app.services.item_edit import create_item
from app.services.posting import post_sales_invoice, post_purchase_invoice, get_default_warehouse
from app.services.opening_balances import CLEARING_ACCOUNT_SETTING_KEY, OpeningBalanceError
from app.services.opening_party_balances import (
    post_opening_party_entry, reverse_opening_party_entry, get_opening_party_entry_balance_due,
)
from app.services.settlements import (
    post_receipt, post_payment, post_receipt_allocated, post_payment_allocated,
    post_customer_refund, post_supplier_refund, get_party_currency_balance,
    get_invoice_balance_due, AllocationInput, SettlementError,
)

today = datetime.date(2026, 1, 1)
results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def fresh_env(base_currency="USD"):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    coa = create_default_chart_of_accounts(session)
    session.add(Setting(key="base_currency", value=base_currency))
    equity = Account(code="3199", name_ar="أرصدة افتتاحية - توازن", account_type="EQUITY")
    session.add(equity); session.flush()
    session.add(Setting(key=CLEARING_ACCOUNT_SETTING_KEY, value=str(equity.id)))
    session.commit()
    return session, coa, equity


def make_customer(session, name="أحمد"):
    return get_or_create_party_account(session, name, is_customer=True)


def make_supplier(session, name="مورد X"):
    return get_or_create_party_account(session, name, is_customer=False)


def make_sale_invoice(session, coa, customer_name, quantity, price, wh_id, no, currency="USD", rate=D_("1")):
    item = create_item(session, sku=no, name_ar=f"مادة {no}", unit="قطعة",
                        inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                        cost_method=CostMethod.AVERAGE)
    inv = Invoice(invoice_no=no, kind=InvoiceKind.SALES, party_name=customer_name, invoice_date=today,
                  currency_code=currency, exchange_rate=rate, status=InvoiceStatus.DRAFT, warehouse_id=wh_id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=quantity, unit_price=price)]
    session.add(inv); session.commit()
    post_sales_invoice(session, inv, is_cash=False)
    session.commit()
    return inv


def make_purchase_invoice(session, coa, supplier_name, quantity, price, wh_id, no, currency="USD", rate=D_("1")):
    item = create_item(session, sku=no, name_ar=f"مادة {no}", unit="قطعة",
                        inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                        cost_method=CostMethod.AVERAGE)
    inv = Invoice(invoice_no=no, kind=InvoiceKind.PURCHASE, party_name=supplier_name, invoice_date=today,
                  currency_code=currency, exchange_rate=rate, status=InvoiceStatus.DRAFT, warehouse_id=wh_id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=quantity, unit_price=price)]
    session.add(inv); session.commit()
    post_purchase_invoice(session, inv, is_cash=False)
    session.commit()
    return inv


# =====================================================================
# 1) Account Classification
# =====================================================================
print("== 1) Account Classification ==")
s1, coa1, _ = fresh_env()
customer1 = make_customer(s1, "أحمد")
supplier1 = make_supplier(s1, "مورد1")
general1 = Account(code="9999", name_ar="حساب عام", account_type="ASSET")
s1.add(general1); s1.commit()
check("Customer صحيح", customer1.subtype == AccountSubtype.CUSTOMER)
check("Supplier صحيح", supplier1.subtype == AccountSubtype.SUPPLIER)
check("General لا يُعامَل كـCustomer/Supplier", general1.subtype == AccountSubtype.GENERAL)

try:
    post_opening_party_entry(s1, general1.id, OpeningPartyKind.RECEIVABLE, "REF-G", D_("100"), today)
    check("رفض OpeningPartyEntry على حساب GENERAL", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("رفض OpeningPartyEntry على حساب GENERAL", True)
s1.rollback()

try:
    post_opening_party_entry(s1, customer1.id, OpeningPartyKind.PAYABLE, "REF-BAD", D_("100"), today)
    check("Customer يرفض Payable opening", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("Customer يرفض Payable opening", True)
s1.rollback()

try:
    post_opening_party_entry(s1, supplier1.id, OpeningPartyKind.RECEIVABLE, "REF-BAD2", D_("100"), today)
    check("Supplier يرفض Receivable opening", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("Supplier يرفض Receivable opening", True)
s1.rollback()

# =====================================================================
# 2) OpeningPartyEntry — إنشاء، عزل القيود، Reverse
# =====================================================================
print("\n== 2) OpeningPartyEntry ==")
s2, coa2, equity2 = fresh_env()
customer2 = make_customer(s2, "أحمد")
opeA = post_opening_party_entry(s2, customer2.id, OpeningPartyKind.RECEIVABLE, "A", D_("4000"), today)
s2.commit()
opeB = post_opening_party_entry(s2, customer2.id, OpeningPartyKind.RECEIVABLE, "B", D_("3000"), today)
s2.commit()
check("رصيد A منشأ (id مستقل)", opeA.id != opeB.id)
check("A: JournalEntry مستقل عن B", opeA.journal_entry_id != opeB.journal_entry_id)
entryA = s2.get(JournalEntry, opeA.journal_entry_id)
entryB = s2.get(JournalEntry, opeB.journal_entry_id)
check("قيد A POSTED ومتوازن", entryA.status == JournalEntryStatus.POSTED and entryA.is_balanced())
check("قيد A لا يحتوي أسطر B (لا قيد مُجمَّع)", all(l.debit + l.credit in (D_("4000"), D_("0")) for l in entryA.lines))
check("رصيد A المتبقي = 4000 قبل أي تسوية", get_opening_party_entry_balance_due(s2, opeA) == D_("4000"))
check("رصيد B المتبقي = 3000 قبل أي تسوية", get_opening_party_entry_balance_due(s2, opeB) == D_("3000"))

# Reverse مستقل لكل سجل — عكس A لا يمس B
reversalA = reverse_opening_party_entry(s2, opeA, today + datetime.timedelta(days=1))
s2.commit()
check("عكس A نجح", reversalA.status == JournalEntryStatus.POSTED)
check("رصيد B لم يتأثر إطلاقاً بعكس A", get_opening_party_entry_balance_due(s2, opeB) == D_("3000"))
bal_after_reverse = get_party_currency_balance(s2, customer2.id, "USD")
# اتفاقية الإشارة (§13/foreign_balance=credit-debit): B لا تزال Receivable
# غير مسدَّدة (Dr AR = العميل مدين لنا) → رصيد سالب بهذه الاتفاقية،
# وليس دائناً موجباً — راجع post_customer_refund الذي يتطلب صراحة
# foreign_balance > 0 لرصيد دائن حقيقي فقط.
check("بعد عكس A: رصيد حساب أحمد = -3000 (B لا تزال ديناً غير مسدَّد، لا رصيداً دائناً)",
      bal_after_reverse.foreign_balance == D_("-3000"))

# =====================================================================
# 3) SettlementAllocation — تعدد الأهداف، Exclusive Arc، تسوية جزئية
# =====================================================================
print("\n== 3) SettlementAllocation ==")
s3, coa3, equity3 = fresh_env()
customer3 = make_customer(s3, "أحمد")
wh3 = Warehouse(name_ar="مستودع رئيسي", is_active=True); s3.add(wh3); s3.commit()
inv3 = make_sale_invoice(s3, coa3, "أحمد", D_("1"), D_("2000"), wh3.id, "SL-3")
opening3 = post_opening_party_entry(s3, customer3.id, OpeningPartyKind.RECEIVABLE, "OLD-1", D_("3000"), today)
s3.commit()

# قبض واحد 5,000 مُوزَّع: فاتورة 2,000 + رصيد افتتاحي 1,500 (تسوية جزئية للرصيد الافتتاحي)
entry3 = post_receipt_allocated(
    s3, party_account_id=customer3.id, amount_foreign=D_("3500"), currency_code="USD",
    settlement_rate=D_("1"), settlement_date=today, cash_account_id=coa3["cash"].id,
    allocations=[
        AllocationInput(amount_foreign=D_("2000"), invoice_id=inv3.id),
        AllocationInput(amount_foreign=D_("1500"), opening_party_entry_id=opening3.id),
    ],
)
s3.commit()
check("القيد متوازن (Multiple Allocation)", entry3.is_balanced())
check("فاتورة SL-3 أصبحت مسدَّدة بالكامل", get_invoice_balance_due(s3, inv3) == D_("0"))
check("الرصيد الافتتاحي: تسوية جزئية 1500، متبقي 1500",
      get_opening_party_entry_balance_due(s3, opening3) == D_("1500"))
allocs3 = s3.query(SettlementAllocation).filter_by(settlement_id=entry3.source_id or 0).all()
settlement3 = s3.query(Settlement).filter_by(journal_entry_id=entry3.id).first()
allocs3 = s3.query(SettlementAllocation).filter_by(settlement_id=settlement3.id).all()
check("عدد SettlementAllocation = 2 بالضبط لهذه التسوية", len(allocs3) == 2)

# Exclusive Arc DB-level: محاولة إدراج صف بلا هدف أو بهدفين معاً يجب أن يُرفَض بالـCHECK
import sqlalchemy.exc
try:
    bad = SettlementAllocation(settlement_id=settlement3.id, invoice_id=None, opening_party_entry_id=None,
                                 amount_foreign=D_("1"))
    s3.add(bad); s3.flush()
    check("Exclusive Arc: رفض صف بلا أي هدف", False, "لم يُرفَض!")
except sqlalchemy.exc.IntegrityError:
    check("Exclusive Arc: رفض صف بلا أي هدف (DB CHECK)", True)
s3.rollback()

try:
    settlement3b = s3.query(Settlement).filter_by(journal_entry_id=entry3.id).first()
    bad2 = SettlementAllocation(settlement_id=settlement3b.id, invoice_id=inv3.id,
                                  opening_party_entry_id=opening3.id, amount_foreign=D_("1"))
    s3.add(bad2); s3.flush()
    check("Exclusive Arc: رفض صف بهدفين معاً", False, "لم يُرفَض!")
except sqlalchemy.exc.IntegrityError:
    check("Exclusive Arc: رفض صف بهدفين معاً (DB CHECK)", True)
s3.rollback()

# رفض تجاوز remaining لهدف واحد بمعزل
try:
    post_receipt_allocated(
        s3, party_account_id=customer3.id, amount_foreign=D_("2000"), currency_code="USD",
        settlement_rate=D_("1"), settlement_date=today, cash_account_id=coa3["cash"].id,
        allocations=[AllocationInput(amount_foreign=D_("2000"), opening_party_entry_id=opening3.id)],
    )
    check("رفض تجاوز remaining الرصيد الافتتاحي (متبقي 1500 فقط)", False, "لم يُرفَض!")
except SettlementError:
    check("رفض تجاوز remaining الرصيد الافتتاحي (متبقي 1500 فقط)", True)
s3.rollback()

# رفض mismatch عملة الهدف مع عملة التسوية
inv3_eur = make_sale_invoice(s3, coa3, "أحمد", D_("1"), D_("100"), wh3.id, "SL-3EUR", currency="EUR", rate=D_("1.1"))
try:
    post_receipt_allocated(
        s3, party_account_id=customer3.id, amount_foreign=D_("100"), currency_code="USD",
        settlement_rate=D_("1"), settlement_date=today, cash_account_id=coa3["cash"].id,
        allocations=[AllocationInput(amount_foreign=D_("100"), invoice_id=inv3_eur.id)],
    )
    check("رفض mismatch عملة الهدف (EUR) مع عملة التسوية (USD)", False, "لم يُرفَض!")
except SettlementError:
    check("رفض mismatch عملة الهدف (EUR) مع عملة التسوية (USD)", True)
s3.rollback()

# =====================================================================
# 4) FX لكل Allocation + التجميع
# =====================================================================
print("\n== 4) FX per allocation + aggregate ==")
s4, coa4, equity4 = fresh_env()
customer4 = make_customer(s4, "أحمد")
wh4 = Warehouse(name_ar="مستودع رئيسي", is_active=True); s4.add(wh4); s4.commit()
inv4a = make_sale_invoice(s4, coa4, "أحمد", D_("1"), D_("1000"), wh4.id, "SL-4A", rate=D_("1.00"))
opening4 = post_opening_party_entry(s4, customer4.id, OpeningPartyKind.RECEIVABLE, "OLD-4",
                                     D_("500"), today, exchange_rate=D_("1.00"))
s4.commit()
entry4 = post_receipt_allocated(
    s4, party_account_id=customer4.id, amount_foreign=D_("1500"), currency_code="USD",
    settlement_rate=D_("1.10"), settlement_date=today, cash_account_id=coa4["cash"].id,
    allocations=[
        AllocationInput(amount_foreign=D_("1000"), invoice_id=inv4a.id),
        AllocationInput(amount_foreign=D_("500"), opening_party_entry_id=opening4.id),
    ],
)
s4.commit()
settlement4 = s4.query(Settlement).filter_by(journal_entry_id=entry4.id).first()
allocs4 = s4.query(SettlementAllocation).filter_by(settlement_id=settlement4.id).all()
expected_fx_total = (D_("1000") * D_("1.10") - D_("1000") * D_("1.00")) + (D_("500") * D_("1.10") - D_("500") * D_("1.00"))
actual_alloc_fx_sum = sum((D_(str(a.fx_amount)) for a in allocs4), D_("0"))
check("Σ(SettlementAllocation.fx_amount) = Oracle يدوي (150 بالضبط)",
      actual_alloc_fx_sum == expected_fx_total == D_("150.00"), f"actual={actual_alloc_fx_sum}")
fx_line = next((l for l in entry4.lines if l.account_id == coa4["fx_gain"].id), None)
check("سطر FX Gain بالقيد = 150 بالضبط (يطابق Σ(allocation.fx_amount))",
      fx_line is not None and D_(str(fx_line.credit_base)) == D_("150.00"))

# =====================================================================
# 5) الدفعة الزائدة — سطر GL صافٍ واحد، لا OpeningPartyEntry جديد
# =====================================================================
print("\n== 5) Overpayment: سطر GL صافٍ واحد ==")
s5, coa5, equity5 = fresh_env()
customer5 = make_customer(s5, "أحمد")
wh5 = Warehouse(name_ar="مستودع رئيسي", is_active=True); s5.add(wh5); s5.commit()
inv5 = make_sale_invoice(s5, coa5, "أحمد", D_("1"), D_("5000"), wh5.id, "SL-5", rate=D_("1.00"))
opening_count_before = s5.query(OpeningPartyEntry).count()
entry5 = post_receipt_allocated(
    s5, party_account_id=customer5.id, amount_foreign=D_("7000"), currency_code="USD",
    settlement_rate=D_("1.02"), settlement_date=today, cash_account_id=coa5["cash"].id,
    allocations=[AllocationInput(amount_foreign=D_("5000"), invoice_id=inv5.id)],
)
s5.commit()
opening_count_after = s5.query(OpeningPartyEntry).count()
check("OpeningPartyEntry.count() لم يتغيّر إطلاقاً بسبب الفائض (تصحيح Bilal)",
      opening_count_before == opening_count_after)
party_lines5 = [l for l in entry5.lines if l.account_id == customer5.id]
check("سطر GL واحد صافٍ فقط لحساب العميل (لا سطرين)", len(party_lines5) == 1)
# allocated_booked=5000×1.00=5000 ; unallocated_booked=2000×1.02=2040 ; net=7040
check("قيمة السطر الصافي = 7040 بالضبط (5000 دفتري + 2000×1.02 فائض)",
      D_(str(party_lines5[0].credit_base)) == D_("7040.00"), str(party_lines5[0].credit_base))
settlement5 = s5.query(Settlement).filter_by(journal_entry_id=entry5.id).first()
allocs5 = s5.query(SettlementAllocation).filter_by(settlement_id=settlement5.id).all()
check("SettlementAllocation واحد فقط (الفاتورة) — لا صف للفائض", len(allocs5) == 1)
bal5 = get_party_currency_balance(s5, customer5.id, "USD")
check("رصيد حساب أحمد النهائي = 2000 USD دائن (المبلغ الفعلي غير المخصَّص، لا القيمة الأساسية المُدمَجة)",
      bal5.foreign_balance == D_("2000.00"), str(bal5))
check("رصيد حساب أحمد الأساسي (base) = 2040 (القيمة المُدمَجة الصحيحة)",
      bal5.base_balance == D_("2040.00"), str(bal5))

# =====================================================================
# 6) Refund — القيمة الدفترية + فحص الإشارة
# =====================================================================
print("\n== 6) Refund: carrying_rate + sign check ==")
s6, coa6, equity6 = fresh_env()
customer6 = make_customer(s6, "أحمد")
wh6 = Warehouse(name_ar="مستودع رئيسي", is_active=True); s6.add(wh6); s6.commit()
inv6 = make_sale_invoice(s6, coa6, "أحمد", D_("1"), D_("5000"), wh6.id, "SL-6", rate=D_("1.00"))
post_receipt_allocated(
    s6, party_account_id=customer6.id, amount_foreign=D_("7000"), currency_code="USD",
    settlement_rate=D_("1.02"), settlement_date=today, cash_account_id=coa6["cash"].id,
    allocations=[AllocationInput(amount_foreign=D_("5000"), invoice_id=inv6.id)],
)
s6.commit()
bal6 = get_party_currency_balance(s6, customer6.id, "USD")
check("رصيد أحمد قبل Refund: foreign=2000, base=2040", bal6.foreign_balance == D_("2000.00") and bal6.base_balance == D_("2040.00"))
carrying_rate_oracle = bal6.base_balance / bal6.foreign_balance
check("Oracle مستقل: carrying_rate = 1.02 بالضبط", carrying_rate_oracle == D_("1.02"))

refund_entry6 = post_customer_refund(s6, customer6.id, D_("1000"), "USD", D_("1.03"), today, coa6["cash"].id)
s6.commit()
party_line6 = next(l for l in refund_entry6.lines if l.account_id == customer6.id)
check("Dr party_account_id = القيمة الدفترية (1020) لا القيمة الجديدة (1030)",
      D_(str(party_line6.debit_base)) == D_("1020.00"), str(party_line6.debit_base))
cash_line6 = next(l for l in refund_entry6.lines if l.account_id == coa6["cash"].id)
check("Cr Cash = القيمة الجديدة الفعلية (1030)", D_(str(cash_line6.credit_base)) == D_("1030.00"))
fx_loss_line6 = next((l for l in refund_entry6.lines if l.account_id == coa6["fx_loss"].id), None)
check("خسارة صرف = 10 بالضبط (1030-1020) لأن refund_rate>carrying_rate",
      fx_loss_line6 is not None and D_(str(fx_loss_line6.debit_base)) == D_("10.00"))

# نفس السعر بالضبط → fx=0
s6b, coa6b, _ = fresh_env()
customer6b = make_customer(s6b, "أحمد")
wh6b = Warehouse(name_ar="مستودع رئيسي", is_active=True); s6b.add(wh6b); s6b.commit()
inv6b = make_sale_invoice(s6b, coa6b, "أحمد", D_("1"), D_("5000"), wh6b.id, "SL-6B", rate=D_("1.00"))
post_receipt_allocated(s6b, party_account_id=customer6b.id, amount_foreign=D_("7000"), currency_code="USD",
                        settlement_rate=D_("1.02"), settlement_date=today, cash_account_id=coa6b["cash"].id,
                        allocations=[AllocationInput(amount_foreign=D_("5000"), invoice_id=inv6b.id)])
s6b.commit()
refund6b = post_customer_refund(s6b, customer6b.id, D_("1000"), "USD", D_("1.02"), today, coa6b["cash"].id)
s6b.commit()
check("Refund بسعر مطابق للسعر الدفتري: لا سطر FX إطلاقاً",
      not any(l.account_id in (coa6b["fx_gain"].id, coa6b["fx_loss"].id) for l in refund6b.lines))

# رفض Customer Refund على حساب مدين (لا رصيد دائن)
s6c, coa6c, _ = fresh_env()
customer6c = make_customer(s6c, "خالد")
try:
    post_customer_refund(s6c, customer6c.id, D_("100"), "USD", D_("1"), today, coa6c["cash"].id)
    check("رفض Customer Refund بلا رصيد دائن (foreign_balance<=0)", False, "لم يُرفَض!")
except SettlementError:
    check("رفض Customer Refund بلا رصيد دائن (foreign_balance<=0)", True)

# رفض تجاوز الرصيد المتاح بنفس العملة تحديداً (اختبار عزل العملات)
s6d, coa6d, _ = fresh_env()
customer6d = make_customer(s6d, "أحمد")
wh6d = Warehouse(name_ar="مستودع رئيسي", is_active=True); s6d.add(wh6d); s6d.commit()
inv6d_usd = make_sale_invoice(s6d, coa6d, "أحمد", D_("1"), D_("100"), wh6d.id, "SL-6D-USD", currency="USD", rate=D_("1"))
inv6d_eur = make_sale_invoice(s6d, coa6d, "أحمد", D_("1"), D_("50"), wh6d.id, "SL-6D-EUR", currency="EUR", rate=D_("1.1"))
post_receipt_allocated(s6d, party_account_id=customer6d.id, amount_foreign=D_("300"), currency_code="USD",
                        settlement_rate=D_("1"), settlement_date=today, cash_account_id=coa6d["cash"].id,
                        allocations=[AllocationInput(amount_foreign=D_("100"), invoice_id=inv6d_usd.id)])
s6d.commit()
post_receipt_allocated(s6d, party_account_id=customer6d.id, amount_foreign=D_("100"), currency_code="EUR",
                        settlement_rate=D_("1.1"), settlement_date=today, cash_account_id=coa6d["cash"].id,
                        allocations=[AllocationInput(amount_foreign=D_("50"), invoice_id=inv6d_eur.id)])
s6d.commit()
bal_usd_6d = get_party_currency_balance(s6d, customer6d.id, "USD")
bal_eur_6d = get_party_currency_balance(s6d, customer6d.id, "EUR")
check("رصيد USD معزول = 200 (300-100)", bal_usd_6d.foreign_balance == D_("200.00"))
check("رصيد EUR معزول = 50 (100-50)", bal_eur_6d.foreign_balance == D_("50.00"))
try:
    post_customer_refund(s6d, customer6d.id, D_("100"), "EUR", D_("1.1"), today, coa6d["cash"].id)
    check("رفض Refund EUR يتجاوز رصيد EUR المتاح (50) رغم كفاية USD", False, "لم يُرفَض!")
except SettlementError:
    check("رفض Refund EUR يتجاوز رصيد EUR المتاح (50) رغم كفاية USD (عزل العملات)", True)

# Supplier Refund — اتجاه FX معكوس (تحذير Bilal الأخير)
s6e, coa6e, _ = fresh_env()
supplier6e = make_supplier(s6e, "مورد6")
wh6e = Warehouse(name_ar="مستودع رئيسي", is_active=True); s6e.add(wh6e); s6e.commit()
pinv6e = make_purchase_invoice(s6e, coa6e, "مورد6", D_("1"), D_("5000"), wh6e.id, "PU-6E", rate=D_("1.00"))
post_payment_allocated(s6e, party_account_id=supplier6e.id, amount_foreign=D_("7000"), currency_code="USD",
                        settlement_rate=D_("1.02"), settlement_date=today, cash_account_id=coa6e["cash"].id,
                        allocations=[AllocationInput(amount_foreign=D_("5000"), invoice_id=pinv6e.id)])
s6e.commit()
bal6e = get_party_currency_balance(s6e, supplier6e.id, "USD")
check("رصيد المورد بعد الدفع الزائد: foreign=-2000 (مدين لنا)", bal6e.foreign_balance == D_("-2000.00"))
refund6e = post_supplier_refund(s6e, supplier6e.id, D_("1000"), "USD", D_("1.03"), today, coa6e["cash"].id)
s6e.commit()
fx_gain_line6e = next((l for l in refund6e.lines if l.account_id == coa6e["fx_gain"].id), None)
check("Supplier Refund بـrefund_fx موجب → FX Gain (ربح، لا خسارة) — عكس حالة العميل تماماً",
      fx_gain_line6e is not None and D_(str(fx_gain_line6e.credit_base)) == D_("10.00"))
check("لا سطر FX Loss لحالة المورد هذه", not any(l.account_id == coa6e["fx_loss"].id for l in refund6e.lines))

try:
    post_supplier_refund(s6e, supplier6e.id, D_("100"), "USD", D_("1"), today, coa6e["cash"].id)
except SettlementError:
    pass
else:
    pass  # لا يُفترض رفض هنا؛ يوجد رصيد كافٍ (1000 متبقٍ)

# رفض Supplier Refund على حساب دائن (لا رصيد مدين)
s6f, coa6f, _ = fresh_env()
supplier6f = make_supplier(s6f, "مورد جديد")
try:
    post_supplier_refund(s6f, supplier6f.id, D_("100"), "USD", D_("1"), today, coa6f["cash"].id)
    check("رفض Supplier Refund بلا رصيد مدين (foreign_balance>=0)", False, "لم يُرفَض!")
except SettlementError:
    check("رفض Supplier Refund بلا رصيد مدين (foreign_balance>=0)", True)

# =====================================================================
# 7) Migration/Backfill — مُختبَر بملف منفصل (test_phase3b3_migration.py)
# =====================================================================
print("\n== 7) ملاحظة: Migration/Backfill مُختبَر بملف منفصل test_phase3b3_migration.py ==")

# =====================================================================
# =====================================================================
# 7-ب) Hardening — DB CHECK/UNIQUE constraints (§13 بالمواصفة)
# =====================================================================
print("\n== 7-ب) Hardening: DB CHECK/UNIQUE ==")
s7b, coa7b, _ = fresh_env()
customer7b = make_customer(s7b, "أحمد")
s7b.commit()

try:
    bad_ope = OpeningPartyEntry(journal_entry_id=1, party_account_id=customer7b.id,
                                  kind=OpeningPartyKind.RECEIVABLE, reference="X",
                                  original_amount_foreign=D_("-5"), currency_code="USD",
                                  exchange_rate=D_("1"), amount_base=D_("-5"), opening_date=today)
    s7b.add(bad_ope); s7b.flush()
    check("DB CHECK يرفض OpeningPartyEntry.original_amount_foreign سالب", False, "لم يُرفَض!")
except sqlalchemy.exc.IntegrityError:
    check("DB CHECK يرفض OpeningPartyEntry.original_amount_foreign سالب", True)
s7b.rollback()

opening7b_1 = post_opening_party_entry(s7b, customer7b.id, OpeningPartyKind.RECEIVABLE, "R1", D_("100"), today)
s7b.commit()
try:
    dup_ope = OpeningPartyEntry(journal_entry_id=opening7b_1.journal_entry_id, party_account_id=customer7b.id,
                                  kind=OpeningPartyKind.RECEIVABLE, reference="R2",
                                  original_amount_foreign=D_("50"), currency_code="USD",
                                  exchange_rate=D_("1"), amount_base=D_("50"), opening_date=today)
    s7b.add(dup_ope); s7b.flush()
    check("DB UNIQUE يرفض journal_entry_id مكرر بـOpeningPartyEntry (1:1)", False, "لم يُرفَض!")
except sqlalchemy.exc.IntegrityError:
    check("DB UNIQUE يرفض journal_entry_id مكرر بـOpeningPartyEntry (1:1)", True)
s7b.rollback()

wh7b = Warehouse(name_ar="مستودع", is_active=True); s7b.add(wh7b); s7b.commit()
inv7b = make_sale_invoice(s7b, coa7b, "أحمد", D_("1"), D_("100"), wh7b.id, "SL-7B")
settle7b = post_receipt(s7b, inv7b, D_("50"), today, D_("1"), coa7b["cash"].id)
s7b.commit()
settlement7b = s7b.query(Settlement).filter_by(journal_entry_id=settle7b.id).first()
try:
    dup_settle = Settlement(journal_entry_id=settle7b.id, party_account_id=customer7b.id, kind="receipt",
                              settlement_date=today, currency_code="USD", amount_foreign=D_("1"),
                              settlement_rate=D_("1"))
    s7b.add(dup_settle); s7b.flush()
    check("DB UNIQUE يرفض journal_entry_id مكرر بـSettlement (1:1)", False, "لم يُرفَض!")
except sqlalchemy.exc.IntegrityError:
    check("DB UNIQUE يرفض journal_entry_id مكرر بـSettlement (1:1)", True)
s7b.rollback()

# =====================================================================
# 8) Append-Only — SettlementAllocation
# =====================================================================
print("\n== 8) Append-Only: لا update/delete لـSettlementAllocation ==")
import app.services.settlements as settlements_module
check("لا توجد دالة update_settlement_allocation", not hasattr(settlements_module, "update_settlement_allocation"))
check("لا توجد دالة delete_settlement_allocation", not hasattr(settlements_module, "delete_settlement_allocation"))

# =====================================================================
print(f"\n{'='*70}\nالنتيجة: {sum(1 for _, ok in results if ok)}/{len(results)} نجح\n{'='*70}")
print("✅ كل اختبارات Phase 3B-3 (Settlement Allocation) نجحت")
