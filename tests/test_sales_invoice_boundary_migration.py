"""
tests/test_sales_invoice_boundary_migration.py
===================================================
Group 3-A — post_sales_invoice مُهاجَر إلى post_immediate() (Accounting
Posting Boundary). يغطي القائمة المطلوبة صراحة بالمراجعة: نقدي/آجل،
tax=0/>0، حسابات مبيعات/تكلفة/مخزون متعددة، عملة أجنبية، base-only
لـCOGS/Inventory، قمع الصفر، توازن، انتقال حالة الفاتورة، ربط المصدر،
رفض الترحيل المكرر — بالإضافة لحقن فشل حقيقي وتحقق rollback/atomicity.
"""
import os, sys, datetime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Account, AccountType, Item, CostMethod, Warehouse, InventoryMovement,
    MovementDirection, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, Setting,
    JournalEntry, JournalEntryStatus,
)
import app.services.posting as posting_mod
from app.services.posting import post_sales_invoice, PostingError
from app.services.invoice_validation import InvoiceValidationError

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def fresh_env(base_currency="SYP"):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    cash = Account(code="1101", name_ar="الصندوق", account_type=AccountType.ASSET)
    ar_default = Account(code="1102", name_ar="عملاء", account_type=AccountType.ASSET)
    tax_acc = Account(code="2300", name_ar="ضريبة مبيعات", account_type=AccountType.LIABILITY)
    inv_a = Account(code="1210", name_ar="مخزون أ", account_type=AccountType.ASSET)
    inv_b = Account(code="1220", name_ar="مخزون ب", account_type=AccountType.ASSET)
    cogs_a = Account(code="5110", name_ar="تكلفة أ", account_type=AccountType.EXPENSE)
    cogs_b = Account(code="5120", name_ar="تكلفة ب", account_type=AccountType.EXPENSE)
    sales_a = Account(code="4110", name_ar="مبيعات أ", account_type=AccountType.REVENUE)
    default_sales = Account(code="4100", name_ar="مبيعات عامة", account_type=AccountType.REVENUE)
    s.add_all([cash, ar_default, tax_acc, inv_a, inv_b, cogs_a, cogs_b, sales_a, default_sales])
    s.commit()
    wh = Warehouse(name_ar="الرئيسي")
    s.add(wh); s.commit()
    s.add_all([
        Setting(key="default_cash_account_id", value=str(cash.id)),
        Setting(key="default_sales_account_id", value=str(default_sales.id)),
        Setting(key="default_sales_tax_account_id", value=str(tax_acc.id)),
        Setting(key="base_currency", value=base_currency),
        Setting(key="ar_parent_account_id", value=str(ar_default.id)),
    ])
    s.commit()
    item_a = Item(sku="A", name_ar="مادة أ", inventory_account_id=inv_a.id, cogs_account_id=cogs_a.id,
                  sales_account_id=sales_a.id, cost_method=CostMethod.AVERAGE)
    item_b = Item(sku="B", name_ar="مادة ب", inventory_account_id=inv_b.id, cogs_account_id=cogs_b.id,
                  sales_account_id=None, cost_method=CostMethod.AVERAGE)
    s.add_all([item_a, item_b]); s.commit()
    today = datetime.date.today()
    s.add(InventoryMovement(item_id=item_a.id, warehouse_id=wh.id, direction=MovementDirection.IN,
                             quantity=100, unit_cost=D_("10"), movement_date=today, source_type="opening"))
    s.add(InventoryMovement(item_id=item_b.id, warehouse_id=wh.id, direction=MovementDirection.IN,
                             quantity=100, unit_cost=D_("20"), movement_date=today, source_type="opening"))
    s.commit()
    return dict(s=s, cash=cash, ar_default=ar_default, tax_acc=tax_acc, inv_a=inv_a, inv_b=inv_b,
                cogs_a=cogs_a, cogs_b=cogs_b, sales_a=sales_a, default_sales=default_sales,
                wh=wh, item_a=item_a, item_b=item_b, today=today)


def mk_invoice(env, lines, is_cash, currency="SYP", rate="1"):
    inv = Invoice(
        invoice_no=f"SI-{id(lines)}", kind=InvoiceKind.SALES, party_name="عميل تجريبي",
        invoice_date=env["today"], currency_code=currency, exchange_rate=D_(rate),
        status=InvoiceStatus.DRAFT, warehouse_id=env["wh"].id,
    )
    inv.lines = lines
    env["s"].add(inv); env["s"].commit()
    return inv


