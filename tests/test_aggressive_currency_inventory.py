"""
tests/test_aggressive_currency_inventory.py
===============================================
اختبارات عدوانية مستهدِفة (WORKFLOW.md §35) — ليست حالات عشوائية، بل
سيناريوهات صُمِّمت خصيصاً لاستهداف أنماط الأخطاء المكتشَفة سابقاً:
unit_cost الخاطئ، double conversion، تكلفة مرتجع غير تاريخية، فاتورة
متعددة المواد بحسابات مختلفة.

Oracle مستقل تماماً (class AverageCostOracle) — يعيد تطبيق خوارزمية
Weighted Average المرجّح **من الصفر** بمعادلات يدوية، دون استدعاء
app/services/item_queries.py أو posting.py أو أي كود إنتاجي، تماماً
كما اتُّفق عليه صراحة.

نطاق مُغطّى: مجموعات 1-4 و8-9 من قائمة صديق المبرمج بالكامل.
نطاق **غير مُغطّى عمداً**: مجموعة 5 (قبض/دفع بعملة مختلفة، فروقات صرف
تسوية) — لأن لا يوجد في المشروع حالياً أي وحدة receipts/payments أو
settlement أو fx_gain/fx_loss إطلاقاً (تحقّقتُ بالبحث بالكود، راجع
§35.6). هذه ليست ثغرة اختبار، بل ميزة غير موجودة بعد بالتطبيق نفسه —
لا يمكن اختبار شيء غير مبني.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, InventoryMovement,
    JournalLine, JournalEntry, JournalEntryStatus, CostMethod, MovementDirection,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item, ItemEditError
from app.services.invoice_edit import ensure_editable as invoice_ensure_editable, EditNotAllowedError
from app.services.posting import post_purchase_invoice, post_sales_invoice
from app.services.returns import post_sales_return, post_purchase_return
from app.services.journal_edit import (
    add_manual_line, post_manual_entry, reverse_manual_entry, ensure_editable as je_ensure_editable, JournalEditError,
)
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


# ======================================================================
# Oracle مستقل تماماً — لا يستدعي أي كود من app/services/
# ======================================================================
class AverageCostOracle:
    """
    يعيد بناء Weighted Average Perpetual من الصفر: كل IN يحدّث المتوسط
    وفق qty×cost المُضافة، كل OUT يسحب بالمتوسط الحالي وقت الخروج
    (بلا إعادة حساب رجعي). هذه معادلة مستقلة، مكتوبة هنا يدوياً فقط —
    ولو تصادف تشابهها مع app/services/item_queries.py، فهذا لأن كلاهما
    يطبّق نفس التعريف المحاسبي القياسي، لا لأن أحدهما نسخة من الآخر.
    """
    def __init__(self):
        self.qty = D_("0")
        self.value = D_("0")  # قيمة المخزون الإجمالية بالعملة الأساسية

    @property
    def avg_cost(self) -> D_:
        return self.value / self.qty if self.qty else D_("0")

    def purchase(self, qty: D_, unit_price_doc_ccy: D_, exchange_rate: D_) -> D_:
        """يُرجع unit_cost بالعملة الأساسية لهذه الدفعة تحديداً (وليس المتوسط)."""
        unit_cost_base = unit_price_doc_ccy * exchange_rate
        self.qty += qty
        self.value += qty * unit_cost_base
        return unit_cost_base

    def sell(self, qty: D_):
        """يُرجع (COGS لهذه العملية، unit_cost وقت البيع) — يسحب بالمتوسط الحالي."""
        cost_per_unit = self.avg_cost
        cogs = qty * cost_per_unit
        self.qty -= qty
        self.value -= cogs
        return cogs, cost_per_unit

    def return_in(self, qty: D_, historical_unit_cost: D_) -> None:
        """مرتجع بيع: يعيد الكمية بتكلفتها التاريخية (لا بالمتوسط الحالي)."""
        self.qty += qty
        self.value += qty * historical_unit_cost


def make_purchase(session, item, qty, price, ccy, rate, no):
    inv = Invoice(invoice_no=no, kind=InvoiceKind.PURCHASE, party_name="مورد",
                   invoice_date=today, currency_code=ccy, exchange_rate=rate, status=InvoiceStatus.DRAFT)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=qty, unit_price=price)]
    session.add(inv); session.commit()
    entry = post_purchase_invoice(session, inv, is_cash=True); session.commit()
    return inv, entry


def make_sale(session, item, qty, price, ccy, rate, no):
    inv = Invoice(invoice_no=no, kind=InvoiceKind.SALES, party_name="زبون",
                   invoice_date=today, currency_code=ccy, exchange_rate=rate, status=InvoiceStatus.DRAFT)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=qty, unit_price=price)]
    session.add(inv); session.commit()
    entry = post_sales_invoice(session, inv, is_cash=True); session.commit()
    return inv, entry


# ======================================================================
# المجموعة 1 — تكلفة المادة عبر عملات متعددة + دورة كاملة
# ======================================================================
print("== المجموعة 1: Weighted Average عبر SYP/USD/EUR + بيع + مرتجع ==")
s = fresh_session()
coa = create_default_chart_of_accounts(s)
item1 = create_item(s, sku="AGG-1", name_ar="مادة اختبار عدواني 1", unit="قطعة",
                     inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                     cost_method=CostMethod.AVERAGE)
s.commit()
oracle1 = AverageCostOracle()

_, e1 = make_purchase(s, item1, D_("100"), D_("10000"), "SYP", D_("1"), "AGG1-P1")
oracle1.purchase(D_("100"), D_("10000"), D_("1"))
_, e2 = make_purchase(s, item1, D_("100"), D_("1"), "USD", D_("18000"), "AGG1-P2")
oracle1.purchase(D_("100"), D_("1"), D_("18000"))
_, e3 = make_purchase(s, item1, D_("50"), D_("2"), "EUR", D_("21000"), "AGG1-P3")
oracle1.purchase(D_("50"), D_("2"), D_("21000"))

summary = get_item_stock_summary(s, item1.id)
check("المجموعة1: الكمية بعد 3 مشتريات", summary.quantity == oracle1.qty, f"actual={summary.quantity} oracle={oracle1.qty}")
check("المجموعة1: قيمة المخزون تطابق Oracle", abs(summary.inventory_value - oracle1.value) <= D_("0.05"),
      f"actual={summary.inventory_value} oracle={oracle1.value}")
check("المجموعة1: متوسط التكلفة يطابق Oracle", abs(summary.average_cost - oracle1.avg_cost) <= D_("1"),
      f"actual={summary.average_cost} oracle={oracle1.avg_cost}")

sale_inv, sale_entry = make_sale(s, item1, D_("80"), D_("50000"), "SYP", D_("1"), "AGG1-S1")
expected_cogs, sale_unit_cost = oracle1.sell(D_("80"))
cogs_line = next(l for l in sale_entry.lines if l.account_id == coa["cogs"].id)
check("المجموعة1: COGS يطابق Oracle", abs(D_(str(cogs_line.debit_base)) - expected_cogs) <= D_("1"),
      f"actual={cogs_line.debit_base} oracle={expected_cogs}")

sret_inv = Invoice(invoice_no="AGG1-SR1", kind=InvoiceKind.SALES_RETURN, party_name="زبون",
                    invoice_date=today, currency_code="SYP", exchange_rate=D_("1"),
                    status=InvoiceStatus.DRAFT, original_invoice_id=sale_inv.id)
sret_inv.lines = [InvoiceLine(item_id=item1.id, quantity=D_("10"), unit_price=D_("50000"))]
s.add(sret_inv); s.commit()
sret_entry = post_sales_return(s, sret_inv, is_cash=True); s.commit()
oracle1.return_in(D_("10"), sale_unit_cost)

summary_after_return = get_item_stock_summary(s, item1.id)
check("المجموعة1: الكمية بعد مرتجع البيع تطابق Oracle",
      summary_after_return.quantity == oracle1.qty, f"actual={summary_after_return.quantity} oracle={oracle1.qty}")
check("المجموعة1: قيمة المخزون بعد المرتجع تطابق Oracle (تكلفة تاريخية لا متوسط)",
      abs(summary_after_return.inventory_value - oracle1.value) <= D_("1"),
      f"actual={summary_after_return.inventory_value} oracle={oracle1.value}")

# ======================================================================
# المجموعة 2 — تمييز سعر الشراء عن سعر صرف العملة
# ======================================================================
print("== المجموعة 2: تغير سعر الصرف بين عمليات شراء بنفس العملة ==")
s2 = fresh_session()
coa2 = create_default_chart_of_accounts(s2)
item2 = create_item(s2, sku="AGG-2", name_ar="مادة اختبار سعر صرف", unit="قطعة",
                     inventory_account_id=coa2["inventory"].id, cogs_account_id=coa2["cogs"].id,
                     cost_method=CostMethod.AVERAGE)
s2.commit()
oracle2 = AverageCostOracle()
for i, rate in enumerate([D_("15000"), D_("18000"), D_("22000")]):
    make_purchase(s2, item2, D_("10"), D_("5"), "USD", rate, f"AGG2-P{i}")
    oracle2.purchase(D_("10"), D_("5"), rate)

summary2 = get_item_stock_summary(s2, item2.id)
check("المجموعة2: 3 أسعار صرف مختلفة لنفس العملة — القيمة تطابق Oracle",
      abs(summary2.inventory_value - oracle2.value) <= D_("0.5"),
      f"actual={summary2.inventory_value} oracle={oracle2.value}")
check("المجموعة2: القيمة بمقياس مناسب (لا خلط سعر الوحدة بسعر الصرف)",
      summary2.inventory_value > D_("1000000"), f"actual={summary2.inventory_value}")

# ======================================================================
# المجموعة 3 — مرتجع بعد تغيّر المتوسط (الاختبار الأهم تاريخياً)
# ======================================================================
print("== المجموعة 3: بيع → شراء يغيّر المتوسط → مرتجع البيع القديم بتكلفته الأصلية ==")
s3 = fresh_session()
coa3 = create_default_chart_of_accounts(s3)
item3 = create_item(s3, sku="AGG-3", name_ar="مادة اختبار مرتجع متأخر", unit="قطعة",
                     inventory_account_id=coa3["inventory"].id, cogs_account_id=coa3["cogs"].id,
                     cost_method=CostMethod.AVERAGE)
s3.commit()
oracle3 = AverageCostOracle()

make_purchase(s3, item3, D_("100"), D_("10000"), "SYP", D_("1"), "AGG3-P1")
oracle3.purchase(D_("100"), D_("10000"), D_("1"))

old_sale_inv, old_sale_entry = make_sale(s3, item3, D_("20"), D_("15000"), "SYP", D_("1"), "AGG3-S1")
_, old_sale_unit_cost = oracle3.sell(D_("20"))
check("المجموعة3: تكلفة البيع الأصلي = 10,000 (المتوسط وقتها)", old_sale_unit_cost == D_("10000"))

make_purchase(s3, item3, D_("100"), D_("16000"), "SYP", D_("1"), "AGG3-P2")
oracle3.purchase(D_("100"), D_("16000"), D_("1"))

new_avg_after_second_purchase = oracle3.avg_cost
check("المجموعة3: المتوسط تغيّر فعلاً بعد الشراء الثاني", new_avg_after_second_purchase != D_("10000"),
      f"avg={new_avg_after_second_purchase}")

sret3 = Invoice(invoice_no="AGG3-SR1", kind=InvoiceKind.SALES_RETURN, party_name="زبون",
                invoice_date=today, currency_code="SYP", exchange_rate=D_("1"),
                status=InvoiceStatus.DRAFT, original_invoice_id=old_sale_inv.id)
sret3.lines = [InvoiceLine(item_id=item3.id, quantity=D_("20"), unit_price=D_("15000"))]
s3.add(sret3); s3.commit()
sret3_entry = post_sales_return(s3, sret3, is_cash=True); s3.commit()

inv_line_returned = next(l for l in sret3_entry.lines if l.account_id == coa3["inventory"].id)
expected_return_value = D_("20") * old_sale_unit_cost
wrong_value_if_bug = D_("20") * new_avg_after_second_purchase
check("المجموعة3: قيمة المرتجع = تكلفة تاريخية (20×10,000)، لا المتوسط الحالي",
      abs(D_(str(inv_line_returned.debit_base)) - expected_return_value) <= D_("1"),
      f"actual={inv_line_returned.debit_base} expected(تاريخي)={expected_return_value} "
      f"لو-كان-فيه-خطأ-سيكون={wrong_value_if_bug}")

# ======================================================================
# المجموعة 4 — فاتورة متعددة المواد بحسابات مختلفة كلياً
# ======================================================================
print("== المجموعة 4: فاتورة بيع بمادتين، كل مادة بحسابات مختلفة كلياً ==")
s4 = fresh_session()
coa4 = create_default_chart_of_accounts(s4)
from app.models import Account, AccountType
inv_acc_a = Account(code="1301", name_ar="مخزون أ", account_type=AccountType.ASSET); s4.add(inv_acc_a)
inv_acc_b = Account(code="1302", name_ar="مخزون ب", account_type=AccountType.ASSET); s4.add(inv_acc_b)
cogs_acc_a = Account(code="5301", name_ar="تكلفة أ", account_type=AccountType.EXPENSE); s4.add(cogs_acc_a)
cogs_acc_b = Account(code="5302", name_ar="تكلفة ب", account_type=AccountType.EXPENSE); s4.add(cogs_acc_b)
sales_acc_a = Account(code="4301", name_ar="مبيعات أ", account_type=AccountType.REVENUE); s4.add(sales_acc_a)
sales_acc_b = Account(code="4302", name_ar="مبيعات ب", account_type=AccountType.REVENUE); s4.add(sales_acc_b)
s4.commit()

item_a = create_item(s4, sku="AGG-4A", name_ar="مادة أ", unit="قطعة",
                      inventory_account_id=inv_acc_a.id, cogs_account_id=cogs_acc_a.id,
                      sales_account_id=sales_acc_a.id, cost_method=CostMethod.AVERAGE)
item_b = create_item(s4, sku="AGG-4B", name_ar="مادة ب", unit="قطعة",
                      inventory_account_id=inv_acc_b.id, cogs_account_id=cogs_acc_b.id,
                      sales_account_id=sales_acc_b.id, cost_method=CostMethod.AVERAGE)
s4.commit()

make_purchase(s4, item_a, D_("50"), D_("1000"), "SYP", D_("1"), "AGG4-PA")
make_purchase(s4, item_b, D_("30"), D_("2000"), "SYP", D_("1"), "AGG4-PB")

combined_inv = Invoice(invoice_no="AGG4-S1", kind=InvoiceKind.SALES, party_name="زبون",
                        invoice_date=today, currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
combined_inv.lines = [
    InvoiceLine(item_id=item_a.id, quantity=D_("10"), unit_price=D_("1500")),
    InvoiceLine(item_id=item_b.id, quantity=D_("5"), unit_price=D_("3000")),
]
s4.add(combined_inv); s4.commit()
combined_entry = post_sales_invoice(s4, combined_inv, is_cash=True); s4.commit()

sales_a_line = next((l for l in combined_entry.lines if l.account_id == sales_acc_a.id), None)
sales_b_line = next((l for l in combined_entry.lines if l.account_id == sales_acc_b.id), None)
cogs_a_line = next((l for l in combined_entry.lines if l.account_id == cogs_acc_a.id), None)
cogs_b_line = next((l for l in combined_entry.lines if l.account_id == cogs_acc_b.id), None)
inv_a_line = next((l for l in combined_entry.lines if l.account_id == inv_acc_a.id), None)
inv_b_line = next((l for l in combined_entry.lines if l.account_id == inv_acc_b.id), None)

check("المجموعة4: مبيعات المادة أ على حسابها الصحيح", sales_a_line is not None and D_(str(sales_a_line.credit_base)) == D_("15000"))
check("المجموعة4: مبيعات المادة ب على حسابها الصحيح", sales_b_line is not None and D_(str(sales_b_line.credit_base)) == D_("15000"))
check("المجموعة4: COGS المادة أ وب موجودان على حسابين منفصلين", cogs_a_line is not None and cogs_b_line is not None)
check("المجموعة4: مخزون أ منفصل عن مخزون ب", inv_a_line is not None and inv_b_line is not None)
check("المجموعة4: القيد متوازن رغم تعدد الحسابات", combined_entry.is_balanced())

# ======================================================================
# مجموعة إضافية — مرتجع شراء بسعر صرف مختلف عن الفاتورة الأصلية
# (بالضبط النمط الموثَّق في WORKFLOW.md §30 كخطأ تاريخي مُصلَح)
# ======================================================================
print("== مرتجع شراء بسعر صرف مختلف عن الشراء الأصلي ==")
s6 = fresh_session()
coa6 = create_default_chart_of_accounts(s6)
item6 = create_item(s6, sku="AGG-6", name_ar="مادة مرتجع شراء", unit="قطعة",
                     inventory_account_id=coa6["inventory"].id, cogs_account_id=coa6["cogs"].id,
                     cost_method=CostMethod.AVERAGE)
s6.commit()

# شراء أصلي: 50 وحدة × 2 USD @ 15,000 → تكلفة الوحدة التاريخية = 30,000 SYP
orig_purchase_inv, _ = make_purchase(s6, item6, D_("50"), D_("2"), "USD", D_("15000"), "AGG6-P1")
historical_unit_cost = D_("2") * D_("15000")  # = 30,000 — من الفاتورة الأصلية فقط

# مرتجع شراء لـ10 وحدات، لكن بسعر صرف مختلف تماماً (18,000 بدل 15,000)
# — لو استخدم القيد سعر المرتجع بدل التاريخي، ستكون القيمة 10×2×18,000=360,000
# بدل الصحيح 10×30,000=300,000
pret6 = Invoice(invoice_no="AGG6-PR1", kind=InvoiceKind.PURCHASE_RETURN, party_name="مورد",
                invoice_date=today, currency_code="USD", exchange_rate=D_("18000"),
                status=InvoiceStatus.DRAFT, original_invoice_id=orig_purchase_inv.id)
pret6.lines = [InvoiceLine(item_id=item6.id, quantity=D_("10"), unit_price=D_("2"))]
s6.add(pret6); s6.commit()
pret6_entry = post_purchase_return(s6, pret6, is_cash=True); s6.commit()

inv_credit_line = next(l for l in pret6_entry.lines if l.account_id == coa6["inventory"].id)
expected_correct = D_("10") * historical_unit_cost       # 300,000 — الصحيح
wrong_if_bug = D_("10") * D_("2") * D_("18000")           # 360,000 — لو رجع الخطأ التاريخي

check("مرتجع شراء بسعر صرف مختلف: القيد يطابق التكلفة التاريخية للشراء الأصلي، لا سعر صرف المرتجع",
      abs(D_(str(inv_credit_line.credit_base)) - expected_correct) <= D_("1"),
      f"actual={inv_credit_line.credit_base} expected(تاريخي)={expected_correct} لو-رجع-الخطأ={wrong_if_bug}")

# التحقق أيضاً أن حركة المخزون (InventoryMovement) وسطر القيد متطابقان تماماً
# — هذا بالضبط ما وثّقه §30 كمصدرين مختلفين لنفس الرقم قبل الإصلاح
return_movement = s6.execute(
    select(InventoryMovement).where(
        InventoryMovement.source_type == "purchase_return",
        InventoryMovement.source_id == pret6.id,
    )
).scalars().first()
check("مرتجع شراء: قيمة حركة المخزون تطابق قيمة سطر القيد (مصدر واحد لا مصدرين)",
      abs(D_(str(return_movement.unit_cost)) * D_("10") - D_(str(inv_credit_line.credit_base))) <= D_("1"),
      f"movement_value={D_(str(return_movement.unit_cost)) * D_('10')} journal_line={inv_credit_line.credit_base}")

# ======================================================================
# المجموعة 8/9 — عكس القيد المرحّل + منع تعديل المستندات المرحّلة
# ======================================================================
print("== المجموعة 8/9: عكس قيد يدوي مرحّل + رفض تعديل مستند مرحّل ==")
s5 = fresh_session()
coa5 = create_default_chart_of_accounts(s5)

manual = JournalEntry(entry_date=today, ref_no="AGG-MJ-1", description="قيد للعكس",
                       currency_code="SYP", exchange_rate=D_("1"), source_type="manual",
                       status=JournalEntryStatus.DRAFT)
s5.add(manual); s5.flush()
add_manual_line(s5, manual, coa5["cash"].id, debit=D_("5000"))
add_manual_line(s5, manual, coa5["sales"].id, credit=D_("5000"))
post_manual_entry(s5, manual); s5.commit()

reversal = reverse_manual_entry(s5, manual, reversal_date=today)
s5.commit()
check("المجموعة8: قيد العكس متوازن", reversal.is_balanced())
cash_lines = s5.query(JournalLine).filter_by(account_id=coa5["cash"].id).all()
net_cash = sum(D_(str(l.debit_base)) - D_(str(l.credit_base)) for l in cash_lines)
check("المجموعة8: صافي الأثر على الصندوق = صفر بعد العكس", net_cash == D_("0"), f"net={net_cash}")

try:
    reverse_manual_entry(s5, manual, reversal_date=today)
    check("المجموعة8: رفض عكس نفس القيد مرتين", False, "لم يُرفع استثناء!")
except JournalEditError:
    check("المجموعة8: رفض عكس نفس القيد مرتين", True)

try:
    add_manual_line(s5, manual, coa5["cash"].id, debit=D_("1"))
    check("المجموعة9: رفض تعديل قيد يدوي مرحّل", False, "لم يُرفع استثناء!")
except JournalEditError:
    check("المجموعة9: رفض تعديل قيد يدوي مرحّل", True)

item9 = create_item(s5, sku="AGG-9", name_ar="مادة قفل", unit="قطعة",
                     inventory_account_id=coa5["inventory"].id, cogs_account_id=coa5["cogs"].id,
                     cost_method=CostMethod.AVERAGE)
s5.commit()
make_purchase(s5, item9, D_("10"), D_("100"), "SYP", D_("1"), "AGG9-P1")
posted_inv = s5.query(Invoice).filter_by(invoice_no="AGG9-P1").first()
try:
    invoice_ensure_editable(posted_inv)
    check("المجموعة9: رفض تعديل فاتورة مرحّلة", False, "لم يُرفع استثناء!")
except EditNotAllowedError:
    check("المجموعة9: رفض تعديل فاتورة مرحّلة", True)

print()
print("=" * 70)
print(f"✅ كل الاختبارات العدوانية نجحت ({len(results)} تحقّقاً) — المجموعات 1-4، 8-9")
print("⚠️  المجموعة 5 (قبض/دفع بعملة مختلفة، فروقات صرف تسوية) غير قابلة للاختبار:")
print("   لا توجد وحدة receipts/payments/settlement في المشروع حالياً — راجع WORKFLOW.md §35.6")
print("=" * 70)
