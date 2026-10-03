"""
tests/test_comprehensive_review.py
=====================================
مراجعة تكاملية (WORKFLOW.md §48) — يغطي تحديداً ما لم تختبره الملفات
السابقة صراحة: نقل B→A (عكس اتجاه ما اختُبر بـ§46)، تسلسل قبض/دفع
متعدد الدفعات بأسعار صرف مختلفة (Oracle تراكمي مستقل)، وتطابق ميزان
المراجعة مع محرك المخزون (warehouse-aware).
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, CostMethod, Warehouse
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice, post_sales_invoice
from app.services.inventory_transfer import transfer_stock, get_stock_balance
from app.services.item_queries import get_item_stock_summary
from app.services.settlements import post_receipt, post_payment, get_invoice_balance_due
from app.reports.trial_balance import get_trial_balance

today = datetime.date.today()
results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def fresh():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# ======================================================================
# 1) نقل B → A (عكس الاتجاه المُختبَر سابقاً — لا افتراض أن اتجاهاً واحداً كافٍ)
# ======================================================================
print("== 1) نقل B → A (الاتجاه المعاكس لما اختُبر بـ§46) ==")
s = fresh()
coa = create_default_chart_of_accounts(s)
item = create_item(s, sku="REV-1", name_ar="مادة مراجعة شاملة", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
wh_a = Warehouse(name_ar="مراجعة A", is_active=True)
wh_b = Warehouse(name_ar="مراجعة B", is_active=True)
s.add_all([wh_a, wh_b]); s.commit()


def buy(session, item, qty, price, wh_id, no, ccy="SYP", rate=D_("1")):
    inv = Invoice(invoice_no=no, kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
                  currency_code=ccy, exchange_rate=rate, status=InvoiceStatus.DRAFT, warehouse_id=wh_id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=qty, unit_price=price)]
    session.add(inv); session.commit()
    post_purchase_invoice(session, inv, is_cash=True); session.commit()
    return inv


buy(s, item, D_("50"), D_("2000"), wh_a.id, "REV-PA")   # A: متوسط 2,000
buy(s, item, D_("30"), D_("6000"), wh_b.id, "REV-PB")   # B: متوسط 6,000

b_avg_before = get_item_stock_summary(s, item.id, warehouse_id=wh_b.id).average_cost
a_value_before = get_item_stock_summary(s, item.id, warehouse_id=wh_a.id).inventory_value
a_qty_before = get_item_stock_summary(s, item.id, warehouse_id=wh_a.id).quantity

transfer_stock(s, item.id, wh_b.id, wh_a.id, D_("10"), today)  # نقل B → A
s.commit()

expected_a_value_after = a_value_before + D_("10") * b_avg_before
expected_a_qty_after = a_qty_before + D_("10")
expected_a_avg_after = expected_a_value_after / expected_a_qty_after
a_avg_after = get_item_stock_summary(s, item.id, warehouse_id=wh_a.id).average_cost
check("1) نقل B→A: متوسط A الجديد يطابق الاندماج المتوقع (لا اتجاه واحد فقط يعمل)",
      abs(a_avg_after - expected_a_avg_after) <= D_("1"),
      f"actual={a_avg_after} expected={expected_a_avg_after}")
b_qty_after = get_item_stock_summary(s, item.id, warehouse_id=wh_b.id).quantity
check("1) كمية B انخفضت بمقدار 10 بالضبط", b_qty_after == D_("20"), f"actual={b_qty_after}")

# ======================================================================
# 2) تسلسل قبض متعدد بأسعار صرف مختلفة + دفع متعدد — Oracle تراكمي مستقل
# ======================================================================
print("== 2) تسلسل قبض متعدد (3 دفعات) + دفع متعدد (3 دفعات) بأسعار مختلفة ==")
s2 = fresh()
coa2 = create_default_chart_of_accounts(s2)
item2 = create_item(s2, sku="REV-2", name_ar="مادة تسلسل تسويات", unit="قطعة",
                     inventory_account_id=coa2["inventory"].id, cogs_account_id=coa2["cogs"].id,
                     cost_method=CostMethod.AVERAGE)
wh2 = Warehouse(name_ar="مستودع تسلسل", is_active=True)
s2.add(wh2); s2.commit()

sale_inv = Invoice(invoice_no="REV-S1", kind=InvoiceKind.SALES, party_name="عميل تسلسل",
                    invoice_date=today, currency_code="USD", exchange_rate=D_("15000"),
                    status=InvoiceStatus.DRAFT, warehouse_id=wh2.id)
sale_inv.lines = [InvoiceLine(item_id=item2.id, quantity=D_("10"), unit_price=D_("100"))]
s2.add(sale_inv); s2.commit()
post_sales_invoice(s2, sale_inv, is_cash=False); s2.commit()  # إجمالي = 1,000 USD

purchase_inv = Invoice(invoice_no="REV-P1", kind=InvoiceKind.PURCHASE, party_name="مورد تسلسل",
                        invoice_date=today, currency_code="USD", exchange_rate=D_("15000"),
                        status=InvoiceStatus.DRAFT, warehouse_id=wh2.id)
purchase_inv.lines = [InvoiceLine(item_id=item2.id, quantity=D_("5"), unit_price=D_("100"))]
s2.add(purchase_inv); s2.commit()
post_purchase_invoice(s2, purchase_inv, is_cash=False); s2.commit()  # إجمالي = 500 USD

# oracle تراكمي مستقل لفرق الصرف — معادلة يدوية بحتة
receipt_fx_oracle = D_("0")
for amt, rate in [(D_("300"), D_("15200")), (D_("300"), D_("14800")), (D_("400"), D_("15500"))]:
    receipt_fx_oracle += (amt * rate) - (amt * D_("15000"))
    post_receipt(s2, sale_inv, amt, today, rate, coa2["cash"].id)
    s2.commit()
check("2) الرصيد المستحق = صفر بعد 3 قبضيات (300+300+400=1000)",
      get_invoice_balance_due(s2, sale_inv) == D_("0"))

payment_fx_oracle = D_("0")
for amt, rate in [(D_("200"), D_("15300")), (D_("200"), D_("14700")), (D_("100"), D_("15100"))]:
    payment_fx_oracle += -((amt * rate) - (amt * D_("15000")))
    post_payment(s2, purchase_inv, amt, today, rate, coa2["cash"].id)
    s2.commit()
check("2) الرصيد المستحق للمورد = صفر بعد 3 دفعات (200+200+100=500)",
      get_invoice_balance_due(s2, purchase_inv) == D_("0"))

from app.models import JournalLine
fx_gain_total = sum(D_(str(l.credit_base)) for l in s2.query(JournalLine).filter_by(account_id=coa2["fx_gain"].id).all())
fx_loss_total = sum(D_(str(l.debit_base)) for l in s2.query(JournalLine).filter_by(account_id=coa2["fx_loss"].id).all())
net_fx_ledger = fx_gain_total - fx_loss_total
net_fx_oracle = receipt_fx_oracle + payment_fx_oracle
check("2) صافي فرق الصرف التراكمي (6 تسويات) يطابق Oracle مستقل",
      abs(net_fx_ledger - net_fx_oracle) <= D_("2"), f"ledger={net_fx_ledger} oracle={net_fx_oracle}")

# ======================================================================
# 3) تطابق ميزان المراجعة مع محرك المخزون (warehouse-aware) بعد كل ما سبق
# ======================================================================
print("== 3) تطابق قيمة المخزون بميزان المراجعة مع get_item_stock_summary ==")
tb = get_trial_balance(s, today)
check("3) ميزان مراجعة (المستودعات A/B) متوازن فعلياً", tb.is_balanced,
      f"debit={tb.total_debit} credit={tb.total_credit}")
inventory_row = next((r for r in tb.rows if r.account.id == coa["inventory"].id), None)
combined_engine_value = (
    get_item_stock_summary(s, item.id, warehouse_id=wh_a.id).inventory_value +
    get_item_stock_summary(s, item.id, warehouse_id=wh_b.id).inventory_value
)
ledger_inventory_value = D_(str(inventory_row.total_debit)) - D_(str(inventory_row.total_credit)) if inventory_row else D_("0")
check("3) قيمة المخزون بميزان المراجعة = مجموع قيم كل مستودع من محرك المخزون",
      abs(ledger_inventory_value - combined_engine_value) <= D_("2"),
      f"ledger={ledger_inventory_value} engine_sum={combined_engine_value}")

tb2 = get_trial_balance(s2, today)
check("3) ميزان مراجعة سيناريو التسويات متوازن أيضاً", tb2.is_balanced)

print()
print("=" * 70)
print(f"✅ المراجعة الشاملة نجحت ({len(results)} تحقّقاً)")
print("=" * 70)