print("== 1) بيع نقدي (is_cash=True) ==")
env = fresh_env()
inv1 = mk_invoice(env, [InvoiceLine(item_id=env["item_a"].id, quantity=5, unit_price=D_("50"))], is_cash=True)
entry1 = post_sales_invoice(env["s"], inv1, is_cash=True)
env["s"].commit()
lines1 = {l.account_id: (l.debit, l.credit) for l in entry1.lines}
check("سطر Cash مديناً بإجمالي البيع", lines1.get(env["cash"].id) == (D_("250"), D_("0")), str(lines1))
check("لا سطر AR إطلاقاً بالبيع النقدي", env["ar_default"].id not in lines1)

print("\n== 2) بيع آجل (is_cash=False) — ينشئ/يستخدم حساب عميل ==")
env2 = fresh_env()
inv2 = mk_invoice(env2, [InvoiceLine(item_id=env2["item_a"].id, quantity=5, unit_price=D_("50"))], is_cash=False)
entry2 = post_sales_invoice(env2["s"], inv2, is_cash=False)
env2["s"].commit()
check("لا سطر بحساب الصندوق العام بالبيع الآجل", env2["cash"].id not in {l.account_id for l in entry2.lines})
check("القيد متوازن ويحوي 3 أسطر (AR+Sales+COGS+Inv=4 فعلياً، tax=0 فمحذوف)", len(entry2.lines) == 4)

print("\n== 3) tax = 0 — لا سطر ضريبة إطلاقاً ==")
check("لا حساب الضريبة ضمن أسطر القيد الأول (tax_rate=0 افتراضياً)", env["tax_acc"].id not in lines1)

print("\n== 4) tax > 0 — سطر ضريبة منفصل بالمبلغ الصحيح ==")
env4 = fresh_env()
line_with_tax = InvoiceLine(item_id=env4["item_a"].id, quantity=5, unit_price=D_("50"), tax_rate=D_("10"))
inv4 = mk_invoice(env4, [line_with_tax], is_cash=True)
entry4 = post_sales_invoice(env4["s"], inv4, is_cash=True)
env4["s"].commit()
lines4 = {l.account_id: (l.debit, l.credit) for l in entry4.lines}
check("سطر ضريبة 25 (10% من 250) دائناً", lines4.get(env4["tax_acc"].id) == (D_("0"), D_("25")), str(lines4))
check("سطر Cash مديناً بالإجمالي شاملاً الضريبة (275)", lines4.get(env4["cash"].id) == (D_("275"), D_("0")), str(lines4))

print("\n== 5) حسابات مبيعات/تكلفة/مخزون متعددة (item_a له حساب خاص، item_b يستخدم الافتراضي) ==")
env5 = fresh_env()
inv5 = mk_invoice(env5, [
    InvoiceLine(item_id=env5["item_a"].id, quantity=5, unit_price=D_("50")),
    InvoiceLine(item_id=env5["item_b"].id, quantity=3, unit_price=D_("80")),
], is_cash=True)
entry5 = post_sales_invoice(env5["s"], inv5, is_cash=True)
env5["s"].commit()
lines5 = {l.account_id: (l.debit, l.credit) for l in entry5.lines}
check("حساب مبيعات A المخصص = 250 دائن", lines5.get(env5["sales_a"].id) == (D_("0"), D_("250")))
check("حساب المبيعات الافتراضي لـB = 240 دائن", lines5.get(env5["default_sales"].id) == (D_("0"), D_("240")))
check("حساب تكلفة A منفصل = 50 مدين", lines5.get(env5["cogs_a"].id) == (D_("50"), D_("0")))
check("حساب تكلفة B منفصل = 60 مدين", lines5.get(env5["cogs_b"].id) == (D_("60"), D_("0")))
check("حساب مخزون A منفصل = 50 دائن", lines5.get(env5["inv_a"].id) == (D_("0"), D_("50")))
check("حساب مخزون B منفصل = 60 دائن", lines5.get(env5["inv_b"].id) == (D_("0"), D_("60")))

