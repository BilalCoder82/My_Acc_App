"""
tests/test_invoice_cycle_customer_supplier.py
==================================================
إغلاق Phase 2 / بند 3: Invoice → POSTED → Settlement → Balance Due
لكل من Customer وSupplier، بعد إضافة subtype/allow_reconciliation —
تسوية جزئية، كاملة، أكثر من تسوية، رفض DRAFT، رفض تجاوز الرصيد، رفض
حساب غير قابل للمطابقة.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, CostMethod, Settlement,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
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


def run_cycle(kind_label: str, invoice_kind, settle_fn, party_name):
    print(f"\n== دورة {kind_label} ==")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    coa = create_default_chart_of_accounts(session)
    item = create_item(session, sku=f"CYC-{kind_label}", name_ar="مادة", unit="قطعة",
                        inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                        cost_method=CostMethod.AVERAGE)
    session.commit()

    inv = Invoice(invoice_no=f"CYC-{kind_label}-1", kind=invoice_kind, party_name=party_name,
                   invoice_date=today, currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=D_("10"), unit_price=D_("1000"))]
    session.add(inv); session.commit()

    # --- رفض تسوية فاتورة DRAFT ---
    try:
        settle_fn(session, inv, D_("1000"), today, D_("1"), coa["cash"].id)
        check(f"[{kind_label}] رفض تسوية DRAFT", False, "لم تُرفَض!")
    except SettlementError:
        check(f"[{kind_label}] رفض تسوية DRAFT", True)

    posting_fn = post_sales_invoice if invoice_kind == InvoiceKind.SALES else post_purchase_invoice
    posting_fn(session, inv, is_cash=False)
    session.commit()
    check(f"[{kind_label}] الفاتورة POSTED فعلياً", inv.status == InvoiceStatus.POSTED)

    initial_balance = get_invoice_balance_due(session, inv)
    check(f"[{kind_label}] الرصيد الابتدائي = 10,000 بالضبط", initial_balance == D_("10000"))

    # --- تسوية جزئية ---
    settle_fn(session, inv, D_("3000"), today, D_("1"), coa["cash"].id)
    session.commit()
    balance_after_1 = get_invoice_balance_due(session, inv)
    check(f"[{kind_label}] بعد تسوية جزئية 3,000: الرصيد = 7,000", balance_after_1 == D_("7000"))

    # --- أكثر من تسوية (ثانية جزئية) ---
    settle_fn(session, inv, D_("2000"), today, D_("1"), coa["cash"].id)
    session.commit()
    balance_after_2 = get_invoice_balance_due(session, inv)
    check(f"[{kind_label}] بعد تسوية جزئية ثانية 2,000: الرصيد = 5,000", balance_after_2 == D_("5000"))

    # --- رفض تجاوز الرصيد (محاولة تسوية 6,000 والمتبقي 5,000 فقط) ---
    try:
        settle_fn(session, inv, D_("6000"), today, D_("1"), coa["cash"].id)
        check(f"[{kind_label}] رفض تجاوز الرصيد المتبقي", False, "لم تُرفَض تسوية أكبر من الرصيد!")
    except SettlementError:
        check(f"[{kind_label}] رفض تجاوز الرصيد المتبقي", True)
    check(f"[{kind_label}] الرصيد لم يتغيّر بعد محاولة التجاوز المرفوضة",
          get_invoice_balance_due(session, inv) == D_("5000"))

    # --- تسوية كاملة (المتبقي بالضبط) ---
    settle_fn(session, inv, D_("5000"), today, D_("1"), coa["cash"].id)
    session.commit()
    final_balance = get_invoice_balance_due(session, inv)
    check(f"[{kind_label}] بعد التسوية الكاملة: الرصيد = صفر بالضبط", final_balance == D_("0"))

    settlements_count = session.query(Settlement).filter_by(invoice_id=inv.id).count()
    check(f"[{kind_label}] 3 سجلات Settlement فعلية (جزئية×2 + كاملة)", settlements_count == 3)

    # --- رفض التسوية لحساب غير قابل للمطابقة (نُعطِّل allow_reconciliation
    #     على حساب الطرف بعد كل هذا، ونتأكد فاتورة جديدة عليه تُرفَض) ---
    from app.models import JournalEntry
    entry = session.get(JournalEntry, inv.journal_entry_id)
    party_account = session.get(type(entry.lines[0].account), entry.lines[0].account_id) if False else None
    from app.models import Account
    party_account = session.get(Account, entry.lines[0].account_id)
    party_account.allow_reconciliation = False
    session.commit()

    inv2 = Invoice(invoice_no=f"CYC-{kind_label}-2", kind=invoice_kind, party_name=party_name,
                    invoice_date=today, currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
    inv2.lines = [InvoiceLine(item_id=item.id, quantity=D_("1"), unit_price=D_("500"))]
    session.add(inv2); session.commit()
    posting_fn(session, inv2, is_cash=False)
    session.commit()
    try:
        settle_fn(session, inv2, D_("100"), today, D_("1"), coa["cash"].id)
        check(f"[{kind_label}] رفض التسوية لحساب غير قابل للمطابقة", False, "لم تُرفَض!")
    except SettlementError:
        check(f"[{kind_label}] رفض التسوية لحساب غير قابل للمطابقة", True)


run_cycle("Customer", InvoiceKind.SALES, post_receipt, "عميل الدورة الكاملة")
run_cycle("Supplier", InvoiceKind.PURCHASE, post_payment, "مورد الدورة الكاملة")

print()
print("=" * 70)
print(f"✅ دورة Invoice→POSTED→Settlement→Balance لعميل ومورد نجحت — {len(results)} تحقّقاً")
print("=" * 70)
