"""
اختبار End-to-End شامل — بالضبط السيناريو المتَّفق عليه:
دليل الحسابات → دليل المواد → أرصدة افتتاحية → شراء → شراء بعملة أجنبية →
بيع متعدد المواد → مرتجع بيع → مرتجع شراء → قبض/دفع → سند قيد يدوي متعدد
العملات → التقارير.

في كل مرحلة: تحقق من الحساب الصحيح، المبلغ، العملة الأصلية، سعر الصرف،
المعادل الأساسي، المخزون، متوسط التكلفة، COGS، رصيد الطرف، كشف الحساب.
النهاية: Trial Balance (كل حساب على حدة لا مجرد التوازن)، Income Statement،
Balance Sheet.

3 مواد بحسابات مخزون/مبيعات/COGS مختلفة تماماً (إحداها بلا حساب مبيعات خاص
لاختبار الاحتياطي)، وفاتورة بيع واحدة تحتوي الثلاث معاً.
عمليات بعملات: SYP (أساسية)، USD، EUR، سند قيد USD/EUR، سند قيد USD/SYP،
فرق صرف حقيقي بحساب أرباح/خسائر فروقات عملة.
"""
import os, sys, datetime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Account, AccountType, Item, CostMethod, Invoice, InvoiceLine,
    InvoiceKind, InvoiceStatus, JournalEntry, JournalEntryStatus, Setting,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.opening_balances import post_opening_inventory, OpeningInventoryLineInput
from app.services.item_queries import get_item_stock_summary
from app.services.posting import post_purchase_invoice, post_sales_invoice, get_default_warehouse
from app.services.returns import post_sales_return, post_purchase_return
from app.services.journal_edit import add_manual_line, post_manual_entry
from app.reports.ledger import get_account_statement
from app.reports.trial_balance import get_trial_balance
from app.reports.income_statement import get_income_statement
from app.reports.balance_sheet import get_balance_sheet
from app.services.money import money

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

D = Decimal
today = datetime.date.today()
d0 = today - datetime.timedelta(days=30)   # أرصدة افتتاحية
d1 = today - datetime.timedelta(days=25)   # شراء SYP
d2 = today - datetime.timedelta(days=20)   # شراء USD
d3 = today - datetime.timedelta(days=15)   # بيع متعدد المواد
d4 = today - datetime.timedelta(days=10)   # مرتجع بيع
d5 = today - datetime.timedelta(days=9)    # مرتجع شراء
d6 = today - datetime.timedelta(days=5)    # قبض/دفع + سند قيد يدوي

print("=" * 70)
print("المرحلة 1: دليل الحسابات")
print("=" * 70)
coa = create_default_chart_of_accounts(session)
cash, ar_parent, inventory_default, ap_parent = coa["cash"], coa["ar_parent"], coa["inventory"], coa["ap_parent"]
sales_default, cogs_default = coa["sales"], coa["cogs"]
sales_tax_acc, purchases_tax_acc = coa["sales_tax"], coa["purchases_tax"]
equity = session.query(Account).filter_by(code="3101").first()
bank = coa["bank"]

# حساب فروقات عملة إضافي (مفقود من الشجرة القياسية — نضيفه هنا لاختبار
# سيناريو الفرق الحقيقي المطلوب صراحة بهذه الجلسة)
fx_gain_loss = Account(code="6105", name_ar="أرباح وخسائر فروقات عملة",
                        account_type=AccountType.EXPENSE,
                        parent_id=session.query(Account).filter_by(code="6").first().id)
session.add(fx_gain_loss)
session.commit()
print(f"شجرة قياسية + حساب فروقات عملة إضافي ({fx_gain_loss.code}) — {session.query(Account).count()} حساباً")

print()
print("=" * 70)
print("المرحلة 2: دليل المواد — 3 مواد بحسابات مختلفة تماماً")
print("=" * 70)
inv_b = Account(code="1105", name_ar="مخزون مادة ب", account_type=AccountType.ASSET, parent_id=inventory_default.parent_id)
sales_a = Account(code="4111", name_ar="مبيعات مادة أ", account_type=AccountType.REVENUE, parent_id=coa["sales"].parent_id)
sales_b = Account(code="4112", name_ar="مبيعات مادة ب", account_type=AccountType.REVENUE, parent_id=coa["sales"].parent_id)
cogs_b = Account(code="5102", name_ar="تكلفة مبيعات مادة ب", account_type=AccountType.EXPENSE, parent_id=coa["cogs"].parent_id)
inv_c = Account(code="1106", name_ar="مخزون مادة ج", account_type=AccountType.ASSET, parent_id=inventory_default.parent_id)
cogs_c = Account(code="5103", name_ar="تكلفة مبيعات مادة ج", account_type=AccountType.EXPENSE, parent_id=coa["cogs"].parent_id)
session.add_all([inv_b, sales_a, sales_b, cogs_b, inv_c, cogs_c])
session.commit()