print("\n== 6) عملة أجنبية (USD مقابل أساسية SYP) — semantics raw/base لكل نوع سطر ==")
env6 = fresh_env(base_currency="SYP")
inv6 = mk_invoice(env6, [InvoiceLine(item_id=env6["item_a"].id, quantity=5, unit_price=D_("50"))],
                   is_cash=True, currency="USD", rate="15000")
entry6 = post_sales_invoice(env6["s"], inv6, is_cash=True)
env6["s"].commit()
cash_line = next(l for l in entry6.lines if l.account_id == env6["cash"].id)
cogs_line = next(l for l in entry6.lines if l.account_id == env6["cogs_a"].id)
check("سطر Cash: raw=250 (USD)، base=250×15000=3,750,000 (تحويل عبر سعر الفاتورة)",
      cash_line.debit == D_("250") and cash_line.debit_base == D_("3750000"),
      f"debit={cash_line.debit}, debit_base={cash_line.debit_base}")
check("سطر COGS: raw==base=50 بالضبط (base-only — لا علاقة بسعر صرف الفاتورة 15000 إطلاقاً)",
      cogs_line.debit == D_("50") and cogs_line.debit_base == D_("50"),
      f"debit={cogs_line.debit}, debit_base={cogs_line.debit_base}")

print("\n== 7) COGS/Inventory base-only semantics — line_currency_code/line_exchange_rate فارغان دائماً ==")
check("لا currency/rate على مستوى السطر لأي سطر (لا Cash ولا COGS) — كالسابق تماماً، لم يتغيّر",
      all(l.line_currency_code is None and l.line_exchange_rate is None for l in entry6.lines))

print("\n== 8) قمع الصفر — لا سطر COGS/Inventory لمادة تكلفتها صفر ==")
env8 = fresh_env()
zero_cost_item = Item(sku="Z", name_ar="مادة تكلفتها صفر", inventory_account_id=env8["inv_b"].id,
                       cogs_account_id=env8["cogs_b"].id, sales_account_id=None, cost_method=CostMethod.AVERAGE)
env8["s"].add(zero_cost_item); env8["s"].commit()
env8["s"].add(InventoryMovement(item_id=zero_cost_item.id, warehouse_id=env8["wh"].id, direction=MovementDirection.IN,
                                 quantity=50, unit_cost=D_("0"), movement_date=env8["today"], source_type="opening"))
env8["s"].commit()
inv8 = mk_invoice(env8, [InvoiceLine(item_id=zero_cost_item.id, quantity=5, unit_price=D_("30"))], is_cash=True)
entry8 = post_sales_invoice(env8["s"], inv8, is_cash=True)
env8["s"].commit()
check("لا سطر COGS إطلاقاً (المبلغ صفر)", env8["cogs_b"].id not in {l.account_id for l in entry8.lines})
check("لا سطر Inventory إطلاقاً (المبلغ صفر)", env8["inv_b"].id not in {l.account_id for l in entry8.lines})
check("لكن سطر المبيعات موجود رغم أن COGS صفر (لا فلترة صفر على المبيعات — سلوك موروث كما هو)",
      env8["default_sales"].id in {l.account_id for l in entry8.lines})

print("\n== 9) توازن القيد ==")
check("entry.is_balanced() لكل القيود أعلاه", all(e.is_balanced() for e in [entry1, entry2, entry4, entry5, entry6, entry8]))

print("\n== 10) انتقال حالة الفاتورة + ربط المصدر ==")
check("invoice.status = POSTED", inv1.status == InvoiceStatus.POSTED)
check("invoice.journal_entry_id = entry.id", inv1.journal_entry_id == entry1.id)
check("entry.source_type = sales_invoice, source_id = invoice.id",
      entry1.source_type == "sales_invoice" and entry1.source_id == inv1.id)

print("\n== 11) رفض الترحيل المكرر ==")
try:
    post_sales_invoice(env["s"], inv1, is_cash=True)
    check("رفض ترحيل فاتورة مرحَّلة أصلاً", False)
except PostingError:
    check("رفض ترحيل فاتورة مرحَّلة أصلاً", True)

print("\n== 12) namespace الترقيم الصحيح ==")
check("ref_no من namespace JE-SAL تحديداً (Sequence، لا COUNT/LIKE)", entry1.ref_no.startswith("JE-SAL-") and len(entry1.ref_no.split("-")[-1]) == 6)

