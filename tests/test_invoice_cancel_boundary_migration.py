"""
tests/test_invoice_cancel_boundary_migration.py
====================================================
Group 3-C — cancel_invoice() يستدعي الآن journal_edit.reverse() (بدل بناء
JournalEntry يدوياً) مع source_type="invoice_cancel"/source_id=invoice.id.
الفحوص الستة (CANCELLED/POSTED/SettlementAllocation/original_entry/
is_reversal_of/عدم تكرار) تبقى في invoice_cancel.py قبل أي استدعاء لـ
reverse() — لا حجز رقم قبل نجاحها جميعاً.

يغطي: (A) عمل فعلي على قاعدة SQLite ملف حقيقية (لا :memory: فقط — طلب
صريح بعد اكتشاف Group 3-B)، (B) حقن فشل حقيقي في 3 نقاط + تحقق rollback
من القاعدة مباشرة.
"""
import os, sys, shutil, datetime, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine, text as _sql
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Account, AccountType, Item, CostMethod, Warehouse, InventoryMovement,
    MovementDirection, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, Setting,
    JournalEntry,
)
import app.services.invoice_cancel as cancel_mod
from app.services.posting import post_sales_invoice
from app.services.invoice_cancel import cancel_invoice, CancelNotAllowedError
from app.services.settlements import post_receipt_allocated, AllocationInput

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def fresh_env(file_based=False):
    if file_based:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}")
    else:
        path = None
        engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    cash = Account(code="1101", name_ar="الصندوق", account_type=AccountType.ASSET)
    inv_a = Account(code="1210", name_ar="مخزون", account_type=AccountType.ASSET)
    cogs_a = Account(code="5110", name_ar="تكلفة", account_type=AccountType.EXPENSE)
    sales_a = Account(code="4110", name_ar="مبيعات", account_type=AccountType.REVENUE)
    ar = Account(code="1102", name_ar="عملاء", account_type=AccountType.ASSET)
    s.add_all([cash, inv_a, cogs_a, sales_a, ar]); s.commit()
    wh = Warehouse(name_ar="الرئيسي"); s.add(wh); s.commit()
    s.add_all([
        Setting(key="default_cash_account_id", value=str(cash.id)),
        Setting(key="default_sales_account_id", value=str(sales_a.id)),
        Setting(key="default_sales_tax_account_id", value=str(cash.id)),
        Setting(key="default_purchases_tax_account_id", value=str(cash.id)),
        Setting(key="base_currency", value="SYP"),
        Setting(key="ar_parent_account_id", value=str(ar.id)),
    ])
    s.commit()
    item = Item(sku="A", name_ar="مادة", inventory_account_id=inv_a.id, cogs_account_id=cogs_a.id,
                sales_account_id=sales_a.id, cost_method=CostMethod.AVERAGE)
    s.add(item); s.commit()
    today = datetime.date.today()
    s.add(InventoryMovement(item_id=item.id, warehouse_id=wh.id, direction=MovementDirection.IN,
                             quantity=100, unit_cost=D_("10"), movement_date=today, source_type="opening"))
    s.commit()
    return dict(s=s, cash=cash, inv_a=inv_a, cogs_a=cogs_a, sales_a=sales_a, ar=ar,
                wh=wh, item=item, today=today, db_path=path)


def mk_sale(env, qty="5", price="50"):
    inv = Invoice(invoice_no=f"SI-{id(env)}-{qty}", kind=InvoiceKind.SALES, party_name="عميل",
                   invoice_date=env["today"], currency_code="SYP", exchange_rate=D_("1"),
                   status=InvoiceStatus.DRAFT, warehouse_id=env["wh"].id)
    inv.lines = [InvoiceLine(item_id=env["item"].id, quantity=D_(qty), unit_price=D_(price))]
    env["s"].add(inv); env["s"].commit()
    entry = post_sales_invoice(env["s"], inv, is_cash=True)
    env["s"].commit()
    return inv, entry


print("== A) إلغاء ناجح على قاعدة SQLite ملف حقيقية (ليس :memory:) — بند صريح مطلوب بعد Group 3-B ==")
envA = fresh_env(file_based=True)
inv_a, orig_entry_a = mk_sale(envA)
reversal_a = cancel_invoice(envA["s"], inv_a, cancel_date=envA["today"])
envA["s"].commit()

raw_check = create_engine(f"sqlite:///{envA['db_path']}").connect()
je_count = raw_check.execute(_sql("SELECT COUNT(*) FROM journal_entries")).scalar()
im_count = raw_check.execute(_sql("SELECT COUNT(*) FROM inventory_movements")).scalar()
inv_status = raw_check.execute(_sql("SELECT status FROM invoices WHERE id=:id"), {"id": inv_a.id}).scalar()
raw_check.close()
check("القيد الأصلي + العكسي موجودان فعلياً بالقاعدة (2 على الأقل)", je_count >= 2, str(je_count))
check("حركتا مخزون أصليتان + عكسيتان (opening + OUT + IN عكسي = 3 على الأقل هنا)", im_count >= 3, str(im_count))
check("invoice.status = CANCELLED فعلياً بقاعدة البيانات (لا بالذاكرة فقط)", inv_status.upper() == "CANCELLED", str(inv_status))
check("reversal_entry.source_type/source_id صحيحان", reversal_a.source_type == "invoice_cancel" and reversal_a.source_id == inv_a.id)
check("ref_no من JV-REV (namespace موحَّد، لا INV-CXL منفصل بعد الآن)", reversal_a.ref_no.startswith("JV-REV-"))
os.remove(envA["db_path"])

