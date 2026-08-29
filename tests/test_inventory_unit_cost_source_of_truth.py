"""
tests/test_inventory_unit_cost_source_of_truth.py
=====================================================
يثبت القاعدة المحاسبية الموثَّقة بـWORKFLOW.md §39: "التاريخ لا يُعاد
تسعيره بأثر رجعي" — InventoryMovement.unit_cost هو مصدر الحقيقة الوحيد
لأي حركة خروج، لا إعادة حساب متوسط مستقل.

السيناريو (بالضبط كما طُلب):
  شراء A@12,000 → شراء B@18,000 (متوسط=15,000) → بيع 10 (COGS=15,000)
  → شراء C@20,000 (متوسط يتغيّر) → مرتجع شراء مرتبط بالشراء A تحديداً
  (يجب أن يستخدم 12,000 لا المتوسط الحالي) → بيع جديد (يجب أن يستخدم
  المتوسط الصحيح بعد المرتجع، لا متوسط ملوَّث بافتراض خاطئ).

عند كل مرحلة حرجة: نقارن 3 مصادر مستقلة يجب أن تتفق:
  1) InventoryMovement (الحركات الخام من قاعدة البيانات)
  2) get_item_stock_summary() (دالة الإنتاج المُعاد بناؤها)
  3) General Ledger → حساب المخزون (القيود الفعلية المرحّلة)
Oracle مستقل رابع يحسب من معادلة summation مباشرة (Σin - Σout بـunit_cost
كل حركة) دون استدعاء أي كود إنتاجي، كخط دفاع أخير مستقل عن الثلاثة.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, InventoryMovement,
    JournalLine, CostMethod, MovementDirection,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice, post_sales_invoice
from app.services.returns import post_purchase_return
from app.services.item_queries import get_item_stock_summary

today = datetime.date.today()
results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def independent_summation_oracle(session, item_id) -> D_:
    """Oracle مستقل رابع: Σ(qty×unit_cost) لحركات الدخول - نفس الشيء
    للخروج، بمعادلة مباشرة من الحركات الخام، دون أي منطق ترحيل أو متوسط.
    يُستخدم unit_cost المخزَّن على كل حركة كما هو — هذا هو التعريف
    الحسابي المباشر لـ"القيمة المتبقية" بصرف النظر عن أي دالة إنتاجية."""
    movements = session.execute(
        select(InventoryMovement).where(InventoryMovement.item_id == item_id)
    ).scalars().all()
    total = D_("0")
    for m in movements:
        signed_qty = D_(str(m.quantity)) if m.direction == MovementDirection.IN else -D_(str(m.quantity))
        total += signed_qty * D_(str(m.unit_cost))
    return total


def three_way_check(session, item, inventory_acc_id, label):
    summary = get_item_stock_summary(session, item.id)
    oracle_value = independent_summation_oracle(session, item.id)
    inv_lines = session.query(JournalLine).filter_by(account_id=inventory_acc_id).all()
    ledger_balance = sum(D_(str(l.debit_base)) - D_(str(l.credit_base)) for l in inv_lines)

    check(f"{label}: get_item_stock_summary == Oracle مستقل (Σ unit_cost)",
          abs(summary.inventory_value - oracle_value) <= D_("2"),
          f"summary={summary.inventory_value} oracle={oracle_value}")
    check(f"{label}: get_item_stock_summary == دفتر الأستاذ الفعلي",
          abs(summary.inventory_value - ledger_balance) <= D_("2"),
          f"summary={summary.inventory_value} ledger={ledger_balance}")
    check(f"{label}: دفتر الأستاذ == Oracle مستقل",
          abs(ledger_balance - oracle_value) <= D_("2"),
          f"ledger={ledger_balance} oracle={oracle_value}")
    return summary


engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
s = sessionmaker(bind=engine)()
coa = create_default_chart_of_accounts(s)
item = create_item(s, sku="UC-SOT-1", name_ar="مادة مصدر الحقيقة", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
s.commit()

# --- شراء A: 50 × 12,000 ---
pa = Invoice(invoice_no="SOT-PA", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
             currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
pa.lines = [InvoiceLine(item_id=item.id, quantity=D_("50"), unit_price=D_("12000"))]
s.add(pa); s.commit(); post_purchase_invoice(s, pa, is_cash=True); s.commit()
three_way_check(s, item, coa["inventory"].id, "1) بعد شراء A@12,000")

# --- شراء B: 50 × 18,000 → المتوسط = 15,000 ---
pb = Invoice(invoice_no="SOT-PB", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
             currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
pb.lines = [InvoiceLine(item_id=item.id, quantity=D_("50"), unit_price=D_("18000"))]
s.add(pb); s.commit(); post_purchase_invoice(s, pb, is_cash=True); s.commit()
summary_after_b = three_way_check(s, item, coa["inventory"].id, "2) بعد شراء B@18,000")
check("2) المتوسط بعد A+B = 15,000 بالضبط", summary_after_b.average_cost == D_("15000"),
      f"actual={summary_after_b.average_cost}")

# --- بيع 10 وحدات → COGS يجب أن يكون 15,000 للوحدة ---
sale1 = Invoice(invoice_no="SOT-S1", kind=InvoiceKind.SALES, party_name="زبون", invoice_date=today,
                 currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
sale1.lines = [InvoiceLine(item_id=item.id, quantity=D_("10"), unit_price=D_("25000"))]
s.add(sale1); s.commit()
sale1_entry = post_sales_invoice(s, sale1, is_cash=True); s.commit()
cogs_line1 = next(l for l in sale1_entry.lines if l.account_id == coa["cogs"].id)
check("3) COGS للبيع الأول = 10×15,000 بالضبط", D_(str(cogs_line1.debit_base)) == D_("150000"),
      f"actual={cogs_line1.debit_base}")
three_way_check(s, item, coa["inventory"].id, "3) بعد البيع الأول")

# --- شراء C: 40 × 20,000 → المتوسط يتغيّر مجدداً ---
pc = Invoice(invoice_no="SOT-PC", kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
             currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
pc.lines = [InvoiceLine(item_id=item.id, quantity=D_("40"), unit_price=D_("20000"))]
s.add(pc); s.commit(); post_purchase_invoice(s, pc, is_cash=True); s.commit()
summary_after_c = three_way_check(s, item, coa["inventory"].id, "4) بعد شراء C@20,000")
avg_before_return = summary_after_c.average_cost
check("4) المتوسط تغيّر ولم يعد 12,000", avg_before_return != D_("12000"), f"avg={avg_before_return}")

# --- مرتجع شراء مرتبط بالشراء A تحديداً (20 وحدة) — يجب أن يستخدم 12,000 لا المتوسط الحالي ---
pret = Invoice(invoice_no="SOT-PR1", kind=InvoiceKind.PURCHASE_RETURN, party_name="مورد", invoice_date=today,
                currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT,
                original_invoice_id=pa.id)
pret.lines = [InvoiceLine(item_id=item.id, quantity=D_("20"), unit_price=D_("12000"))]
s.add(pret); s.commit()
pret_entry = post_purchase_return(s, pret, is_cash=True); s.commit()
inv_credit_line = next(l for l in pret_entry.lines if l.account_id == coa["inventory"].id)
expected_return_value = D_("20") * D_("12000")  # = 240,000 — التاريخية
check("5) مرتجع الشراء يستخدم تكلفة الشراء A التاريخية (240,000)، لا المتوسط الحالي",
      abs(D_(str(inv_credit_line.credit_base)) - expected_return_value) <= D_("1"),
      f"actual={inv_credit_line.credit_base} expected(تاريخي)={expected_return_value}")

# **هذا هو الفحص الحاسم**: بعد المرتجع، الثلاثة مصادر يجب أن تتفق
summary_after_return = three_way_check(s, item, coa["inventory"].id, "5) بعد مرتجع الشراء (الفحص الحاسم)")

# احسب المتوسط الصحيح يدوياً للتأكد من الرقم المتوقع (Oracle خامس، تحقّق يدوي بحت)
# قبل المرتجع: qty=130, value=avg_before_return*130
# بعد المرتجع: qty=110, value = (avg_before_return*130) - 240,000
expected_qty_after_return = D_("130") - D_("20")
expected_value_after_return = (avg_before_return * D_("130")) - expected_return_value
expected_avg_after_return = expected_value_after_return / expected_qty_after_return
check("5) المتوسط بعد المرتجع يطابق الحساب اليدوي المستقل",
      abs(summary_after_return.average_cost - expected_avg_after_return) <= D_("1"),
      f"actual={summary_after_return.average_cost} expected={expected_avg_after_return}")

# --- بيع جديد بعد المرتجع — يجب أن يستخدم المتوسط الصحيح (بعد المرتجع)، لا القديم الملوَّث ---
sale2 = Invoice(invoice_no="SOT-S2", kind=InvoiceKind.SALES, party_name="زبون", invoice_date=today,
                 currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
sale2.lines = [InvoiceLine(item_id=item.id, quantity=D_("15"), unit_price=D_("30000"))]
s.add(sale2); s.commit()
sale2_entry = post_sales_invoice(s, sale2, is_cash=True); s.commit()
cogs_line2 = next(l for l in sale2_entry.lines if l.account_id == coa["cogs"].id)
expected_cogs2 = D_("15") * expected_avg_after_return
check("6) COGS للبيع الثاني يستخدم المتوسط الصحيح بعد المرتجع (لا القديم الملوَّث)",
      abs(D_(str(cogs_line2.debit_base)) - expected_cogs2) <= D_("2"),
      f"actual={cogs_line2.debit_base} expected={expected_cogs2}")
three_way_check(s, item, coa["inventory"].id, "6) بعد البيع الثاني (الرصيد النهائي)")

print()
print("=" * 70)
print(f"✅ قاعدة 'التاريخ لا يُعاد تسعيره' مُثبَتة عبر 3 مصادر مستقلة في كل مرحلة ({len(results)} تحقّقاً)")
print("=" * 70)