# ============================================================
# حقن الفشل — Atomicity
# ============================================================

print("\n== 13) فشل Validation قبل أي mutation — لا أثر إطلاقاً ==")
env13 = fresh_env()
bad_invoice = mk_invoice(env13, [], is_cash=True)  # فاتورة بلا أسطر — يجب أن ترفضها validate_invoice_for_posting
try:
    post_sales_invoice(env13["s"], bad_invoice, is_cash=True)
    check("رفضت الفاتورة الفارغة", False)
except (PostingError, InvoiceValidationError):
    check("رفضت الفاتورة الفارغة", True)
check("حالة الفاتورة لم تتغيّر (بقيت DRAFT)", bad_invoice.status == InvoiceStatus.DRAFT)
check("لا JournalEntry واحد أُنشئ", env13["s"].query(JournalEntry).count() == 0)
check("لا InventoryMovement واحدة جديدة (فقط حركتا opening الأصليتان)", env13["s"].query(InventoryMovement).count() == 2)

print("\n== 14) عطل غير متوقَّع *بعد* post_immediate الناجح، أثناء إضافة الحركات/تحديث الفاتورة — Rollback كامل ==")
env14 = fresh_env()
inv14 = mk_invoice(env14, [InvoiceLine(item_id=env14["item_a"].id, quantity=5, unit_price=D_("50"))], is_cash=True)
orig_flush = env14["s"].flush
call_count = {"n": 0}
def boom_on_second_flush(*a, **k):
    call_count["n"] += 1
    if call_count["n"] >= 2:  # الأول من post_immediate الداخلية، الثاني هو flush النهائي بعد إضافة الحركات
        raise RuntimeError("محاكاة عطل بنية تحتية بعد نجاح post_immediate")
    return orig_flush(*a, **k)
env14["s"].flush = boom_on_second_flush
try:
    post_sales_invoice(env14["s"], inv14, is_cash=True)
    check("توقّع فشلاً ولم يحدث", False)
except RuntimeError:
    check("العطل المُحاكى حدث فعلياً كما هو متوقَّع", True)
finally:
    env14["s"].flush = orig_flush
env14["s"].expire_all()
check("لا JournalEntry يتيم بقاعدة البيانات (rollback أزال القيد الذي أنشأه post_immediate)",
      env14["s"].query(JournalEntry).count() == 0)
check("لا InventoryMovement جديدة يتيمة (فقط حركتا opening الأصليتان)",
      env14["s"].query(InventoryMovement).count() == 2)
fresh_invoice = env14["s"].get(Invoice, inv14.id)
check("حالة الفاتورة لم تتحوّل لـPOSTED كذباً — بقيت DRAFT فعلياً بقاعدة البيانات",
      fresh_invoice.status == InvoiceStatus.DRAFT)
check("journal_entry_id لم يُعبَّأ", fresh_invoice.journal_entry_id is None)

print("\n== 15) عطل داخل post_immediate() نفسها (قبل نجاحها) — نفس الضمانات ==")
env15 = fresh_env()
inv15 = mk_invoice(env15, [InvoiceLine(item_id=env15["item_a"].id, quantity=5, unit_price=D_("50"))], is_cash=True)
orig_post_immediate = posting_mod.post_immediate
def boom_post_immediate(*a, **k):
    raise RuntimeError("محاكاة عطل داخل post_immediate نفسها")
posting_mod.post_immediate = boom_post_immediate
try:
    post_sales_invoice(env15["s"], inv15, is_cash=True)
    check("توقّع فشلاً ولم يحدث", False)
except RuntimeError:
    check("العطل المُحاكى داخل post_immediate حدث فعلياً", True)
finally:
    posting_mod.post_immediate = orig_post_immediate
env15["s"].expire_all()
check("لا JournalEntry يتيم", env15["s"].query(JournalEntry).count() == 0)
check("لا InventoryMovement يتيمة جديدة", env15["s"].query(InventoryMovement).count() == 2)
fresh_invoice15 = env15["s"].get(Invoice, inv15.id)
check("الفاتورة بقيت DRAFT", fresh_invoice15.status == InvoiceStatus.DRAFT)

print("\n" + "=" * 70)
print(f"النتيجة: {sum(1 for _, c in results if c)}/{len(results)} نجح")
print("=" * 70)
