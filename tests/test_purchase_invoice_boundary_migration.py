"""
tests/test_purchase_invoice_boundary_migration.py
======================================================
Group 3-B — post_purchase_invoice مُهاجَر إلى post_immediate(). نفس
منهجية Group 3-A، مع تركيز خاص على النقطة الحرجة: unit_cost (تحويل
مستقل للعملة الأساسية) منفصل تماماً عن AccountingIntent (يبقى بعملة
الفاتورة الخام عبر _line_intent، مثل Cash/AP تماماً — لا _jline_base
هنا إطلاقاً، الشراء لا COGS له).
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
    JournalEntry,
)
import app.services.posting as posting_mod
from app.services.posting import post_purchase_invoice, PostingError
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
    ap_default = Account(code="2101", name_ar="موردون", account_type=AccountType.LIABILITY)
    tax_acc = Account(code="2310", name_ar="ضريبة مشتريات", account_type=AccountType.LIABILITY)
    inv_a = Account(code="1210", name_ar="مخزون أ", account_type=AccountType.ASSET)
    inv_b = Account(code="1220", name_ar="مخزون ب", account_type=AccountType.ASSET)
    cogs_a = Account(code="5110", name_ar="تكلفة أ", account_type=AccountType.EXPENSE)
    cogs_b = Account(code="5120", name_ar="تكلفة ب", account_type=AccountType.EXPENSE)
    s.add_all([cash, ap_default, tax_acc, inv_a, inv_b, cogs_a, cogs_b])
    s.commit()
    wh1 = Warehouse(name_ar="الرئيسي")
    wh2 = Warehouse(name_ar="الفرعي")
    s.add_all([wh1, wh2]); s.commit()
    s.add_all([
        Setting(key="default_cash_account_id", value=str(cash.id)),
        Setting(key="default_purchases_tax_account_id", value=str(tax_acc.id)),
        Setting(key="base_currency", value=base_currency),
        Setting(key="ap_parent_account_id", value=str(ap_default.id)),
    ])
    s.commit()
    item_a = Item(sku="A", name_ar="مادة أ", inventory_account_id=inv_a.id, cogs_account_id=cogs_a.id,
                  cost_method=CostMethod.AVERAGE)
    item_b = Item(sku="B", name_ar="مادة ب", inventory_account_id=inv_b.id, cogs_account_id=cogs_b.id,
                  cost_method=CostMethod.AVERAGE)
    s.add_all([item_a, item_b]); s.commit()
    return dict(s=s, cash=cash, ap_default=ap_default, tax_acc=tax_acc, inv_a=inv_a, inv_b=inv_b,
                cogs_a=cogs_a, cogs_b=cogs_b, wh1=wh1, wh2=wh2, item_a=item_a, item_b=item_b,
                today=datetime.date.today())


def mk_invoice(env, lines, is_cash, currency="SYP", rate="1", warehouse=None):
    inv = Invoice(
        invoice_no=f"PI-{id(lines)}", kind=InvoiceKind.PURCHASE, party_name="مورد تجريبي",
        invoice_date=env["today"], currency_code=currency, exchange_rate=D_(rate),
        status=InvoiceStatus.DRAFT, warehouse_id=(warehouse or env["wh1"]).id,
    )
    inv.lines = lines
    env["s"].add(inv); env["s"].commit()
    return inv


print("== 1) شراء نقدي ==")
env = fresh_env()
inv1 = mk_invoice(env, [InvoiceLine(item_id=env["item_a"].id, quantity=10, unit_price=D_("20"))], is_cash=True)
entry1 = post_purchase_invoice(env["s"], inv1, is_cash=True)
env["s"].commit()
lines1 = {l.account_id: (l.debit, l.credit) for l in entry1.lines}
check("سطر Cash دائناً بإجمالي الشراء", lines1.get(env["cash"].id) == (D_("0"), D_("200")), str(lines1))
check("سطر Inventory مديناً بنفس المبلغ", lines1.get(env["inv_a"].id) == (D_("200"), D_("0")))
check("لا COGS إطلاقاً بالشراء", env["cogs_a"].id not in lines1)

print("\n== 2) شراء آجل (AP) ==")
env2 = fresh_env()
inv2 = mk_invoice(env2, [InvoiceLine(item_id=env2["item_a"].id, quantity=10, unit_price=D_("20"))], is_cash=False)
entry2 = post_purchase_invoice(env2["s"], inv2, is_cash=False)
env2["s"].commit()
check("لا سطر بحساب الصندوق العام", env2["cash"].id not in {l.account_id for l in entry2.lines})

print("\n== 3) tax = 0 ==")
check("لا سطر ضريبة بالقيد الأول", env["tax_acc"].id not in lines1)

print("\n== 4) tax > 0 ==")
env4 = fresh_env()
line_tax = InvoiceLine(item_id=env4["item_a"].id, quantity=10, unit_price=D_("20"), tax_rate=D_("5"))
inv4 = mk_invoice(env4, [line_tax], is_cash=True)
entry4 = post_purchase_invoice(env4["s"], inv4, is_cash=True)
env4["s"].commit()
lines4 = {l.account_id: (l.debit, l.credit) for l in entry4.lines}
check("سطر ضريبة 10 (5% من 200) مديناً", lines4.get(env4["tax_acc"].id) == (D_("10"), D_("0")), str(lines4))
check("سطر Cash دائناً بالإجمالي شاملاً الضريبة (210)", lines4.get(env4["cash"].id) == (D_("0"), D_("210")))

print("\n== 5) حسابات/مواد مخزون متعددة ==")
env5 = fresh_env()
inv5 = mk_invoice(env5, [
    InvoiceLine(item_id=env5["item_a"].id, quantity=10, unit_price=D_("20")),
    InvoiceLine(item_id=env5["item_b"].id, quantity=5, unit_price=D_("30")),
], is_cash=True)
entry5 = post_purchase_invoice(env5["s"], inv5, is_cash=True)
env5["s"].commit()
lines5 = {l.account_id: (l.debit, l.credit) for l in entry5.lines}
check("مخزون A = 200 مدين", lines5.get(env5["inv_a"].id) == (D_("200"), D_("0")))
check("مخزون B = 150 مدين", lines5.get(env5["inv_b"].id) == (D_("150"), D_("0")))

print("\n== 6) خصم (discount_amount) — net_after_all_discounts صحيح بالقيد وبـunit_cost معاً ==")
env6 = fresh_env()
line_disc = InvoiceLine(item_id=env6["item_a"].id, quantity=10, unit_price=D_("20"), discount_amount=D_("20"))
inv6 = mk_invoice(env6, [line_disc], is_cash=True)
entry6 = post_purchase_invoice(env6["s"], inv6, is_cash=True)
env6["s"].commit()
lines6 = {l.account_id: (l.debit, l.credit) for l in entry6.lines}
check("القيد المحاسبي يعكس الصافي بعد الخصم (200-20=180)", lines6.get(env6["inv_a"].id) == (D_("180"), D_("0")), str(lines6))
mv6 = env6["s"].query(InventoryMovement).filter_by(source_type="purchase_invoice", source_id=inv6.id).one()
check("unit_cost يعكس نفس الصافي بعد الخصم مقسوماً على الكمية (180/10=18)", mv6.unit_cost == D_("18"), str(mv6.unit_cost))

print("\n== 7) عملة أجنبية — الإثبات الرقمي الصريح المطلوب: لا تحويل مزدوج ==")
env7 = fresh_env(base_currency="SYP")
inv7 = mk_invoice(env7, [InvoiceLine(item_id=env7["item_a"].id, quantity=10, unit_price=D_("20"))],
                   is_cash=True, currency="USD", rate="15000")
entry7 = post_purchase_invoice(env7["s"], inv7, is_cash=True)
env7["s"].commit()
inv_line7 = next(l for l in entry7.lines if l.account_id == env7["inv_a"].id)
check("سطر القيد المحاسبي لحساب المخزون: raw=200 (USD)، base=200×15000=3,000,000 — نفس معاملة Cash/AP تماماً",
      inv_line7.debit == D_("200") and inv_line7.debit_base == D_("3000000"),
      f"debit={inv_line7.debit}, debit_base={inv_line7.debit_base}")
mv7 = env7["s"].query(InventoryMovement).filter_by(source_type="purchase_invoice", source_id=inv7.id).one()
check("InventoryMovement.unit_cost = (200×15000)/10 = 300,000 بالعملة الأساسية — مطابق لسطر القيد وليس مضاعَفاً",
      mv7.unit_cost == D_("300000"), f"unit_cost={mv7.unit_cost}")
check("لا تحويل مزدوج: unit_cost × quantity == JournalLine.debit_base بالضبط",
      money_check := (mv7.unit_cost * 10 == inv_line7.debit_base), f"{mv7.unit_cost}×10 vs {inv_line7.debit_base}")

print("\n== 8) InventoryMovement IN + عدة مستودعات ==")
env8 = fresh_env()
inv8a = mk_invoice(env8, [InvoiceLine(item_id=env8["item_a"].id, quantity=10, unit_price=D_("20"))],
                    is_cash=True, warehouse=env8["wh1"])
post_purchase_invoice(env8["s"], inv8a, is_cash=True)
env8["s"].commit()
inv8b = mk_invoice(env8, [InvoiceLine(item_id=env8["item_a"].id, quantity=5, unit_price=D_("25"))],
                    is_cash=True, warehouse=env8["wh2"])
post_purchase_invoice(env8["s"], inv8b, is_cash=True)
env8["s"].commit()
mv_wh1 = env8["s"].query(InventoryMovement).filter_by(warehouse_id=env8["wh1"].id, direction=MovementDirection.IN).all()
mv_wh2 = env8["s"].query(InventoryMovement).filter_by(warehouse_id=env8["wh2"].id, direction=MovementDirection.IN).all()
check("حركة IN بالمستودع الأول", len(mv_wh1) == 1 and mv_wh1[0].unit_cost == D_("20"))
check("حركة IN منفصلة بالمستودع الثاني بتكلفة مختلفة", len(mv_wh2) == 1 and mv_wh2[0].unit_cost == D_("25"))

print("\n== 9) توازن + انتقال حالة + ربط مصدر ==")
check("كل القيود متوازنة", all(e.is_balanced() for e in [entry1, entry2, entry4, entry5, entry6, entry7]))
check("invoice.status = POSTED", inv1.status == InvoiceStatus.POSTED)
check("invoice.journal_entry_id = entry.id", inv1.journal_entry_id == entry1.id)
check("source_type/source_id صحيحان", entry1.source_type == "purchase_invoice" and entry1.source_id == inv1.id)

print("\n== 10) رفض الترحيل المكرر ==")
try:
    post_purchase_invoice(env["s"], inv1, is_cash=True)
    check("رفض الترحيل المكرر", False)
except PostingError:
    check("رفض الترحيل المكرر", True)

print("\n== 11) namespace الترقيم JE-PUR ==")
check("ref_no من JE-PUR عبر Sequence (6 أرقام)", entry1.ref_no.startswith("JE-PUR-") and len(entry1.ref_no.split("-")[-1]) == 6)

# ============================================================
# حقن الفشل — Atomicity
# ============================================================
print("\n== 12) فشل Validation قبل أي mutation ==")
env12 = fresh_env()
bad_invoice = mk_invoice(env12, [], is_cash=True)
try:
    post_purchase_invoice(env12["s"], bad_invoice, is_cash=True)
    check("رفضت الفاتورة الفارغة", False)
except (PostingError, InvoiceValidationError):
    check("رفضت الفاتورة الفارغة", True)
check("لا JournalEntry", env12["s"].query(JournalEntry).count() == 0)
check("لا InventoryMovement", env12["s"].query(InventoryMovement).count() == 0)
check("الفاتورة بقيت DRAFT", bad_invoice.status == InvoiceStatus.DRAFT)

print("\n== 13) عطل داخل post_immediate() نفسها ==")
env13 = fresh_env()
inv13 = mk_invoice(env13, [InvoiceLine(item_id=env13["item_a"].id, quantity=10, unit_price=D_("20"))], is_cash=True)
orig_pi = posting_mod.post_immediate
posting_mod.post_immediate = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("محاكاة عطل"))
try:
    post_purchase_invoice(env13["s"], inv13, is_cash=True)
    check("توقّع فشلاً", False)
except RuntimeError:
    check("العطل حدث فعلياً", True)
finally:
    posting_mod.post_immediate = orig_pi
env13["s"].expire_all()
check("لا JournalEntry يتيم", env13["s"].query(JournalEntry).count() == 0)
check("لا InventoryMovement يتيمة", env13["s"].query(InventoryMovement).count() == 0)
fresh13 = env13["s"].get(Invoice, inv13.id)
check("الفاتورة بقيت DRAFT، journal_entry_id فارغ",
      fresh13.status == InvoiceStatus.DRAFT and fresh13.journal_entry_id is None)

print("\n== 14) عطل بعد نجاح post_immediate، أثناء إضافة حركات المخزون/تحديث الفاتورة ==")
env14 = fresh_env()
inv14 = mk_invoice(env14, [InvoiceLine(item_id=env14["item_a"].id, quantity=10, unit_price=D_("20"))], is_cash=True)
orig_flush = env14["s"].flush
call_count = {"n": 0}
def boom_second_flush(*a, **k):
    call_count["n"] += 1
    if call_count["n"] >= 2:
        raise RuntimeError("محاكاة عطل بعد نجاح post_immediate")
    return orig_flush(*a, **k)
env14["s"].flush = boom_second_flush
try:
    post_purchase_invoice(env14["s"], inv14, is_cash=True)
    check("توقّع فشلاً", False)
except RuntimeError:
    check("العطل حدث فعلياً بعد نجاح post_immediate", True)
finally:
    env14["s"].flush = orig_flush
env14["s"].expire_all()
check("لا JournalEntry يتيم رغم نجاح post_immediate الداخلي", env14["s"].query(JournalEntry).count() == 0)
check("لا InventoryMovement يتيمة", env14["s"].query(InventoryMovement).count() == 0)
fresh14 = env14["s"].get(Invoice, inv14.id)
check("الفاتورة لم تتحوّل POSTED كذباً، journal_entry_id غير مُعبَّأ",
      fresh14.status == InvoiceStatus.DRAFT and fresh14.journal_entry_id is None)

print("\n" + "=" * 70)
print(f"النتيجة: {sum(1 for _, c in results if c)}/{len(results)} نجح")
print("=" * 70)