item_a = create_item(session, sku="A-001", name_ar="مادة أ", unit="قطعة",
                      inventory_account_id=inventory_default.id, sales_account_id=sales_a.id,
                      cogs_account_id=coa["cogs"].id, cost_method=CostMethod.AVERAGE)
item_b = create_item(session, sku="B-001", name_ar="مادة ب", unit="قطعة",
                      inventory_account_id=inv_b.id, sales_account_id=sales_b.id,
                      cogs_account_id=cogs_b.id, cost_method=CostMethod.AVERAGE)
item_c = create_item(session, sku="C-001", name_ar="مادة ج", unit="قطعة",
                      inventory_account_id=inv_c.id, sales_account_id=None,  # يختبر الاحتياطي default_sales
                      cogs_account_id=cogs_c.id, cost_method=CostMethod.AVERAGE)
session.commit()
print(f"3 مواد: {item_a.sku}(مخزون={inventory_default.code},مبيعات={sales_a.code},كلفة={coa['cogs'].code}), "
      f"{item_b.sku}(مخزون={inv_b.code},مبيعات={sales_b.code},كلفة={cogs_b.code}), "
      f"{item_c.sku}(مخزون={inv_c.code},مبيعات=احتياطي {sales_default.code},كلفة={cogs_c.code})")

print()
print("=" * 70)
print("المرحلة 3: أرصدة افتتاحية")
print("=" * 70)
session.add(Setting(key="base_currency", value="SYP"))
session.add(Setting(key="opening_balance_clearing_account_id", value=str(equity.id)))
session.commit()
default_wh = get_default_warehouse(session)
post_opening_inventory(session, [
    OpeningInventoryLineInput(item_id=item_a.id, warehouse_id=default_wh.id, quantity=D("100"), unit_cost_foreign=D("1000")),
    OpeningInventoryLineInput(item_id=item_b.id, warehouse_id=default_wh.id, quantity=D("50"), unit_cost_foreign=D("2000")),
    OpeningInventoryLineInput(item_id=item_c.id, warehouse_id=default_wh.id, quantity=D("200"), unit_cost_foreign=D("500")),
], d0)
session.commit()

opening_capital = D("500000000")  # رأس مال افتتاحي كافٍ لتغطية كل عمليات الاختبار
opening_entry = JournalEntry(entry_date=d0, ref_no="JV-OPEN-1", description="رصيد افتتاحي — رأس المال",
                              source_type="opening_balance", currency_code="SYP", exchange_rate=1,
                              status=JournalEntryStatus.DRAFT)
session.add(opening_entry)
session.flush()
add_manual_line(session, opening_entry, cash.id, debit=opening_capital, exchange_rate=1)
add_manual_line(session, opening_entry, equity.id, credit=opening_capital, exchange_rate=1)
post_manual_entry(session, opening_entry)
session.commit()

sum_a = get_item_stock_summary(session, item_a.id)
sum_b = get_item_stock_summary(session, item_b.id)
sum_c = get_item_stock_summary(session, item_c.id)
assert sum_a.quantity == 100 and sum_a.average_cost == D("1000")
assert sum_b.quantity == 50 and sum_b.average_cost == D("2000")
assert sum_c.quantity == 200 and sum_c.average_cost == D("500")
print(f"مادة أ: {sum_a.quantity} × {sum_a.average_cost} = {sum_a.inventory_value}")
print(f"مادة ب: {sum_b.quantity} × {sum_b.average_cost} = {sum_b.inventory_value}")
print(f"مادة ج: {sum_c.quantity} × {sum_c.average_cost} = {sum_c.inventory_value}")
cash_balance_after_opening = get_account_statement(session, cash.id, None, d0).closing_balance
assert cash_balance_after_opening == opening_capital, cash_balance_after_opening
print(f"رصيد الصندوق بعد الافتتاحية: {cash_balance_after_opening} (مطابق تماماً لرأس المال)")

print()
print("=" * 70)
print("المرحلة 4: شراء (SYP، عملة أساسية) — مادة أ بسعر مختلف، يختبر تحديث المتوسط المرجّح")
print("=" * 70)
pur1 = Invoice(invoice_no="PUR-1", kind=InvoiceKind.PURCHASE, party_name="مورد محلي",
               invoice_date=d1, currency_code="SYP", exchange_rate=D("1"), status=InvoiceStatus.DRAFT)
pur1.lines = [InvoiceLine(item_id=item_a.id, quantity=50, unit_price=D("1200"))]  # سعر أعلى من المتوسط الحالي
session.add(pur1)
session.commit()
entry1 = post_purchase_invoice(session, pur1, is_cash=True)
session.commit()

# تحقق من القيد: مدين مخزون مادة أ 60,000 | دائن الصندوق 60,000
pur1_lines = {l.account_id: (l.debit, l.credit) for l in entry1.lines}
assert pur1_lines.get(inventory_default.id) == (D("60000.00"), D("0.00")), pur1_lines.get(inventory_default.id)
assert pur1_lines.get(cash.id) == (D("0.00"), D("60000.00")), pur1_lines.get(cash.id)
assert entry1.currency_code == "SYP" and D(entry1.exchange_rate) == D("1")

