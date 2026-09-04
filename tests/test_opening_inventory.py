"""
tests/test_opening_inventory.py
==================================
Acceptance Gate لـPhase 3B-2 (الأرصدة الافتتاحية للمخزون) — يغطي الـ12
مجموعة المُعتمَدة بـPHASE3B2_DESIGN_SPEC.md §4، زائد قرارات reverse
المُعتمَدة نهائياً بنفس الملف.
"""
import os, sys, datetime, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Setting, Account, AccountType, Warehouse, CostMethod,
    JournalEntry, JournalEntryStatus, OpeningInventoryEntry,
    Invoice, InvoiceLine, InvoiceKind, InvoiceStatus,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.opening_balances import (
    post_opening_inventory, reverse_opening_inventory,
    OpeningInventoryLineInput, OpeningBalanceError,
    OPENING_INVENTORY_SETTING_KEY, CLEARING_ACCOUNT_SETTING_KEY,
)
from app.services.posting import post_sales_invoice, post_purchase_invoice
from app.services.item_queries import get_item_stock_summary
from app.reports.trial_balance import get_trial_balance

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
    equity = Account(code="3199", name_ar="أرصدة افتتاحية - توازن", account_type=AccountType.EQUITY)
    session.add(equity); session.flush()
    session.add(Setting(key=CLEARING_ACCOUNT_SETTING_KEY, value=str(equity.id)))
    session.commit()
    return session, coa, equity


def make_item(session, coa, sku="ITM-1"):
    return create_item(session, sku=sku, name_ar=f"مادة {sku}", unit="قطعة",
                        inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                        cost_method=CostMethod.AVERAGE)


def make_sale(session, item, quantity, price, wh_id, no):
    inv = Invoice(invoice_no=no, kind=InvoiceKind.SALES, party_name="زبون", invoice_date=today,
                  currency_code="USD", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT, warehouse_id=wh_id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=quantity, unit_price=price)]
    session.add(inv); session.commit()
    entry = post_sales_invoice(session, inv, is_cash=True); session.commit()
    return inv, entry


def make_purchase(session, item, quantity, price, wh_id, no):
    inv = Invoice(invoice_no=no, kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
                  currency_code="USD", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT, warehouse_id=wh_id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=quantity, unit_price=price)]
    session.add(inv); session.commit()
    entry = post_purchase_invoice(session, inv, is_cash=True); session.commit()
    return inv, entry


# =====================================================================
# 1) Opening Qty × Unit Cost — القيد الأساسي
# =====================================================================
print("== 1) Opening Qty × Unit Cost ==")
s, coa, equity = fresh_env("USD")
item = make_item(s, coa)
wh_a = Warehouse(name_ar="مستودع A", is_active=True)
s.add(wh_a); s.commit()

entry = post_opening_inventory(
    s, [OpeningInventoryLineInput(item_id=item.id, warehouse_id=wh_a.id,
                                   quantity=D_("100"), unit_cost_foreign=D_("5"))],
    today,
)
s.commit()
check("القيد POSTED فعلياً", entry.status == JournalEntryStatus.POSTED)
check("source_type = opening_inventory", entry.source_type == "opening_inventory")
check("القيد متوازن", entry.is_balanced())
inv_line = next(l for l in entry.lines if l.account_id == coa["inventory"].id)
check("Dr Inventory = 500 بالضبط", D_(str(inv_line.debit_base)) == D_("500.00"))
clr_line = next(l for l in entry.lines if l.account_id == equity.id)
check("Cr Clearing = 500 بالضبط", D_(str(clr_line.credit_base)) == D_("500.00"))
summary = get_item_stock_summary(s, item.id, warehouse_id=wh_a.id)
check("unit_cost المخزَّن = 5", summary.average_cost == D_("5"))
check("سطر تفصيلي واحد بـopening_inventory_entries", s.query(OpeningInventoryEntry).count() == 1)

# =====================================================================
# 2) Warehouse isolation — نفس المادة بمستودعين، تكلفتان مختلفتان
# =====================================================================
print("\n== 2) Warehouse isolation ==")
s2, coa2, equity2 = fresh_env("USD")
item2 = make_item(s2, coa2)
wh_a2 = Warehouse(name_ar="مستودع A", is_active=True)
wh_b2 = Warehouse(name_ar="مستودع B", is_active=True)
s2.add_all([wh_a2, wh_b2]); s2.commit()

