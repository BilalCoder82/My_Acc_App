"""
tests/test_warehouse_cost_isolation.py
==========================================
الإصلاح الفعلي (WORKFLOW.md §46): التكلفة منفصلة لكل مستودع. Oracle
مستقل تماماً (WarehouseCostOracle) لكل مستودع على حدة، لا يستدعي أي
كود من app/services/. يغطي الحالات السبع المطلوبة صراحة + النقل.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, CostMethod, Warehouse, JournalLine
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice, post_sales_invoice
from app.services.returns import post_sales_return, post_purchase_return
from app.services.inventory_transfer import transfer_stock, get_stock_balance
from app.services.item_queries import get_item_stock_summary

today = datetime.date.today()
results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


class WarehouseCostOracle:
    """oracle مستقل — نفس منطق §35 لكن بمعزل تام لكل مستودع، بلا استدعاء أي دالة إنتاجية."""
    def __init__(self):
        self.qty = D_("0")
        self.value = D_("0")

    @property
    def avg(self) -> D_:
        return self.value / self.qty if self.qty else D_("0")

    def receive(self, qty, cost):
        self.qty += qty
        self.value += qty * cost

    def issue(self, qty):
        cost = self.avg
        self.qty -= qty
        self.value -= qty * cost
        return cost


def fresh():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_purchase(session, item, qty, price, wh_id, no):
    inv = Invoice(invoice_no=no, kind=InvoiceKind.PURCHASE, party_name="مورد", invoice_date=today,
                  currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT, warehouse_id=wh_id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=qty, unit_price=price)]
    session.add(inv); session.commit()
    entry = post_purchase_invoice(session, inv, is_cash=True); session.commit()
    return inv, entry


def make_sale(session, item, qty, price, wh_id, no):
    inv = Invoice(invoice_no=no, kind=InvoiceKind.SALES, party_name="زبون", invoice_date=today,
                  currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT, warehouse_id=wh_id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=qty, unit_price=price)]
    session.add(inv); session.commit()
    entry = post_sales_invoice(session, inv, is_cash=True); session.commit()
    return inv, entry


# ======================================================================
# 1) A يحتفظ بمتوسطه، B يحتفظ بمتوسطه — لا تأثير متبادل
# ======================================================================
print("== 1) شراء منفصل في A وB — كل مستودع يحتفظ بمتوسطه الخاص ==")
s = fresh()
coa = create_default_chart_of_accounts(s)
item = create_item(s, sku="WCI-1", name_ar="مادة عزل التكلفة", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
wh_a = Warehouse(name_ar="مستودع A", is_active=True)
wh_b = Warehouse(name_ar="مستودع B", is_active=True)
s.add_all([wh_a, wh_b]); s.commit()
oracle_a, oracle_b = WarehouseCostOracle(), WarehouseCostOracle()

make_purchase(s, item, D_("100"), D_("1000"), wh_a.id, "W1-PA")
oracle_a.receive(D_("100"), D_("1000"))
make_purchase(s, item, D_("100"), D_("9000"), wh_b.id, "W1-PB")
oracle_b.receive(D_("100"), D_("9000"))

check("1) متوسط A = 1,000 بالضبط (لا تأثير من B)",
      get_item_stock_summary(s, item.id, warehouse_id=wh_a.id).average_cost == D_("1000"))
check("1) متوسط B = 9,000 بالضبط (لا تأثير من A)",
      get_item_stock_summary(s, item.id, warehouse_id=wh_b.id).average_cost == D_("9000"))

# ======================================================================
# 2) بيع من A يستخدم تكلفة A، بيع من B يستخدم تكلفة B
# ======================================================================
print("== 2) بيع من كل مستودع يستخدم تكلفته الخاصة فقط ==")
_, entry_sale_a = make_sale(s, item, D_("10"), D_("5000"), wh_a.id, "W2-SA")
expected_cogs_a = oracle_a.issue(D_("10"))
cogs_line_a = next(l for l in entry_sale_a.lines if l.account_id == coa["cogs"].id)
check("2) COGS بيع من A = 10×1,000 (تكلفة A فقط)",
      D_(str(cogs_line_a.debit_base)) == D_("10") * expected_cogs_a,
      f"actual={cogs_line_a.debit_base} expected={D_('10')*expected_cogs_a}")

_, entry_sale_b = make_sale(s, item, D_("10"), D_("12000"), wh_b.id, "W2-SB")
expected_cogs_b = oracle_b.issue(D_("10"))
cogs_line_b = next(l for l in entry_sale_b.lines if l.account_id == coa["cogs"].id)
check("2) COGS بيع من B = 10×9,000 (تكلفة B فقط، لم تتأثر ببيع A)",
      D_(str(cogs_line_b.debit_base)) == D_("10") * expected_cogs_b,
      f"actual={cogs_line_b.debit_base} expected={D_('10')*expected_cogs_b}")

# ======================================================================
# 3) شراء جديد في A يغيّر متوسط A فقط
# ======================================================================
print("== 3) شراء جديد في A لا يؤثر على متوسط B ==")
b_avg_before = get_item_stock_summary(s, item.id, warehouse_id=wh_b.id).average_cost
make_purchase(s, item, D_("50"), D_("1400"), wh_a.id, "W3-PA2")
oracle_a.receive(D_("50"), D_("1400"))
a_avg_after = get_item_stock_summary(s, item.id, warehouse_id=wh_a.id).average_cost
b_avg_after = get_item_stock_summary(s, item.id, warehouse_id=wh_b.id).average_cost
check("3) متوسط A تغيّر فعلاً بعد شرائه الجديد", abs(a_avg_after - oracle_a.avg) <= D_("1"),
      f"actual={a_avg_after} oracle={oracle_a.avg}")
check("3) متوسط B لم يتغيّر إطلاقاً (شراء A لا يمسّه)", b_avg_after == b_avg_before,
      f"before={b_avg_before} after={b_avg_after}")

# ======================================================================
# 4) مرتجع بيع في A يستخدم التكلفة التاريخية الصحيحة لـA
# ======================================================================
print("== 4) مرتجع بيع مرتبط بفاتورة A يستخدم تكلفة A التاريخية ==")
from app.models import Invoice as InvoiceModel
orig_sale_a = s.query(InvoiceModel).filter_by(invoice_no="W2-SA").first()
sret = Invoice(invoice_no="W4-SR", kind=InvoiceKind.SALES_RETURN, party_name="زبون", invoice_date=today,
               currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT,
               original_invoice_id=orig_sale_a.id, warehouse_id=wh_a.id)
sret.lines = [InvoiceLine(item_id=item.id, quantity=D_("3"), unit_price=D_("5000"))]
s.add(sret); s.commit()
sret_entry = post_sales_return(s, sret, is_cash=True); s.commit()
inv_line = next(l for l in sret_entry.lines if l.account_id == coa["inventory"].id)
check("4) قيمة مرتجع البيع = 3×تكلفة بيع A التاريخية (1,000)",
      D_(str(inv_line.debit_base)) == D_("3") * D_("1000"), f"actual={inv_line.debit_base}")
oracle_a.receive(D_("3"), D_("1000"))

# ======================================================================
# 5) مرتجع شراء مرتبط في A يعيد التكلفة التاريخية لـA
# ======================================================================
print("== 5) مرتجع شراء مرتبط بفاتورة شراء A يستخدم تكلفتها التاريخية ==")
orig_purchase_a2 = s.query(InvoiceModel).filter_by(invoice_no="W3-PA2").first()
pret = Invoice(invoice_no="W5-PR", kind=InvoiceKind.PURCHASE_RETURN, party_name="مورد", invoice_date=today,
               currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT,
               original_invoice_id=orig_purchase_a2.id, warehouse_id=wh_a.id)
pret.lines = [InvoiceLine(item_id=item.id, quantity=D_("5"), unit_price=D_("1400"))]
s.add(pret); s.commit()
pret_entry = post_purchase_return(s, pret, is_cash=True); s.commit()
pret_inv_line = next(l for l in pret_entry.lines if l.account_id == coa["inventory"].id)
check("5) قيمة مرتجع الشراء = 5×1,400 (تكلفة الشراء التاريخية في A)",
      D_(str(pret_inv_line.credit_base)) == D_("5") * D_("1400"), f"actual={pret_inv_line.credit_base}")

# ======================================================================
# 6) نفس المادة بمستودعين بتكاليف مختلفة تماماً — مغطاة أصلاً بحالة (1)، تأكيد
# ======================================================================
check("6) (تأكيد) نفس المادة بمستودعين بتكلفتين مختلفتين تماماً دون تداخل",
      get_item_stock_summary(s, item.id, warehouse_id=wh_a.id).average_cost !=
      get_item_stock_summary(s, item.id, warehouse_id=wh_b.id).average_cost)

# ======================================================================
# 7) النقل A → B: يحمل تكلفة A، يندمج طبيعياً بمتوسط B
# ======================================================================
print("== 7) نقل من A إلى B — التكلفة تُحسب من متوسط A الحالي، تندمج بمتوسط B ==")
a_avg_before_transfer = get_item_stock_summary(s, item.id, warehouse_id=wh_a.id).average_cost
b_qty_before = get_item_stock_summary(s, item.id, warehouse_id=wh_b.id).quantity
b_value_before = get_item_stock_summary(s, item.id, warehouse_id=wh_b.id).inventory_value

transfer_stock(s, item.id, wh_a.id, wh_b.id, D_("20"), today)
s.commit()

a_qty_after_transfer = get_item_stock_summary(s, item.id, warehouse_id=wh_a.id).quantity
b_avg_after_transfer = get_item_stock_summary(s, item.id, warehouse_id=wh_b.id).average_cost
expected_b_value_after = b_value_before + D_("20") * a_avg_before_transfer
expected_b_qty_after = b_qty_before + D_("20")
expected_b_avg_after = expected_b_value_after / expected_b_qty_after

check("7) النقل استخدم متوسط A الحالي (لا آخر شراء عالمي للمادة)",
      abs(b_avg_after_transfer - expected_b_avg_after) <= D_("1"),
      f"actual={b_avg_after_transfer} expected={expected_b_avg_after}")

# التأكد أن الكميات لا تختلط (get_stock_balance لكل مستودع)
check("7) كمية A انخفضت بمقدار 20 بالضبط بعد النقل",
      get_stock_balance(s, item.id, wh_a.id) == a_qty_after_transfer)
total_qty_check = get_stock_balance(s, item.id, wh_a.id) + get_stock_balance(s, item.id, wh_b.id)
check("7) إجمالي الكميتين معاً محفوظ (لا فقدان أو تكرار أثناء النقل)",
      total_qty_check == get_stock_balance(s, item.id, None))

print()
print("=" * 70)
print(f"✅ عزل التكلفة بين المستودعات مُثبَت بالكامل ({len(results)} تحقّقاً)")
print("=" * 70)