# متوسط جديد لمادة أ = (100×1000 + 50×1200) / 150 = 160,000/150 = 1066.666...
sum_a2 = get_item_stock_summary(session, item_a.id)
expected_avg_a = (D("100") * D("1000") + D("50") * D("1200")) / D("150")
assert sum_a2.quantity == 150
assert abs(sum_a2.average_cost - expected_avg_a) < D("0.01"), (sum_a2.average_cost, expected_avg_a)
print(f"قيد الشراء متوازن، مخزون مادة أ محدَّث: {sum_a2.quantity} بمتوسط {sum_a2.average_cost} (متوقَّع {expected_avg_a:.4f})")

print()
print("=" * 70)
print("المرحلة 5: شراء بعملة أجنبية (USD) — مادة ب — يختبر تحويل التكلفة للعملة الأساسية بالمخزون")
print("=" * 70)
usd_rate_purchase = D("15000")
pur2 = Invoice(invoice_no="PUR-2", kind=InvoiceKind.PURCHASE, party_name="مورد أجنبي",
               invoice_date=d2, currency_code="USD", exchange_rate=usd_rate_purchase, status=InvoiceStatus.DRAFT)
pur2.lines = [InvoiceLine(item_id=item_b.id, quantity=20, unit_price=D("10"))]  # 10 USD/وحدة
session.add(pur2)
session.commit()
entry2 = post_purchase_invoice(session, pur2, is_cash=False)  # على الذمم (مورد أجنبي)
session.commit()

supplier_account = session.query(Account).filter_by(parent_id=ap_parent.id, name_ar="مورد أجنبي").first()
assert supplier_account is not None, "لم يُنشأ حساب فرعي للمورد الأجنبي تلقائياً"

pur2_lines = {l.account_id: (l.debit, l.credit, l.debit_base, l.credit_base) for l in entry2.lines}
# القيد بالعملة الأصلية (USD): 20 وحدة × 10 USD = 200 USD | بالعملة الأساسية: ×15,000 = 3,000,000 SYP
expected_base = D("20") * D("10") * usd_rate_purchase
expected_original = D("20") * D("10")
inv_b_line = pur2_lines.get(inv_b.id)
supplier_line = pur2_lines.get(supplier_account.id)
assert inv_b_line[0:2] == (D("200.00"), D("0.00")), f"مبلغ USD الخام خاطئ: {inv_b_line}"
assert inv_b_line[2:4] == (D("3000000.00"), D("0.00")), f"المعادل الأساسي خاطئ: {inv_b_line}"
assert supplier_line[0:2] == (D("0.00"), D("200.00")), f"مبلغ المورد USD خاطئ: {supplier_line}"
assert supplier_line[2:4] == (D("0.00"), D("3000000.00")), f"معادل المورد الأساسي خاطئ: {supplier_line}"
assert entry2.currency_code == "USD" and D(entry2.exchange_rate) == usd_rate_purchase
print(f"القيد المحاسبي صحيح بالكامل: 200 USD خام | {expected_base} SYP معادل أساسي (سعر الصرف {usd_rate_purchase})")

# *** التحقق الحاسم: هل تكلفة الوحدة بالمخزون بالعملة الأساسية أم بالدولار الخام؟ ***
sum_b_after_usd = get_item_stock_summary(session, item_b.id)
expected_avg_b_if_correct = (D("50") * D("2000") + D("20") * D("10") * usd_rate_purchase) / D("70")
expected_avg_b_if_bug = (D("50") * D("2000") + D("20") * D("10")) / D("70")  # لو لم يُحوَّل للأساسية
print(f"متوسط تكلفة مادة ب الفعلي: {sum_b_after_usd.average_cost}")
print(f"  → المتوقَّع الصحيح (محوَّل للعملة الأساسية): {expected_avg_b_if_correct:.4f}")
print(f"  → لو كان هناك خطأ (الدولار الخام بلا تحويل): {expected_avg_b_if_bug:.4f}")
assert abs(sum_b_after_usd.average_cost - expected_avg_b_if_correct) < D("0.01"), (
    f"❌ الخلل الذي كنا نتحقق منه موجود فعلاً: متوسط التكلفة يخلط دولاراً خاماً مع ليرة سورية "
    f"بلا تحويل! القيمة الفعلية {sum_b_after_usd.average_cost} تطابق سيناريو الخطأ "
    f"({expected_avg_b_if_bug:.4f}) لا السيناريو الصحيح ({expected_avg_b_if_correct:.4f})"
)
print("✅ تكلفة المخزون محوَّلة بشكل صحيح للعملة الأساسية")

print("ALL CHECKS UP TO STAGE 5 PASSED")

print()
print("=" * 70)
print("المرحلة 6: بيع متعدد المواد (الثلاث معاً بفاتورة واحدة، SYP)")
print("=" * 70)
sale1 = Invoice(invoice_no="SAL-1", kind=InvoiceKind.SALES, party_name="عميل جملة",
                 invoice_date=d3, currency_code="SYP", exchange_rate=D("1"), status=InvoiceStatus.DRAFT)