entry2 = post_opening_inventory(s2, [
    OpeningInventoryLineInput(item_id=item2.id, warehouse_id=wh_a2.id, quantity=D_("100"), unit_cost_foreign=D_("5")),
    OpeningInventoryLineInput(item_id=item2.id, warehouse_id=wh_b2.id, quantity=D_("100"), unit_cost_foreign=D_("20")),
], today)
s2.commit()
avg_a = get_item_stock_summary(s2, item2.id, warehouse_id=wh_a2.id).average_cost
avg_b = get_item_stock_summary(s2, item2.id, warehouse_id=wh_b2.id).average_cost
check("متوسط A = 5", avg_a == D_("5"))
check("متوسط B = 20", avg_b == D_("20"))

_, sale_a = make_sale(s2, item2, D_("10"), D_("999"), wh_a2.id, "SL-1")
_, sale_b = make_sale(s2, item2, D_("10"), D_("999"), wh_b2.id, "SL-2")
cogs_a = next(l for l in sale_a.lines if l.account_id == coa2["cogs"].id)
cogs_b = next(l for l in sale_b.lines if l.account_id == coa2["cogs"].id)
check("COGS A = 10×5 = 50 بالضبط", D_(str(cogs_a.debit_base)) == D_("50.00"))
check("COGS B = 10×20 = 200 بالضبط", D_(str(cogs_b.debit_base)) == D_("200.00"))

# =====================================================================
# 3-أ) بدون IN لاحقة: بيع يستخدم تكلفة الافتتاح مباشرة
# =====================================================================
print("\n== 3-أ) Historical cost بلا شراء لاحق ==")
s3a, coa3a, _ = fresh_env("USD")
item3a = make_item(s3a, coa3a)
wh3a = Warehouse(name_ar="مستودع رئيسي", is_active=True); s3a.add(wh3a); s3a.commit()
post_opening_inventory(s3a, [OpeningInventoryLineInput(item_id=item3a.id, warehouse_id=wh3a.id,
                                                          quantity=D_("100"), unit_cost_foreign=D_("5"))], today)
s3a.commit()
_, sale3a = make_sale(s3a, item3a, D_("20"), D_("999"), wh3a.id, "SL-3A")
cogs3a = next(l for l in sale3a.lines if l.account_id == coa3a["cogs"].id)
check("COGS = 20×5 = 100 بالضبط (بلا شراء بينهما)", D_(str(cogs3a.debit_base)) == D_("100.00"))

# =====================================================================
# 3-ب) مع IN لاحقة: اندماج بالمتوسط المرجَّح — ليس تجاهلاً ولا استبدالاً
# =====================================================================
print("\n== 3-ب) اندماج الافتتاح بالمتوسط المرجَّح بعد شراء لاحق ==")
s3b, coa3b, _ = fresh_env("USD")
item3b = make_item(s3b, coa3b)
wh3b = Warehouse(name_ar="مستودع رئيسي", is_active=True); s3b.add(wh3b); s3b.commit()
post_opening_inventory(s3b, [OpeningInventoryLineInput(item_id=item3b.id, warehouse_id=wh3b.id,
                                                          quantity=D_("100"), unit_cost_foreign=D_("5"))], today)
s3b.commit()
make_purchase(s3b, item3b, D_("100"), D_("10"), wh3b.id, "PU-3B")
avg3b = get_item_stock_summary(s3b, item3b.id, warehouse_id=wh3b.id).average_cost
check("المتوسط بعد الشراء = (100×5+100×10)/200 = 7.5 بالضبط", avg3b == D_("7.5"))
_, sale3b = make_sale(s3b, item3b, D_("20"), D_("999"), wh3b.id, "SL-3B")
cogs3b = next(l for l in sale3b.lines if l.account_id == coa3b["cogs"].id)
check("COGS = 20×7.5 = 150 بالضبط (Oracle مستقل: (100×5+100×10)/200×20)",
      D_(str(cogs3b.debit_base)) == D_("150.00"))