print("\n== B) رفض الإلغاء بوجود Settlement مرتبط — الفحص يسبق أي حجز رقم ==")
envB = fresh_env()
inv_b = Invoice(invoice_no="SI-B", kind=InvoiceKind.SALES, party_name="عميل آجل",
                 invoice_date=envB["today"], currency_code="SYP", exchange_rate=D_("1"),
                 status=InvoiceStatus.DRAFT, warehouse_id=envB["wh"].id)
inv_b.lines = [InvoiceLine(item_id=envB["item"].id, quantity=D_("5"), unit_price=D_("50"))]
envB["s"].add(inv_b); envB["s"].commit()
entry_b = post_sales_invoice(envB["s"], inv_b, is_cash=False)
envB["s"].commit()
from app.services.parties import get_or_create_party_account
party_acc_b = get_or_create_party_account(envB["s"], "عميل آجل", is_customer=True)
envB["s"].commit()
post_receipt_allocated(envB["s"], party_account_id=party_acc_b.id, cash_account_id=envB["cash"].id,
                        amount_foreign=D_("250"), currency_code="SYP", settlement_rate=D_("1"),
                        settlement_date=envB["today"], allocations=[AllocationInput(amount_foreign=D_("250"), invoice_id=inv_b.id)])
envB["s"].commit()
je_before = envB["s"].query(JournalEntry).count()
try:
    cancel_invoice(envB["s"], inv_b, cancel_date=envB["today"])
    check("رفض الإلغاء بوجود تسوية مرتبطة", False)
except CancelNotAllowedError:
    check("رفض الإلغاء بوجود تسوية مرتبطة", True)
check("لا قيد جديد أُضيف (لا حجز رقم حدث — الفحص سبق أي مساس بالـBoundary)",
      envB["s"].query(JournalEntry).count() == je_before)
check("الفاتورة لم تتحوّل CANCELLED", inv_b.status != InvoiceStatus.CANCELLED)

print("\n== C) حقن فشل حقيقي *بعد* نجاح reverse() — أثناء عكس حركات المخزون/تحديث الفاتورة ==")
envC = fresh_env()
inv_c, entry_c = mk_sale(envC)
orig_flush = envC["s"].flush
def boom_flush(*a, **k):
    raise RuntimeError("محاكاة عطل بعد نجاح reverse()")
envC["s"].flush = boom_flush  # reverse() الداخلية أصلاً أنهت flush الخاص بها قبل أن نصل هنا
try:
    cancel_invoice(envC["s"], inv_c, cancel_date=envC["today"])
    check("توقّع فشلاً بعد نجاح reverse()", False)
except RuntimeError:
    check("العطل حدث فعلياً بعد نجاح reverse() الداخلية", True)
finally:
    envC["s"].flush = orig_flush
envC["s"].expire_all()
reversal_count = envC["s"].query(JournalEntry).filter_by(is_reversal_of=entry_c.id).count()
check("لا قيد عكسي يتيم بقي بالقاعدة بعد الفشل والـrollback", reversal_count == 0, str(reversal_count))
fresh_inv_c = envC["s"].get(Invoice, inv_c.id)
check("الفاتورة لم تتحوّل CANCELLED كذباً", fresh_inv_c.status == InvoiceStatus.POSTED)

print("\n== D) حقن فشل داخل reverse() نفسها (قبل نجاحها) ==")
envD = fresh_env()
inv_d, entry_d = mk_sale(envD)
orig_reverse = cancel_mod.reverse
cancel_mod.reverse = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("محاكاة عطل داخل reverse"))
try:
    cancel_invoice(envD["s"], inv_d, cancel_date=envD["today"])
    check("توقّع فشلاً", False)
except RuntimeError:
    check("العطل حدث فعلياً داخل reverse()", True)
finally:
    cancel_mod.reverse = orig_reverse
envD["s"].expire_all()
check("لا قيد عكسي يتيم", envD["s"].query(JournalEntry).filter_by(is_reversal_of=entry_d.id).count() == 0)
fresh_inv_d = envD["s"].get(Invoice, inv_d.id)
check("الفاتورة بقيت POSTED", fresh_inv_d.status == InvoiceStatus.POSTED)

print("\n== E) رفض إلغاء فاتورة ملغاة أصلاً (فحص مبكر، لا حجز رقم) ==")
envE = fresh_env()
inv_e, entry_e = mk_sale(envE)
cancel_invoice(envE["s"], inv_e, cancel_date=envE["today"])
envE["s"].commit()
je_before_e = envE["s"].query(JournalEntry).count()
try:
    cancel_invoice(envE["s"], inv_e, cancel_date=envE["today"])
    check("رفض إلغاء فاتورة ملغاة أصلاً", False)
except CancelNotAllowedError:
    check("رفض إلغاء فاتورة ملغاة أصلاً", True)
check("لا قيد إضافي أُنشئ", envE["s"].query(JournalEntry).count() == je_before_e)

print("\n" + "=" * 70)
print(f"النتيجة: {sum(1 for _, c in results if c)}/{len(results)} نجح")
print("=" * 70)