sale1.lines = [
    InvoiceLine(item_id=item_a.id, quantity=10, unit_price=D("2000")),   # كلفتها الحالية 1066.6667
    InvoiceLine(item_id=item_b.id, quantity=5, unit_price=D("60000")),   # كلفتها الحالية 44285.7143
    InvoiceLine(item_id=item_c.id, quantity=30, unit_price=D("900")),    # كلفتها الحالية 500
]
session.add(sale1)
session.commit()
entry6 = post_sales_invoice(session, sale1, is_cash=False)  # على الذمم
session.commit()

customer_account = session.query(Account).filter_by(parent_id=ar_parent.id, name_ar="عميل جملة").first()
assert customer_account is not None

sum_a3 = get_item_stock_summary(session, item_a.id)
sum_b3 = get_item_stock_summary(session, item_b.id)
sum_c3 = get_item_stock_summary(session, item_c.id)
assert sum_a3.quantity == 140, sum_a3.quantity   # 150 - 10
assert sum_b3.quantity == 65, sum_b3.quantity    # 70 - 5
assert sum_c3.quantity == 170, sum_c3.quantity   # 200 - 30
print(f"المخزون بعد البيع: أ={sum_a3.quantity}, ب={sum_b3.quantity}, ج={sum_c3.quantity}")

entry6_lines = {l.account_id: (l.debit, l.credit) for l in entry6.lines}
expected_cogs_a = money(D("10") * sum_a2.average_cost)  # المتوسط وقت البيع (قبل البيع) = sum_a2
expected_cogs_b = money(D("5") * sum_b_after_usd.average_cost)
expected_cogs_c = money(D("30") * D("500"))
assert entry6_lines.get(sales_a.id) == (D("0.00"), D("20000.00")), entry6_lines.get(sales_a.id)
assert entry6_lines.get(sales_b.id) == (D("0.00"), D("300000.00")), entry6_lines.get(sales_b.id)
assert entry6_lines.get(sales_default.id) == (D("0.00"), D("27000.00")), entry6_lines.get(sales_default.id)  # مادة ج بلا حساب خاص
assert entry6_lines.get(coa["cogs"].id) == (expected_cogs_a, D("0.00")), (entry6_lines.get(coa["cogs"].id), expected_cogs_a)
assert entry6_lines.get(cogs_b.id) == (expected_cogs_b, D("0.00")), (entry6_lines.get(cogs_b.id), expected_cogs_b)
assert entry6_lines.get(cogs_c.id) == (expected_cogs_c, D("0.00")), (entry6_lines.get(cogs_c.id), expected_cogs_c)
assert entry6_lines.get(inventory_default.id) == (D("0.00"), expected_cogs_a)
assert entry6_lines.get(inv_b.id) == (D("0.00"), expected_cogs_b)
assert entry6_lines.get(inv_c.id) == (D("0.00"), expected_cogs_c)
print("مبيعات مادة أ→حسابها الخاص، ب→حسابها الخاص، ج→الاحتياطي العام — كل واحدة بمبلغها الصحيح")
print(f"COGS: أ={expected_cogs_a} (حسابها الخاص {coa['cogs'].code}), "
      f"ب={expected_cogs_b} (حسابها الخاص {cogs_b.code}), ج={expected_cogs_c} (حسابها الخاص {cogs_c.code})")

customer_balance = get_account_statement(session, customer_account.id, None, d3).closing_balance
expected_customer = D("20000.00") + D("300000.00") + D("27000.00")
assert customer_balance == expected_customer, (customer_balance, expected_customer)
print(f"رصيد العميل الجملة: {customer_balance} (مطابق تماماً لإجمالي الفاتورة)")

print()
print("=" * 70)
print("المرحلة 7: مرتجع بيع (جزئي، مرتبط بفاتورة SAL-1) — مادة أ فقط، نصف الكمية")
print("=" * 70)
sret1 = Invoice(invoice_no="SRET-1", kind=InvoiceKind.SALES_RETURN, party_name="عميل جملة",
                 invoice_date=d4, currency_code="SYP", exchange_rate=D("1"), status=InvoiceStatus.DRAFT,
                 original_invoice_id=sale1.id)
sret1.lines = [InvoiceLine(item_id=item_a.id, quantity=5, unit_price=D("2000"))]
session.add(sret1)
session.commit()
entry7 = post_sales_return(session, sret1, is_cash=False)
session.commit()

