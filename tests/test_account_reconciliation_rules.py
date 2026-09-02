"""
tests/test_account_reconciliation_rules.py
==============================================
إغلاق Phase 2 / بند 1: قواعد subtype + allow_reconciliation عبر الخدمة
مباشرة (لا الواجهة) — تُثبِت أن القرار شرطان معاً (subtype في
CUSTOMER/SUPPLIER وallow_reconciliation=True)، ولا اعتماد إطلاقاً على
account_type أو رقم/بادئة الحساب.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, CostMethod,
    Account, AccountType, AccountSubtype,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_sales_invoice, post_purchase_invoice
from app.services.settlements import post_receipt, post_payment, SettlementError

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
session = sessionmaker(bind=engine)()
coa = create_default_chart_of_accounts(session)
item = create_item(session, sku="AR-1", name_ar="مادة", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
session.commit()


def make_account(code, name, atype, subtype, allow_recon):
    a = Account(code=code, name_ar=name, account_type=atype, subtype=subtype,
                allow_reconciliation=allow_recon, is_group=False)
    session.add(a); session.flush()
    return a


def make_sales_invoice(no, party_account_id_override=None):
    """فاتورة بيع تُرحَّل يدوياً مع تحكّم كامل بالحساب المقابل — نستخدم
    post_sales_invoice العادية ثم نُبدِّل حساب السطر الأول يدوياً بعد
    الترحيل لاختبار حسابات لا يُنشئها get_or_create_party_account أصلاً
    (Expense/Income/Cash/Bank/General) — هذه ليست حالة واقعية بالمنتج
    (لن ينشئها المستخدم هكذا من واجهة فاتورة بيع عادية) لكنها الطريقة
    الوحيدة لاختبار كل تركيبة subtype يطلبها Bilal عبر مسار حقيقي.
    """
    inv = Invoice(invoice_no=no, kind=InvoiceKind.SALES, party_name="طرف اختبار",
                   invoice_date=today, currency_code="SYP", exchange_rate=D_("1"),
                   status=InvoiceStatus.DRAFT)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=D_("1"), unit_price=D_("1000"))]
    session.add(inv); session.commit()
    post_sales_invoice(session, inv, is_cash=False)
    session.commit()
    if party_account_id_override is not None:
        from app.models import JournalEntry
        entry = session.get(JournalEntry, inv.journal_entry_id)
        entry.lines[0].account_id = party_account_id_override
        session.commit()
    return inv


def try_settle(inv, expect_ok: bool, case_name: str):
    try:
        post_receipt(session, inv, D_("100"), today, D_("1"), coa["cash"].id)
        if expect_ok:
            check(case_name, True)
        else:
            check(case_name, False, "التسوية نجحت رغم أنها يجب أن تُرفَض!")
    except SettlementError as e:
        if expect_ok:
            check(case_name, False, f"رُفضت التسوية وكان يجب أن تنجح: {e}")
        else:
            check(case_name, True)


# =====================================================================
# 1) Customer + allow_reconciliation=True → يسمح
# =====================================================================
cust = make_account("9001", "عميل اختباري", AccountType.ASSET, AccountSubtype.CUSTOMER, True)
inv1 = make_sales_invoice("AR-C1", cust.id)
try_settle(inv1, True, "Customer + allow_reconciliation=True → التسوية تُقبَل")

# =====================================================================
# 2) Supplier + allow_reconciliation=True → يسمح
# =====================================================================
supp = make_account("9002", "مورد اختباري", AccountType.LIABILITY, AccountSubtype.SUPPLIER, True)
inv2 = Invoice(invoice_no="AR-S1", kind=InvoiceKind.PURCHASE, party_name="مورد اختباري",
               invoice_date=today, currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
inv2.lines = [InvoiceLine(item_id=item.id, quantity=D_("1"), unit_price=D_("1000"))]
session.add(inv2); session.commit()
post_purchase_invoice(session, inv2, is_cash=False)
session.commit()
from app.models import JournalEntry
entry2 = session.get(JournalEntry, inv2.journal_entry_id)
entry2.lines[0].account_id = supp.id
session.commit()
try:
    post_payment(session, inv2, D_("100"), today, D_("1"), coa["cash"].id)
    check("Supplier + allow_reconciliation=True → التسوية تُقبَل", True)
except SettlementError as e:
    check("Supplier + allow_reconciliation=True → التسوية تُقبَل", False, str(e))

# =====================================================================
# 3-6) Expense/Income/Cash/Bank/General → لا يسمح (حتى مع allow_reconciliation=True
#      يدوياً على بعضها — لإثبات أن subtype شرط مستقل، لا يكفي allow_reconciliation وحده)
# =====================================================================
for i, (label, subtype, atype) in enumerate([
    ("Expense", AccountSubtype.EXPENSE, AccountType.EXPENSE),
    ("Income", AccountSubtype.INCOME, AccountType.REVENUE),
    ("Cash", AccountSubtype.CASH, AccountType.ASSET),
    ("Bank", AccountSubtype.BANK, AccountType.ASSET),
    ("General", AccountSubtype.GENERAL, AccountType.ASSET),
]):
    acc = make_account(f"91{i:02d}", f"حساب {label}", atype, subtype, allow_recon=True)
    inv = make_sales_invoice(f"AR-{label}", acc.id)
    try_settle(inv, False, f"{label} (حتى مع allow_reconciliation=True يدوياً) → التسوية تُرفَض (subtype ليس عميلاً/مورداً)")

# حساب تجميعي (Group) — GENERAL افتراضياً، لا يُستخدَم بفاتورة أصلاً
# لكن نتحقق أن is_group لا يمنح استثناءً لو استُخدِم قسراً
group_acc = make_account("9200", "مجموعة اختبارية", AccountType.ASSET, AccountSubtype.GENERAL, allow_recon=True)
group_acc.is_group = True
session.commit()
inv_group = make_sales_invoice("AR-GROUP", group_acc.id)
try_settle(inv_group, False, "حساب تجميعي (is_group) بـsubtype=GENERAL → التسوية تُرفَض")

# =====================================================================
# 7) Customer/Supplier مع allow_reconciliation=False → يُرفَض من
#    الخدمة حتى لو حاولت الواجهة تجاوزه (نتجاوز الواجهة هنا فعلياً
#    باستدعاء الخدمة مباشرة، تماماً كما لو كانت الواجهة معطوبة/مُتلاعَباً بها)
# =====================================================================
cust_no_recon = make_account("9003", "عميل بلا صلاحية", AccountType.ASSET, AccountSubtype.CUSTOMER, False)
inv3 = make_sales_invoice("AR-C2", cust_no_recon.id)
try_settle(inv3, False, "Customer + allow_reconciliation=False → التسوية تُرفَض حتى بتجاوز الواجهة")

# =====================================================================
# 8) تغيير subtype من Customer إلى General ينعكس فوراً على الصلاحية
#    (هذا بالضبط ما كشف الثغرة المُصلَحة أعلاه)
# =====================================================================
dynamic_acc = make_account("9004", "عميل متحوّل", AccountType.ASSET, AccountSubtype.CUSTOMER, True)
inv4 = make_sales_invoice("AR-DYN", dynamic_acc.id)
try_settle(inv4, True, "قبل التغيير: Customer+True → مقبول")
dynamic_acc.subtype = AccountSubtype.GENERAL
session.commit()
try_settle(inv4, False, "بعد تغيير subtype إلى General (allow_reconciliation ما زال True): التسوية تُرفَض فوراً")
dynamic_acc.subtype = AccountSubtype.CUSTOMER
session.commit()
try_settle(inv4, True, "إعادة subtype إلى Customer: التسوية تُقبَل فوراً مجدداً")

# =====================================================================
# 9) لا اعتماد إطلاقاً على رقم/بادئة الحساب — عميل بكود لا يشبه نمط AR
#    التقليدي (لا بادئة 1103) ينجح بنفس القدر تماماً
# =====================================================================
weird_code_cust = make_account("ZZZ-999-CUSTOM", "عميل بكود غريب", AccountType.LIABILITY, AccountSubtype.CUSTOMER, True)
inv5 = make_sales_invoice("AR-WEIRD", weird_code_cust.id)
try_settle(inv5, True, "عميل بكود/بادئة غير تقليدية إطلاقاً (ZZZ-999-CUSTOM) → التسوية تُقبَل (لا اعتماد على الكود)")

print()
print("=" * 70)
print(f"✅ قواعد subtype/allow_reconciliation عبر الخدمة مباشرة — {len(results)} تحقّقاً")
print("=" * 70)