# =====================================================================
# 4) Multi-item — عدة مواد بنفس الدفعة
# =====================================================================
print("\n== 4) Multi-item batch ==")
s4, coa4, equity4 = fresh_env("USD")
item_x = make_item(s4, coa4, "X"); item_y = make_item(s4, coa4, "Y")
wh4 = Warehouse(name_ar="مستودع رئيسي", is_active=True); s4.add(wh4); s4.commit()
entry4 = post_opening_inventory(s4, [
    OpeningInventoryLineInput(item_id=item_x.id, warehouse_id=wh4.id, quantity=D_("10"), unit_cost_foreign=D_("100")),
    OpeningInventoryLineInput(item_id=item_y.id, warehouse_id=wh4.id, quantity=D_("20"), unit_cost_foreign=D_("50")),
], today)
s4.commit()
check("القيد متوازن رغم تعدد الأسطر (1000 + 1000 = 2000)", entry4.is_balanced())
check("قيمة الدفعة الإجمالية = 2000",
      sum(D_(str(l.debit_base)) for l in entry4.lines if l.account_id != equity4.id) == D_("2000.00"))

# =====================================================================
# 6) Zero/negative validation
# =====================================================================
print("\n== 6) Zero/negative validation ==")
s6, coa6, _ = fresh_env("USD")
item6 = make_item(s6, coa6)
wh6 = Warehouse(name_ar="مستودع رئيسي", is_active=True); s6.add(wh6); s6.commit()
try:
    post_opening_inventory(s6, [OpeningInventoryLineInput(item_id=item6.id, warehouse_id=wh6.id,
                                                            quantity=D_("0"), unit_cost_foreign=D_("5"))], today)
    check("quantity<=0 مرفوض", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("quantity=0 مرفوض", True)
s6.rollback()
try:
    post_opening_inventory(s6, [OpeningInventoryLineInput(item_id=item6.id, warehouse_id=wh6.id,
                                                            quantity=D_("-5"), unit_cost_foreign=D_("5"))], today)
    check("quantity سالبة مرفوضة", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("quantity سالبة مرفوضة", True)
s6.rollback()
try:
    post_opening_inventory(s6, [OpeningInventoryLineInput(item_id=item6.id, warehouse_id=wh6.id,
                                                            quantity=D_("5"), unit_cost_foreign=D_("-1"))], today)
    check("unit_cost سالب مرفوض", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("unit_cost سالب مرفوض", True)
s6.rollback()
# unit_cost == 0 مقبول صراحة — §12 بتعليمات Bilal الأخيرة: يجب إثبات
# صراحة (1) القيد يبقى متوازناً رغم سطر صفري ضمن دفعة بها سطر آخر غير
# صفري، (2) لا JournalLine للسطر الصفري إطلاقاً (لا سطر بقيمة 0 مخفية)،
# (3) قيمة المخزون/الكمية للسطر الصفري صحيحة بالكامل رغم غياب أي أثر
# بالقيد. هذا تحقق أعمق من فقرة سابقة كانت تكتفي بإثبات القبول فقط.
item6b = make_item(s6, coa6, "6B")
entry6 = post_opening_inventory(s6, [
    OpeningInventoryLineInput(item_id=item6.id, warehouse_id=wh6.id, quantity=D_("5"), unit_cost_foreign=D_("0")),
    OpeningInventoryLineInput(item_id=item6b.id, warehouse_id=wh6.id, quantity=D_("3"), unit_cost_foreign=D_("10")),
], today)
s6.commit()
check("unit_cost=0 مقبول صراحة (دفعة بسطرين)", entry6.status == JournalEntryStatus.POSTED)
nonzero_line = next(l for l in entry6.lines if l.account_id == item6b.inventory_account_id)
check("سطر المادة غير الصفرية = 3×10 = 30 بالضبط (بلا تأثر بالسطر الصفري)",
      D_(str(nonzero_line.debit_base)) == D_("30.00"))
check("القيد الكامل متوازن رغم وجود سطر صفري بالدفعة", entry6.is_balanced())
check("عدد أسطر القيد = 2 فقط (Dr مادة غير صفرية + Cr Clearing، لا 3 — أي لا سطر"
      " للمادة صفرية التكلفة إطلاقاً)", len(entry6.lines) == 2)
zero_summary = get_item_stock_summary(s6, item6.id, warehouse_id=wh6.id)
check("كمية المادة صفرية التكلفة صحيحة بالكامل (5) رغم غياب أثر بالقيد",
      zero_summary.quantity == D_("5"))
check("متوسط تكلفة المادة صفرية التكلفة = 0 بالضبط (لا قيمة مخفية)",
      zero_summary.average_cost == D_("0"))
zero_detail = s6.query(OpeningInventoryEntry).filter_by(item_id=item6.id).first()
check("opening_inventory_entries يحتفظ بالسطر الصفري كسجل تفصيلي رغم غياب أثر GL",
      zero_detail is not None and zero_detail.unit_cost_base == D_("0"))

# =====================================================================
# 7) Inactive/missing item & warehouse + duplicate داخل الدفعة
# =====================================================================
print("\n== 7) Inactive/missing item/warehouse + تكرار بالدفعة ==")
s7, coa7, _ = fresh_env("USD")
item7 = make_item(s7, coa7)
item7_inactive = make_item(s7, coa7, "INACTIVE"); item7_inactive.is_active = False
wh7 = Warehouse(name_ar="مستودع رئيسي", is_active=True)
wh7_inactive = Warehouse(name_ar="مستودع غير نشط", is_active=False)
s7.add_all([wh7, wh7_inactive]); s7.commit()

for label, bad_entries in [
    ("مادة غير نشطة", [OpeningInventoryLineInput(item_id=item7_inactive.id, warehouse_id=wh7.id,
                                                    quantity=D_("1"), unit_cost_foreign=D_("1"))]),
    ("مادة غير موجودة", [OpeningInventoryLineInput(item_id=999999, warehouse_id=wh7.id,
                                                      quantity=D_("1"), unit_cost_foreign=D_("1"))]),
    ("مستودع غير نشط", [OpeningInventoryLineInput(item_id=item7.id, warehouse_id=wh7_inactive.id,
                                                     quantity=D_("1"), unit_cost_foreign=D_("1"))]),
    ("مستودع غير موجود", [OpeningInventoryLineInput(item_id=item7.id, warehouse_id=999999,
                                                       quantity=D_("1"), unit_cost_foreign=D_("1"))]),
    ("تكرار (item,warehouse) بنفس الدفعة", [
        OpeningInventoryLineInput(item_id=item7.id, warehouse_id=wh7.id, quantity=D_("1"), unit_cost_foreign=D_("1")),
        OpeningInventoryLineInput(item_id=item7.id, warehouse_id=wh7.id, quantity=D_("2"), unit_cost_foreign=D_("2")),
    ]),
]:
    try:
        post_opening_inventory(s7, bad_entries, today)
        check(f"{label}: مرفوض", False, "لم يُرفَض!")
    except OpeningBalanceError:
        check(f"{label}: مرفوض", True)
    s7.rollback()

# نفس المادة بمستودعين مختلفين بنفس الدفعة — مسموح
ok_entry7 = post_opening_inventory(s7, [
    OpeningInventoryLineInput(item_id=item7.id, warehouse_id=wh7.id, quantity=D_("1"), unit_cost_foreign=D_("1")),
], today)
s7.commit()
check("دفعة صحيحة تنجح بعد كل الرفض أعلاه (لا أثر جانبي)", ok_entry7.status == JournalEntryStatus.POSTED)

# =====================================================================
# 8) Idempotency — محاولة ثانية للدفعة كاملة تُرفَض، الكمية لا تتضاعف
# =====================================================================
print("\n== 8) Idempotency: دفعة ثانية مرفوضة، لا تضاعف ==")
s8, coa8, _ = fresh_env("USD")
item8 = make_item(s8, coa8)
item8b = make_item(s8, coa8, "8B")
wh8 = Warehouse(name_ar="مستودع رئيسي", is_active=True); s8.add(wh8); s8.commit()
post_opening_inventory(s8, [OpeningInventoryLineInput(item_id=item8.id, warehouse_id=wh8.id,
                                                        quantity=D_("100"), unit_cost_foreign=D_("5"))], today)
s8.commit()
try:
    # حتى لو مادة جديدة كلياً لم تُذكَر بالمحاولة الأولى
    post_opening_inventory(s8, [OpeningInventoryLineInput(item_id=item8b.id, warehouse_id=wh8.id,
                                                            quantity=D_("1"), unit_cost_foreign=D_("1"))], today)
    check("محاولة ثانية للدفعة مرفوضة", False, "لم تُرفَض!")
except OpeningBalanceError:
    check("محاولة ثانية للدفعة مرفوضة (حتى لمادة جديدة كلياً)", True)
s8.rollback()
qty_after = get_item_stock_summary(s8, item8.id, warehouse_id=wh8.id).quantity
check("الكمية بقيت 100 ولم تتضاعف لـ200", qty_after == D_("100"))

# =====================================================================
# 9) Rollback ذرّي — فشل سطر لاحق بمنتصف دفعة متعددة الأسطر
# =====================================================================
print("\n== 9) Rollback ذرّي عند فشل منتصف الدفعة ==")
s9, coa9, _ = fresh_env("USD")
item9a = make_item(s9, coa9, "9A")
item9b = make_item(s9, coa9, "9B")
wh9 = Warehouse(name_ar="مستودع رئيسي", is_active=True); s9.add(wh9); s9.commit()
try:
    post_opening_inventory(s9, [
        OpeningInventoryLineInput(item_id=item9a.id, warehouse_id=wh9.id, quantity=D_("10"), unit_cost_foreign=D_("1")),
        OpeningInventoryLineInput(item_id=999999, warehouse_id=wh9.id, quantity=D_("10"), unit_cost_foreign=D_("1")),
        OpeningInventoryLineInput(item_id=item9b.id, warehouse_id=wh9.id, quantity=D_("10"), unit_cost_foreign=D_("1")),
    ], today)
    check("الدفعة رُفضت لوجود سطر غير صالح", False, "لم تُرفَض!")
except OpeningBalanceError:
    check("الدفعة رُفضت (سطر 2: مادة غير موجودة)", True)
s9.rollback()
check("صفر InventoryMovement بعد rollback", s9.query(get_item_stock_summary.__globals__["InventoryMovement"]).count() == 0)
check("صفر JournalEntry بعد rollback", s9.query(JournalEntry).count() == 0)
check("صفر OpeningInventoryEntry بعد rollback", s9.query(OpeningInventoryEntry).count() == 0)
check("Setting القفل غير موجود (لم يُقفَل خطأً)", s9.get(Setting, OPENING_INVENTORY_SETTING_KEY) is None)
# إعادة المحاولة بدفعة صحيحة تنجح نظيفة
retry9 = post_opening_inventory(s9, [
    OpeningInventoryLineInput(item_id=item9a.id, warehouse_id=wh9.id, quantity=D_("10"), unit_cost_foreign=D_("1")),
    OpeningInventoryLineInput(item_id=item9b.id, warehouse_id=wh9.id, quantity=D_("10"), unit_cost_foreign=D_("1")),
], today)
s9.commit()
check("إعادة المحاولة الصحيحة نجحت بعد rollback", retry9.status == JournalEntryStatus.POSTED)

# =====================================================================
# 10) Reopen database — إغلاق فعلي وإعادة فتح باتصال جديد تماماً
# =====================================================================
print("\n== 10) Reopen database ==")
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
db_path = tmp.name
try:
    engine10 = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine10)
    s10 = sessionmaker(bind=engine10)()
    coa10 = create_default_chart_of_accounts(s10)
    s10.add(Setting(key="base_currency", value="USD"))
    equity10 = Account(code="3199", name_ar="توازن افتتاحي", account_type=AccountType.EQUITY)
    s10.add(equity10); s10.flush()
    s10.add(Setting(key=CLEARING_ACCOUNT_SETTING_KEY, value=str(equity10.id)))
    item10 = make_item(s10, coa10)
    wh10 = Warehouse(name_ar="مستودع رئيسي", is_active=True); s10.add(wh10); s10.commit()
    entry10 = post_opening_inventory(s10, [OpeningInventoryLineInput(
        item_id=item10.id, warehouse_id=wh10.id, quantity=D_("40"), unit_cost_foreign=D_("3"))], today)
    entry10_id = entry10.id
    item10_id, wh10_id = item10.id, wh10.id
    s10.commit()
    s10.close(); engine10.dispose()

    engine10b = create_engine(f"sqlite:///{db_path}")
    s10b = sessionmaker(bind=engine10b)()
    reopened_entry = s10b.get(JournalEntry, entry10_id)
    check("القيد موجود بعد إعادة الفتح", reopened_entry is not None and reopened_entry.status == JournalEntryStatus.POSTED)
    tb10 = get_trial_balance(s10b)
    check("ميزان المراجعة متوازن بعد إعادة الفتح", tb10.is_balanced)
    check("get_item_stock_summary يعطي نفس النتيجة بعد إعادة الفتح",
          get_item_stock_summary(s10b, item10_id, warehouse_id=wh10_id).quantity == D_("40"))
    s10b.close(); engine10b.dispose()
finally:
    os.unlink(db_path)

# =====================================================================
# 11) GL ↔ Inventory reconciliation — معزول + أثر القيد نفسه
# =====================================================================
print("\n== 11) GL ↔ Inventory reconciliation ==")
s11, coa11, _ = fresh_env("USD")
item11 = make_item(s11, coa11)
wh11 = Warehouse(name_ar="مستودع رئيسي", is_active=True); s11.add(wh11); s11.commit()
entry11 = post_opening_inventory(s11, [OpeningInventoryLineInput(
    item_id=item11.id, warehouse_id=wh11.id, quantity=D_("100"), unit_cost_foreign=D_("5"))], today)
s11.commit()
# 11-أ: حالة معزولة — لا حركات أخرى بعد
tb11 = get_trial_balance(s11)
inv_row = next(r for r in tb11.rows if r.account.id == coa11["inventory"].id)
check("معزول: رصيد حساب المخزون بميزان المراجعة = 500 بالضبط",
      inv_row.total_debit - inv_row.total_credit == D_("500.00"))
# 11-ب: أثر القيد نفسه — صحيح دائماً بصرف النظر عن حركات لاحقة
make_purchase(s11, item11, D_("50"), D_("9"), wh11.id, "PU-11")
inv_line11 = next(l for l in entry11.lines if l.account_id == coa11["inventory"].id)
check("أثر القيد نفسه لا يتأثر بحركات لاحقة: debit_base = 100×5 = 500",
      D_(str(inv_line11.debit_base)) == D_("500.00"))

# =====================================================================
# 12) Sale after opening — دورة كاملة عبر الخدمة مباشرة
# =====================================================================
print("\n== 12) Sale after opening ==")
s12, coa12, _ = fresh_env("USD")
item12 = make_item(s12, coa12)
wh12 = Warehouse(name_ar="مستودع رئيسي", is_active=True); s12.add(wh12); s12.commit()
post_opening_inventory(s12, [OpeningInventoryLineInput(
    item_id=item12.id, warehouse_id=wh12.id, quantity=D_("100"), unit_cost_foreign=D_("5"))], today)
s12.commit()
make_sale(s12, item12, D_("20"), D_("999"), wh12.id, "SL-12")
remaining = get_item_stock_summary(s12, item12.id, warehouse_id=wh12.id).quantity
check("الكمية المتبقية = 100-20 = 80 متاحة للبيع التالي", remaining == D_("80"))

# =====================================================================
# 13) Reverse — مسموح، مرفوض بعد بيع تالٍ، ومعزول لكل مستودع
# =====================================================================
print("\n== 13) Reverse opening inventory ==")
s13, coa13, _ = fresh_env("USD")
item13 = make_item(s13, coa13)
wh13a = Warehouse(name_ar="مستودع A", is_active=True)
wh13b = Warehouse(name_ar="مستودع B", is_active=True)
s13.add_all([wh13a, wh13b]); s13.commit()
entry13 = post_opening_inventory(s13, [
    OpeningInventoryLineInput(item_id=item13.id, warehouse_id=wh13a.id, quantity=D_("50"), unit_cost_foreign=D_("2")),
    OpeningInventoryLineInput(item_id=item13.id, warehouse_id=wh13b.id, quantity=D_("50"), unit_cost_foreign=D_("4")),
], today)
s13.commit()

# 13-أ: عكس مباشر بلا أي بيع لاحق — مسموح
s13a2, coa13a2, _ = fresh_env("USD")
item13a2 = make_item(s13a2, coa13a2)
wh13a2 = Warehouse(name_ar="مستودع رئيسي", is_active=True); s13a2.add(wh13a2); s13a2.commit()
entry13a2 = post_opening_inventory(s13a2, [OpeningInventoryLineInput(
    item_id=item13a2.id, warehouse_id=wh13a2.id, quantity=D_("10"), unit_cost_foreign=D_("1"))], today)
s13a2.commit()
reversal13a2 = reverse_opening_inventory(s13a2, entry13a2, today + datetime.timedelta(days=1))
s13a2.commit()
check("عكس بلا بيع لاحق: نجح", reversal13a2.status == JournalEntryStatus.POSTED)
check("عكس بلا بيع لاحق: الكمية = 0 بعد العكس",
      get_item_stock_summary(s13a2, item13a2.id, warehouse_id=wh13a2.id).quantity == D_("0"))
check("Setting القفل أُزيل بعد العكس (يسمح بإعادة الإدخال)",
      s13a2.get(Setting, OPENING_INVENTORY_SETTING_KEY) is None)
# إعادة إدخال دفعة صحيحة بعد العكس تنجح
repost13a2 = post_opening_inventory(s13a2, [OpeningInventoryLineInput(
    item_id=item13a2.id, warehouse_id=wh13a2.id, quantity=D_("15"), unit_cost_foreign=D_("2"))], today)
s13a2.commit()
check("إعادة الإدخال بعد العكس نجحت", repost13a2.status == JournalEntryStatus.POSTED)

# 13-ب: بيع لاحق بمستودع A يمنع عكس الدفعة كاملةً (القيد وحدة واحدة —
# لا عكس جزئي لسطر دون آخر بنفس القيد؛ الفحص لكل (item,warehouse) بمعزل
# يقرر *هل* يُمنع العكس، لا أن نعكس B وحدها بينما تبقى A كما هي)
make_sale(s13, item13, D_("5"), D_("999"), wh13a.id, "SL-13")
try:
    reverse_opening_inventory(s13, entry13, today + datetime.timedelta(days=1))
    check("عكس القيد كاملاً بعد بيع من A: مرفوض", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("عكس القيد كاملاً مرفوض (سطر A اعتمد عليه بيع لاحق)", True)
s13.rollback()

# 13-د: حالة Bilal الصريحة الثالثة — دفعة افتتاح بمستودع A فقط (لا B
# إطلاقاً بنفس القيد)، وبيع لاحق بمستودع B منفصل تماماً (رصيده جاء من
# مصدر آخر لا علاقة له بهذا الافتتاح) — لا يمنع عكس افتتاح A إطلاقاً
s13d, coa13d, _ = fresh_env("USD")
item13d = make_item(s13d, coa13d)
wh13d_a = Warehouse(name_ar="مستودع A", is_active=True)
wh13d_b = Warehouse(name_ar="مستودع B", is_active=True)
s13d.add_all([wh13d_a, wh13d_b]); s13d.commit()
entry13d = post_opening_inventory(s13d, [OpeningInventoryLineInput(
    item_id=item13d.id, warehouse_id=wh13d_a.id, quantity=D_("30"), unit_cost_foreign=D_("2"))], today)
s13d.commit()
# رصيد مستودع B يأتي من شراء منفصل تماماً — لا علاقة له بافتتاح A
make_purchase(s13d, item13d, D_("20"), D_("3"), wh13d_b.id, "PU-13D")
make_sale(s13d, item13d, D_("5"), D_("999"), wh13d_b.id, "SL-13D")
reversal13d = reverse_opening_inventory(s13d, entry13d, today + datetime.timedelta(days=1))
s13d.commit()
check("Case 3 (Bilal): بيع من مستودع B منفصل لا يمنع عكس افتتاح مستودع A",
      reversal13d.status == JournalEntryStatus.POSTED)
check("افتتاح A عاد لصفر بعد العكس، وB لم يتأثر إطلاقاً (15 متبقية: 20-5)",
      get_item_stock_summary(s13d, item13d.id, warehouse_id=wh13d_a.id).quantity == D_("0")
      and get_item_stock_summary(s13d, item13d.id, warehouse_id=wh13d_b.id).quantity == D_("15"))

# 13-ج: قيد ليس opening_inventory (مثلاً opening_balance) يُرفَض صراحة
from app.services.opening_balances import post_opening_account_balances, OpeningBalanceLineInput
s13c, coa13c, equity13c = fresh_env("USD")
acct_entry13c = post_opening_account_balances(
    s13c, [OpeningBalanceLineInput(account_id=coa13c["cash"].id, debit_foreign=D_("100"))], today,
)
s13c.commit()
try:
    reverse_opening_inventory(s13c, acct_entry13c, today + datetime.timedelta(days=1))
    check("عكس قيد opening_balance عبر reverse_opening_inventory: مرفوض", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("عكس قيد من نوع خطأ (opening_balance) مرفوض صراحة", True)

# =====================================================================
print(f"\n{'='*70}\nالنتيجة: {sum(1 for _, ok in results if ok)}/{len(results)} نجح\n{'='*70}")
print("✅ كل اختبارات Phase 3B-2 نجحت")