sum_a4 = get_item_stock_summary(session, item_a.id)
assert sum_a4.quantity == 145, sum_a4.quantity  # 140 + 5 راجعة
# الكلفة يجب أن تُقرأ من حركة البيع الأصلية بالضبط (نفس متوسط وقت البيع sum_a2)، لا متوسط اليوم
entry7_lines = {l.account_id: (l.debit, l.credit) for l in entry7.lines}
expected_return_cost_a = money(D("5") * sum_a2.average_cost)
assert entry7_lines.get(inventory_default.id) == (expected_return_cost_a, D("0.00")), entry7_lines.get(inventory_default.id)
assert entry7_lines.get(coa["cogs"].id) == (D("0.00"), expected_return_cost_a), entry7_lines.get(coa["cogs"].id)
assert entry7_lines.get(sales_a.id) == (D("10000.00"), D("0.00")), entry7_lines.get(sales_a.id)
assert entry7_lines.get(customer_account.id) == (D("0.00"), D("10000.00")), entry7_lines.get(customer_account.id)
print(f"مرتجع بيع صحيح: تخفيض مبيعات مادة أ 10,000، إعادة كلفة {expected_return_cost_a} (كلفة وقت البيع الأصلي بالضبط)")

customer_balance_after_return = get_account_statement(session, customer_account.id, None, d4).closing_balance
assert customer_balance_after_return == expected_customer - D("10000.00")
print(f"رصيد العميل بعد المرتجع: {customer_balance_after_return}")

print()
print("=" * 70)
print("المرحلة 8: مرتجع شراء (جزئي، مرتبط بـPUR-2) — مادة ب، نصف الكمية")
print("=" * 70)
pret1 = Invoice(invoice_no="PRET-1", kind=InvoiceKind.PURCHASE_RETURN, party_name="مورد أجنبي",
                 invoice_date=d5, currency_code="USD", exchange_rate=usd_rate_purchase, status=InvoiceStatus.DRAFT,
                 original_invoice_id=pur2.id)
pret1.lines = [InvoiceLine(item_id=item_b.id, quantity=10, unit_price=D("10"))]
session.add(pret1)
session.commit()
entry8 = post_purchase_return(session, pret1, is_cash=False)
session.commit()

sum_b4 = get_item_stock_summary(session, item_b.id)
assert sum_b4.quantity == 55, sum_b4.quantity  # 65 - 10 راجعة للمورد

entry8_lines = {l.account_id: (l.debit, l.credit, l.debit_base, l.credit_base) for l in entry8.lines}
supplier_line8 = entry8_lines.get(supplier_account.id)
inv_b_line8 = entry8_lines.get(inv_b.id)
# مرتجع مرتبط بفاتورة USD أصلية → كلفة الوحدة تُقرأ من حركة الشراء الأصلية (بالعملة الأساسية بعد الإصلاح) = 44285.7143/وحدة تقريباً... 
# لكن الأصح: الكلفة المسجَّلة بحركة الشراء الأصلية لكل وحدة = net_in_base / q = 3,000,000/20 = 150,000 SYP/وحدة
purchase_unit_cost_b = D("3000000.00") / D("20")
expected_return_value_b = money(D("10") * purchase_unit_cost_b)
assert inv_b_line8[0:2] == (D("0.00"), expected_return_value_b), inv_b_line8
assert inv_b_line8[2:4] == (D("0.00"), expected_return_value_b), (inv_b_line8, expected_return_value_b)
# بعد إصلاح §30: سطر المورد لهذا الجزء (تكلفة تاريخية بحتة، بلا ضريبة هنا)
# مُقيَّم بالعملة الأساسية مباشرة عبر _jline_base — لا حقل "عملة أصلية" منفصل
# ذو معنى لمبلغ التكلفة نفسه (راجع WORKFLOW.md §30 لسبب هذا التصميم تحديداً)
assert supplier_line8[0:2] == (expected_return_value_b, D("0.00")), supplier_line8
assert supplier_line8[2:4] == (expected_return_value_b, D("0.00")), supplier_line8
print(f"مرتجع شراء صحيح: كلفة الوحدة تُقرأ من الحركة الأصلية للفاتورة USD ({purchase_unit_cost_b} SYP/وحدة بالأساسية)، "
      f"إجمالي {expected_return_value_b} SYP — مُقيَّم بالعملة الأساسية مباشرة بلا أي تحويل إضافي بسعر صرف المرتجع")

print("ALL CHECKS UP TO STAGE 8 PASSED")

print()
print("=" * 70)
print("المرحلة 9: قبض من العميل + دفع للمورد (سندات قبض/دفع = قيود يدوية بسيطة)")
print("=" * 70)
receipt_amount = D("200000")
receipt = JournalEntry(entry_date=d6, ref_no="JV-RCV-1", description="قبض من عميل جملة",
                        source_type="receipt", currency_code="SYP", exchange_rate=1,
                        status=JournalEntryStatus.DRAFT)
session.add(receipt)
session.flush()
add_manual_line(session, receipt, cash.id, debit=receipt_amount, exchange_rate=1)
add_manual_line(session, receipt, customer_account.id, credit=receipt_amount, exchange_rate=1)
post_manual_entry(session, receipt)
session.commit()

customer_balance_after_receipt = get_account_statement(session, customer_account.id, None, d6).closing_balance
assert customer_balance_after_receipt == expected_customer - D("10000.00") - receipt_amount
print(f"رصيد العميل بعد القبض: {customer_balance_after_receipt}")

