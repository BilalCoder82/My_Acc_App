"""
tests/test_full_inventory_lifecycle.py
==========================================
دورة مخزون كاملة (WORKFLOW.md §37) — Oracle مستقل خاص بالمخزون
(InventoryLedgerOracle)، عبر مستندات منفصلة متعددة (لا فاتورة واحدة تجمع
كل شيء)، مع تتبّع جدول مرحلي (الكمية/متوسط التكلفة/قيمة المخزون/COGS)
عند كل خطوة، والأهم: التحقق من التطابق بين:

    Inventory Ledger  ↕  Inventory Account (GL)  ↕  COGS  ↕  Trial Balance

أي: Inventory Asset = Quantity × Average Cost فعلياً في دفتر الأستاذ،
لا فقط في استعلام المخزون المنعزل.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, InventoryMovement,
    JournalLine, JournalEntry, JournalEntryStatus, CostMethod, Account, Setting,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice, post_sales_invoice, get_default_warehouse
from app.services.returns import post_sales_return, post_purchase_return
from app.services.opening_balances import post_opening_inventory, OpeningInventoryLineInput
from app.services.journal_edit import add_manual_line, post_manual_entry
from app.services.item_queries import get_item_stock_summary
from app.reports.trial_balance import get_trial_balance

today = datetime.date.today()
results = []
stage_log = []  # (اسم المرحلة، كمية، متوسط، قيمة، COGS تراكمي)


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
# Oracle مستقل خاص بالمخزون — لا يستدعي أي كود من app/services/
# ======================================================================
class InventoryLedgerOracle:
    def __init__(self):
        self.qty = D_("0")
        self.value = D_("0")
        self.cumulative_cogs = D_("0")

    @property
    def avg_cost(self) -> D_:
        return self.value / self.qty if self.qty else D_("0")

    def receive(self, qty: D_, unit_cost_base: D_) -> None:
        """أي دخول للمخزون بتكلفة معروفة مباشرة بالعملة الأساسية:
        شراء، رصيد افتتاحي، أو مرتجع بيع (تكلفة تاريخية)."""
        self.qty += qty
        self.value += qty * unit_cost_base

    def issue(self, qty: D_) -> D_:
        """أي خروج بالمتوسط الحالي: بيع، أو مرتجع شراء **حر غير مربوط**
        بفاتورة أصلية (الحالة الوحيدة التي يستخدم فيها المتوسط فعلاً)."""
        cost = self.avg_cost
        removed_value = qty * cost
        self.qty -= qty
        self.value -= removed_value
        self.cumulative_cogs += removed_value
        return cost

    def return_purchase_at_historical_cost(self, qty: D_, historical_unit_cost: D_) -> None:
        """مرتجع شراء **مربوط بفاتورة أصلية**: التكلفة تُقرأ من حركة
        المخزون الأصلية تحديداً (وليس المتوسط الحالي وقت الإرجاع) —
        بالضبط كما يفعل app/services/returns.py فعلياً لهذه الحالة.
        لا يمس COGS إطلاقاً (مرتجع الشراء لا يمر بحساب تكلفة المبيعات)."""
        self.qty -= qty
        self.value -= qty * historical_unit_cost

    def snapshot(self, label: str) -> None:
        stage_log.append((label, self.qty, round(self.avg_cost, 2), round(self.value, 2), round(self.cumulative_cogs, 2)))


def verify_against_ledger(session, item, oracle, inventory_acc_id, cogs_acc_id, label, tol=D_("2")):
    """يتحقق من التطابق بين Oracle واستعلام المخزون المنعزل **و** دفتر
    الأستاذ الفعلي (Inventory Asset = Quantity × Average Cost بالقيود
    الفعلية، لا فقط بالحساب المنعزل)."""
    summary = get_item_stock_summary(session, item.id)
    check(f"{label}: الكمية (item_queries) تطابق Oracle",
          summary.quantity == oracle.qty, f"actual={summary.quantity} oracle={oracle.qty}")
    check(f"{label}: قيمة المخزون (item_queries) تطابق Oracle",
          abs(summary.inventory_value - oracle.value) <= tol,
          f"actual={summary.inventory_value} oracle={oracle.value}")

    inv_lines = session.query(JournalLine).filter_by(account_id=inventory_acc_id).all()
    ledger_inventory_balance = sum(D_(str(l.debit_base)) - D_(str(l.credit_base)) for l in inv_lines)
    check(f"{label}: حساب المخزون بدفتر الأستاذ = Quantity × Average Cost (Oracle)",
          abs(ledger_inventory_balance - oracle.value) <= tol,
          f"ledger={ledger_inventory_balance} oracle_value={oracle.value}")

    cogs_lines = session.query(JournalLine).filter_by(account_id=cogs_acc_id).all()
    ledger_cogs_balance = sum(D_(str(l.debit_base)) - D_(str(l.credit_base)) for l in cogs_lines)
    check(f"{label}: رصيد COGS بدفتر الأستاذ يطابق Oracle التراكمي",
          abs(ledger_cogs_balance - oracle.cumulative_cogs) <= tol,
          f"ledger={ledger_cogs_balance} oracle={oracle.cumulative_cogs}")

    tb = get_trial_balance(session, today)
    check(f"{label}: ميزان المراجعة متوازن فعلياً بعد هذه المرحلة", tb.is_balanced,
          f"debit={tb.total_debit} credit={tb.total_credit}")

    oracle.snapshot(label)


# ======================================================================
# السيناريو الرئيسي — دورة كاملة عبر مستندات منفصلة متعددة
# ======================================================================
print("== دورة المخزون الكاملة: رصيد افتتاحي → شراء → شراء USD → بيع → مرتجع بيع → شراء → مرتجع شراء ==")
s = fresh_session()
coa = create_default_chart_of_accounts(s)
equity_acc = s.query(Account).filter_by(code="3101").first()
item = create_item(s, sku="LIFECYCLE-1", name_ar="مادة دورة كاملة", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
s.commit()
oracle = InventoryLedgerOracle()

# --- المرحلة 0: رصيد افتتاحي (50 وحدة × 8,000 SYP) ---
s.add(Setting(key="base_currency", value="SYP"))
s.add(Setting(key="opening_balance_clearing_account_id", value=str(equity_acc.id)))
s.commit()
default_wh = get_default_warehouse(s)
# Phase 3B-2: post_opening_inventory() ينشئ القيد المحاسبي فعلياً بنفسه
# الآن (بخلاف set_item_opening_balance() القديمة) — لم نعد نحتاج لبناء
# ob_entry يدوياً هنا كما كان سابقاً؛ هذا بالضبط ما كان ينقص التطابق مع
# دفتر الأستاذ، والآن الخدمة نفسها تضمنه.
post_opening_inventory(s, [OpeningInventoryLineInput(
    item_id=item.id, warehouse_id=default_wh.id, quantity=D_("50"), unit_cost_foreign=D_("8000"))],
    today - datetime.timedelta(days=30))
s.commit()
oracle.receive(D_("50"), D_("8000"))
verify_against_ledger(s, item, oracle, coa["inventory"].id, coa["cogs"].id, "0) بعد الرصيد الافتتاحي")

# --- المرحلة 1: شراء SYP (مستند منفصل) ---
p1 = Invoice(invoice_no="LC-P1", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
             currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
p1.lines = [InvoiceLine(item_id=item.id, quantity=D_("100"), unit_price=D_("9000"))]
s.add(p1); s.commit(); post_purchase_invoice(s, p1, is_cash=True); s.commit()
oracle.receive(D_("100"), D_("9000"))
verify_against_ledger(s, item, oracle, coa["inventory"].id, coa["cogs"].id, "1) بعد الشراء الأول (SYP)")

# --- المرحلة 2: شراء USD (مستند منفصل، سعر صرف مختلف) ---
p2 = Invoice(invoice_no="LC-P2", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
             currency_code="USD", exchange_rate=D_("16000"), status=InvoiceStatus.DRAFT)
p2.lines = [InvoiceLine(item_id=item.id, quantity=D_("30"), unit_price=D_("1"))]
s.add(p2); s.commit(); post_purchase_invoice(s, p2, is_cash=True); s.commit()
oracle.receive(D_("30"), D_("1") * D_("16000"))
verify_against_ledger(s, item, oracle, coa["inventory"].id, coa["cogs"].id, "2) بعد الشراء الثاني (USD)")

# --- المرحلة 3: بيع (مستند منفصل) ---
sale1 = Invoice(invoice_no="LC-S1", kind=InvoiceKind.SALES, party_name="زبون", invoice_date=today,
                 currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
sale1.lines = [InvoiceLine(item_id=item.id, quantity=D_("60"), unit_price=D_("20000"))]
s.add(sale1); s.commit(); post_sales_invoice(s, sale1, is_cash=True); s.commit()
sale1_unit_cost = oracle.issue(D_("60"))
verify_against_ledger(s, item, oracle, coa["inventory"].id, coa["cogs"].id, "3) بعد البيع الأول")

# --- المرحلة 4: مرتجع بيع (10 من نفس البيع، تكلفة تاريخية) ---
sret1 = Invoice(invoice_no="LC-SR1", kind=InvoiceKind.SALES_RETURN, party_name="زبون", invoice_date=today,
                 currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT,
                 original_invoice_id=sale1.id)
sret1.lines = [InvoiceLine(item_id=item.id, quantity=D_("10"), unit_price=D_("20000"))]
s.add(sret1); s.commit(); post_sales_return(s, sret1, is_cash=True); s.commit()
oracle.receive(D_("10"), sale1_unit_cost)  # يعود بتكلفة البيع الأصلي، لا المتوسط الحالي
oracle.cumulative_cogs -= D_("10") * sale1_unit_cost  # مرتجع البيع يعكس COGS جزئياً
verify_against_ledger(s, item, oracle, coa["inventory"].id, coa["cogs"].id, "4) بعد مرتجع البيع")

# --- المرحلة 5: شراء جديد (مستند ثالث منفصل، يغيّر المتوسط مجدداً) ---
p3 = Invoice(invoice_no="LC-P3", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
             currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
p3.lines = [InvoiceLine(item_id=item.id, quantity=D_("40"), unit_price=D_("12000"))]
s.add(p3); s.commit(); post_purchase_invoice(s, p3, is_cash=True); s.commit()
oracle.receive(D_("40"), D_("12000"))
verify_against_ledger(s, item, oracle, coa["inventory"].id, coa["cogs"].id, "5) بعد الشراء الثالث")

# --- المرحلة 6: مرتجع شراء (من الشراء الثالث تحديداً، تكلفته التاريخية) ---
pret1 = Invoice(invoice_no="LC-PR1", kind=InvoiceKind.PURCHASE_RETURN, party_name="مورد", invoice_date=today,
                 currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT,
                 original_invoice_id=p3.id)
pret1.lines = [InvoiceLine(item_id=item.id, quantity=D_("15"), unit_price=D_("12000"))]
s.add(pret1); s.commit(); post_purchase_return(s, pret1, is_cash=True); s.commit()
oracle.return_purchase_at_historical_cost(D_("15"), D_("12000"))  # تكلفة p3 الأصلية تحديداً، لا المتوسط الحالي
verify_against_ledger(s, item, oracle, coa["inventory"].id, coa["cogs"].id, "6) بعد مرتجع الشراء (الرصيد النهائي)")

print("\nجدول المراحل:")
print(f"{'المرحلة':45} {'الكمية':>8} {'المتوسط':>12} {'القيمة':>14} {'COGS تراكمي':>14}")
for label, qty, avg, val, cogs in stage_log:
    print(f"{label:45} {str(qty):>8} {str(avg):>12} {str(val):>14} {str(cogs):>14}")

# ======================================================================
# سيناريو فرعي 1 — العودة إلى الصفر تماماً
# ======================================================================
print("\n== سيناريو فرعي: شراء 100 ثم بيع 100 → الكمية والقيمة = صفر تماماً ==")
s_zero = fresh_session()
coa_z = create_default_chart_of_accounts(s_zero)
item_z = create_item(s_zero, sku="ZERO-1", name_ar="مادة اختبار الصفر", unit="قطعة",
                      inventory_account_id=coa_z["inventory"].id, cogs_account_id=coa_z["cogs"].id,
                      cost_method=CostMethod.AVERAGE)
s_zero.commit()
pz = Invoice(invoice_no="Z-P1", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
             currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
pz.lines = [InvoiceLine(item_id=item_z.id, quantity=D_("100"), unit_price=D_("5000"))]
s_zero.add(pz); s_zero.commit(); post_purchase_invoice(s_zero, pz, is_cash=True); s_zero.commit()
sz = Invoice(invoice_no="Z-S1", kind=InvoiceKind.SALES, party_name="زبون", invoice_date=today,
             currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
sz.lines = [InvoiceLine(item_id=item_z.id, quantity=D_("100"), unit_price=D_("7000"))]
s_zero.add(sz); s_zero.commit(); post_sales_invoice(s_zero, sz, is_cash=True); s_zero.commit()

summary_zero = get_item_stock_summary(s_zero, item_z.id)
check("العودة للصفر: الكمية = 0 تماماً", summary_zero.quantity == D_("0"), f"actual={summary_zero.quantity}")
check("العودة للصفر: قيمة المخزون = 0 تماماً", summary_zero.inventory_value == D_("0"), f"actual={summary_zero.inventory_value}")
inv_lines_zero = s_zero.query(JournalLine).filter_by(account_id=coa_z["inventory"].id).all()
ledger_balance_zero = sum(D_(str(l.debit_base)) - D_(str(l.credit_base)) for l in inv_lines_zero)
check("العودة للصفر: رصيد حساب المخزون بدفتر الأستاذ = 0 أيضاً", ledger_balance_zero == D_("0"), f"ledger={ledger_balance_zero}")

# ======================================================================
# سيناريو فرعي 2 — تسلسل كميات مركّب (شراء→بيع40→مرتجع بيع10→مرتجع شراء20)
# ======================================================================
print("== سيناريو فرعي: شراء 100 → بيع 40 → مرتجع بيع 10 → مرتجع شراء 20 ==")
s_seq = fresh_session()
coa_sq = create_default_chart_of_accounts(s_seq)
item_sq = create_item(s_seq, sku="SEQ-1", name_ar="مادة تسلسل كميات", unit="قطعة",
                       inventory_account_id=coa_sq["inventory"].id, cogs_account_id=coa_sq["cogs"].id,
                       cost_method=CostMethod.AVERAGE)
s_seq.commit()
oracle_seq = InventoryLedgerOracle()

p_seq = Invoice(invoice_no="SQ-P1", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
                 currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
p_seq.lines = [InvoiceLine(item_id=item_sq.id, quantity=D_("100"), unit_price=D_("4000"))]
s_seq.add(p_seq); s_seq.commit(); post_purchase_invoice(s_seq, p_seq, is_cash=True); s_seq.commit()
oracle_seq.receive(D_("100"), D_("4000"))

s_seq_sale = Invoice(invoice_no="SQ-S1", kind=InvoiceKind.SALES, party_name="زبون", invoice_date=today,
                      currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
s_seq_sale.lines = [InvoiceLine(item_id=item_sq.id, quantity=D_("40"), unit_price=D_("6000"))]
s_seq.add(s_seq_sale); s_seq.commit(); post_sales_invoice(s_seq, s_seq_sale, is_cash=True); s_seq.commit()
seq_sale_cost = oracle_seq.issue(D_("40"))

s_seq_ret = Invoice(invoice_no="SQ-SR1", kind=InvoiceKind.SALES_RETURN, party_name="زبون", invoice_date=today,
                     currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT,
                     original_invoice_id=s_seq_sale.id)
s_seq_ret.lines = [InvoiceLine(item_id=item_sq.id, quantity=D_("10"), unit_price=D_("6000"))]
s_seq.add(s_seq_ret); s_seq.commit(); post_sales_return(s_seq, s_seq_ret, is_cash=True); s_seq.commit()
oracle_seq.receive(D_("10"), seq_sale_cost)

p_seq_ret = Invoice(invoice_no="SQ-PR1", kind=InvoiceKind.PURCHASE_RETURN, party_name="مورد", invoice_date=today,
                     currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT,
                     original_invoice_id=p_seq.id)
p_seq_ret.lines = [InvoiceLine(item_id=item_sq.id, quantity=D_("20"), unit_price=D_("4000"))]
s_seq.add(p_seq_ret); s_seq.commit(); post_purchase_return(s_seq, p_seq_ret, is_cash=True); s_seq.commit()
oracle_seq.return_purchase_at_historical_cost(D_("20"), D_("4000"))  # تكلفة p_seq الأصلية تحديداً

expected_final_qty = D_("100") - D_("40") + D_("10") - D_("20")  # = 50
summary_seq = get_item_stock_summary(s_seq, item_sq.id)
check("تسلسل الكميات: الكمية النهائية = 50 تماماً (100-40+10-20)",
      summary_seq.quantity == expected_final_qty == oracle_seq.qty,
      f"actual={summary_seq.quantity} expected={expected_final_qty} oracle={oracle_seq.qty}")
check("تسلسل الكميات: القيمة النهائية تطابق Oracle",
      abs(summary_seq.inventory_value - oracle_seq.value) <= D_("2"),
      f"actual={summary_seq.inventory_value} oracle={oracle_seq.value}")

print()
print("=" * 70)
print(f"✅ دورة المخزون الكاملة نجحت بكل مراحلها وسيناريوهاتها الفرعية ({len(results)} تحقّقاً)")
print("=" * 70)