payment_amount_usd = D("50")
payment_rate = D("15200")  # سعر صرف مختلف قليلاً عن سعر الشراء الأصلي (15000) — نفرق صرف لاحقاً بالقيد اليدوي
payment = JournalEntry(entry_date=d6, ref_no="JV-PAY-1", description="دفع جزئي للمورد الأجنبي",
                        source_type="payment", currency_code="USD", exchange_rate=payment_rate,
                        status=JournalEntryStatus.DRAFT)
session.add(payment)
session.flush()
add_manual_line(session, payment, supplier_account.id, debit=payment_amount_usd, exchange_rate=payment_rate)
add_manual_line(session, payment, cash.id, credit=payment_amount_usd, exchange_rate=payment_rate)
post_manual_entry(session, payment)
session.commit()

supplier_balance_after_payment = get_account_statement(session, supplier_account.id, None, d6).closing_balance
# رصيد المورد الأصلي: 3,000,000 (شراء) - 1,500,000 (مرتجع) = 1,500,000، ثم دفع 50 USD × 15200 = 760,000
expected_supplier_before_payment = D("3000000.00") - expected_return_value_b
supplier_balance_after_payment_expected = expected_supplier_before_payment - money(payment_amount_usd * payment_rate)
assert supplier_balance_after_payment == supplier_balance_after_payment_expected, (
    supplier_balance_after_payment, supplier_balance_after_payment_expected
)
print(f"رصيد المورد الأجنبي بعد الدفع الجزئي: {supplier_balance_after_payment} SYP")

print()
print("=" * 70)
print("المرحلة 10: سند قيد يدوي متعدد العملات (USD مقابل EUR، وUSD مقابل SYP) + فرق صرف حقيقي")
print("=" * 70)
# 10.1: تحويل USD مقابل EUR مباشرة (بلا مرور بالعملة الأساسية كوسيط منطقي —
# لكن كل سطر يُسجَّل بمعادله الأساسي المستقل تماماً كما صُمِّم بسند القيد،
# راجع WORKFLOW.md §20)
usd_to_eur = JournalEntry(entry_date=d6, ref_no="JV-FX-1", description="تحويل عملات USD مقابل EUR",
                           source_type="manual", currency_code="USD", exchange_rate=usd_rate_purchase,
                           status=JournalEntryStatus.DRAFT)
session.add(usd_to_eur)
session.flush()
eur_rate = D("16300")
# دائن USD 326 (اختيرت لتقسم تماماً على eur_rate فلا يظهر فرق تقريب عشري
# صناعي غير مقصود — فرق الصرف الحقيقي مُختبَر صراحة بخطوة منفصلة أدناه،
# فلا نريد خلطه بفرق تقريب حسابي بحت هنا)
usd_amount_fx1 = D("326")
add_manual_line(session, usd_to_eur, cash.id, credit=usd_amount_fx1, exchange_rate=usd_rate_purchase,
                 line_currency_code="USD", line_exchange_rate=usd_rate_purchase)
eur_amount = D("300")  # 300 × 16300 = 4,890,000 = 326 × 15000 بالضبط
assert money(usd_amount_fx1 * usd_rate_purchase) == money(eur_amount * eur_rate)
add_manual_line(session, usd_to_eur, bank.id, debit=eur_amount, exchange_rate=eur_rate,
                 line_currency_code="EUR", line_exchange_rate=eur_rate)
post_manual_entry(session, usd_to_eur)
session.commit()
uv_lines = {(l.line_currency_code, l.account_id): (l.debit, l.credit, l.debit_base, l.credit_base) for l in usd_to_eur.lines}
assert uv_lines[("USD", cash.id)][0:2] == (D("0.00"), D("326.00"))
assert uv_lines[("EUR", bank.id)][0:2] == (D("300.00"), D("0.00"))
assert usd_to_eur.is_balanced()
print(f"سند قيد USD مقابل EUR متوازن بالعملة الأساسية: دائن {usd_amount_fx1} USD (معادل {money(usd_amount_fx1*usd_rate_purchase)}), "
      f"مدين {eur_amount} EUR (معادل {money(eur_amount*eur_rate)})")

# 10.2: قيد USD مقابل SYP مباشر
usd_to_syp = JournalEntry(entry_date=d6, ref_no="JV-FX-2", description="سحب دولار مقابل ليرة سورية",
                           source_type="manual", currency_code="SYP", exchange_rate=1,
                           status=JournalEntryStatus.DRAFT)
session.add(usd_to_syp)
session.flush()
add_manual_line(session, usd_to_syp, cash.id, debit=D("100"), exchange_rate=usd_rate_purchase,
                 line_currency_code="USD", line_exchange_rate=usd_rate_purchase)
add_manual_line(session, usd_to_syp, bank.id, credit=money(D("100") * usd_rate_purchase), exchange_rate=1)
post_manual_entry(session, usd_to_syp)
session.commit()
assert usd_to_syp.is_balanced()
print(f"سند قيد USD مقابل SYP متوازن: مدين 100 USD (معادل {D('100')*usd_rate_purchase} SYP) دائن نفس القيمة SYP مباشرة")

# 10.3: فرق صرف حقيقي — سيناريو مستقل ونظيف: تسجيل التزام بالدولار بسعر
# صرف معيّن، ثم تسويته بالكامل لاحقاً بسعر صرف مختلف فعلياً — الفرق بين
# القيمتين الأساسيتين يُسجَّل بحساب أرباح/خسائر فروقات عملة بنفس القيد
# الذي يُسوّي الالتزام (المعالجة المحاسبية القياسية لفرق الصرف عند التسوية:
# ثلاثة أسطر متزامنة، لا تصحيح لاحق منفصل — راجع WORKFLOW.md §20.1 ومبادئ
# IAS 21 المُشار إليها هناك)
fx_liability = Account(code="2104", name_ar="التزام تجريبي بالدولار (لاختبار فرق الصرف)",
                        account_type=AccountType.LIABILITY,
                        parent_id=session.query(Account).filter_by(code="21").first().id)
session.add(fx_liability)
session.commit()

booking_rate = D("15000")
settlement_rate = D("15300")
fx_usd_amount = D("200")
booking_base = money(fx_usd_amount * booking_rate)       # 3,000,000 — قيمة الالتزام وقت التسجيل
settlement_base = money(fx_usd_amount * settlement_rate)  # 3,060,000 — قيمة السداد الفعلي
fx_loss = settlement_base - booking_base                  # 60,000 خسارة صرف حقيقية

book_liability = JournalEntry(entry_date=d6, ref_no="JV-FX-4A", description="تسجيل التزام تجريبي بالدولار",
                               source_type="manual", currency_code="USD", exchange_rate=booking_rate,
                               status=JournalEntryStatus.DRAFT)
session.add(book_liability)
session.flush()
add_manual_line(session, book_liability, bank.id, debit=fx_usd_amount, exchange_rate=booking_rate)
add_manual_line(session, book_liability, fx_liability.id, credit=fx_usd_amount, exchange_rate=booking_rate)
post_manual_entry(session, book_liability)
session.commit()

settle_liability = JournalEntry(entry_date=d6, ref_no="JV-FX-4B", description="تسوية الالتزام التجريبي بسعر صرف مختلف — فرق صرف حقيقي",
                                 source_type="manual", currency_code="SYP", exchange_rate=1,
                                 status=JournalEntryStatus.DRAFT)
session.add(settle_liability)
session.flush()
# مدين الالتزام بقيمته المسجَّلة الأصلية بالضبط (يُطفئه بالكامل)
add_manual_line(session, settle_liability, fx_liability.id, debit=booking_base, exchange_rate=1)
# مدين خسارة الصرف بالفرق
add_manual_line(session, settle_liability, fx_gain_loss.id, debit=fx_loss, exchange_rate=1)
# دائن البنك بقيمة السداد الفعلي الكاملة بسعر اليوم
add_manual_line(session, settle_liability, bank.id, credit=settlement_base, exchange_rate=1)
post_manual_entry(session, settle_liability)
session.commit()

fx_liability_balance = get_account_statement(session, fx_liability.id, None, d6).closing_balance
assert fx_liability_balance == 0, f"الالتزام يجب أن يُطفأ بالكامل: {fx_liability_balance}"
fx_gain_loss_balance = get_account_statement(session, fx_gain_loss.id, None, d6).closing_balance
assert fx_gain_loss_balance == fx_loss, (fx_gain_loss_balance, fx_loss)
print(f"التزام بالدولار سُجِّل بـ{booking_base} SYP (سعر {booking_rate})، سُوِّي بالكامل بـ{settlement_base} SYP "
      f"(سعر {settlement_rate}) — فرق صرف حقيقي {fx_loss} SYP، والالتزام مُطفأ تماماً (رصيد صفر)")

print("ALL CHECKS UP TO STAGE 10 PASSED")

print()
print("=" * 70)
print("المرحلة 11: التقارير — Trial Balance (كل حساب على حدة) + Income Statement + Balance Sheet")
print("=" * 70)

tb = get_trial_balance(session)
assert tb.is_balanced, f"ميزان المراجعة غير متوازن! الفرق = {tb.total_debit - tb.total_credit}"
tb_by_account = {r.account.id: (r.total_debit, r.total_credit) for r in tb.rows}
print(f"ميزان المراجعة متوازن: مدين {tb.total_debit} = دائن {tb.total_credit} — لكن هذا وحده غير كافٍ، نتحقق حساباً حساباً:")

# لا نكتفي بالتوازن الكلي — نتحقق من صافي كل حساب بمفرده مقابل ما نتوقعه فعلياً
def net(account_id):
    d, c = tb_by_account.get(account_id, (Decimal("0"), Decimal("0")))
    return d - c  # موجب = رصيد مدين صافٍ

expected_cash_net = (
    opening_capital
    - D("60000.00")            # شراء SYP
    + receipt_amount           # قبض من العميل
    - money(payment_amount_usd * payment_rate)  # دفع للمورد بسعر السداد الفعلي
    - money(usd_amount_fx1 * usd_rate_purchase)  # سند FX-1: دائن USD من الصندوق
    + D("1500000.00")          # سند FX-2: مدين USD 100 بالصندوق (معادله الأساسي)
)
assert net(cash.id) == expected_cash_net, (net(cash.id), expected_cash_net)
print(f"  الصندوق: {net(cash.id)} (متوقَّع {expected_cash_net}) ✅")

expected_customer_net = expected_customer - D("10000.00") - receipt_amount
assert net(customer_account.id) == expected_customer_net
print(f"  عميل الجملة: {net(customer_account.id)} (متوقَّع {expected_customer_net}) ✅")

expected_supplier_credit_balance = supplier_balance_after_payment  # المرتبط بالمورد فقط — لم يعد يتأثر بسيناريو FX المستقل
supplier_net = net(supplier_account.id)
assert -supplier_net == expected_supplier_credit_balance, (-supplier_net, expected_supplier_credit_balance)
print(f"  المورد الأجنبي (التزام، دائن): {-supplier_net} (متوقَّع {expected_supplier_credit_balance}) ✅")

expected_fx_net = fx_loss  # مصروف — رصيده الطبيعي مدين (من سيناريو تسوية الالتزام التجريبي فقط)
assert net(fx_gain_loss.id) == expected_fx_net
print(f"  فروقات العملة (مصروف): {net(fx_gain_loss.id)} (متوقَّع {expected_fx_net}) ✅")

expected_sales_a_net = -(D("20000.00") - D("10000.00"))  # إيراد، رصيده الطبيعي دائن
assert net(sales_a.id) == expected_sales_a_net
print(f"  مبيعات مادة أ (إيراد، دائن): {-net(sales_a.id)} (متوقَّع {-expected_sales_a_net}) ✅")

expected_bank_net = (
    money(eur_amount * eur_rate)   # سند FX-1: مدين EUR بمعادله الأساسي
    - D("1500000.00")              # سند FX-2: دائن SYP مباشر
    + booking_base                 # تسجيل الالتزام التجريبي: مدين بنك (استلام الدولار المُقترَض افتراضاً)
    - settlement_base              # تسوية الالتزام: دائن بنك بقيمة السداد الفعلي
)
assert net(bank.id) == expected_bank_net
print(f"  البنك: {net(bank.id)} (متوقَّع {expected_bank_net}) ✅")

print()
income = get_income_statement(session, datetime.date(1900, 1, 1), today)
expected_revenue = D("20000.00") - D("10000.00") + D("300000.00") + D("27000.00")
expected_cogs_total = expected_cogs_a - expected_return_cost_a + expected_cogs_b + expected_cogs_c
expected_expenses = fx_loss  # فقط فروقات العملة بهذا السيناريو
assert income.total_revenue == expected_revenue, (income.total_revenue, expected_revenue)
assert income.total_cogs == expected_cogs_total, (income.total_cogs, expected_cogs_total)
assert income.total_expenses == expected_expenses, (income.total_expenses, expected_expenses)
expected_gross_profit = expected_revenue - expected_cogs_total
expected_net_profit = expected_gross_profit - expected_expenses
assert income.gross_profit == expected_gross_profit
assert income.net_profit == expected_net_profit
print(f"قائمة الدخل: إيرادات={income.total_revenue}, تكلفة مبيعات={income.total_cogs}, "
      f"مصروفات={income.total_expenses}, ربح إجمالي={income.gross_profit}, صافي الربح={income.net_profit}")

print()
bs = get_balance_sheet(session, today)
assert bs.is_balanced, (
    f"الميزانية غير متوازنة: أصول={bs.total_assets}, "
    f"خصوم+حقوق={bs.total_liabilities + bs.total_equity_with_earnings}"
)
assert bs.unclosed_net_profit == income.net_profit, (bs.unclosed_net_profit, income.net_profit)
print(f"الميزانية العمومية متوازنة: أصول={bs.total_assets} = "
      f"خصوم({bs.total_liabilities}) + حقوق ملكية مسجَّلة({bs.total_equity_recorded}) + "
      f"صافي ربح غير مُقفل({bs.unclosed_net_profit}) = {bs.total_liabilities + bs.total_equity_with_earnings}")

print()
print("=" * 70)
print("✅ كل مراحل السيناريو الـEnd-to-End نجحت — 11 مرحلة، كل حساب تحقَّق بمفرده")
print("=" * 70)
print(f"عدد القيود المرحّلة الكلي: {session.query(JournalEntry).filter_by(status=JournalEntryStatus.POSTED).count()}")
print(f"عدد الفواتير المرحّلة: {session.query(Invoice).filter_by(status=InvoiceStatus.POSTED).count()}")



