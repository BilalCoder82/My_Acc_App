import os, sys, datetime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Account, AccountType, Item, CostMethod, Warehouse, InventoryMovement,
    MovementDirection, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, Setting,
)
from app.services.posting import post_sales_invoice
from app.services.returns import post_sales_return

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

cash = Account(code="1101", name_ar="الصندوق", account_type=AccountType.ASSET)
inv_a = Account(code="1210", name_ar="مخزون أ", account_type=AccountType.ASSET)
inv_b = Account(code="1220", name_ar="مخزون ب", account_type=AccountType.ASSET)
cogs_a = Account(code="5110", name_ar="تكلفة مبيعات أ", account_type=AccountType.EXPENSE)
cogs_b = Account(code="5120", name_ar="تكلفة مبيعات ب", account_type=AccountType.EXPENSE)
sales_a = Account(code="4110", name_ar="مبيعات أ", account_type=AccountType.REVENUE)
default_sales = Account(code="4100", name_ar="مبيعات عامة", account_type=AccountType.REVENUE)
session.add_all([cash, inv_a, inv_b, cogs_a, cogs_b, sales_a, default_sales])
session.commit()

wh = Warehouse(name_ar="الرئيسي")
session.add(wh)
session.add(Setting(key="default_cash_account_id", value=str(cash.id)))
session.add(Setting(key="default_sales_account_id", value=str(default_sales.id)))
session.add(Setting(key="default_sales_tax_account_id", value=str(default_sales.id)))
session.commit()

# Item A: has its OWN sales_account_id (sales_a) -> should NOT use default_sales
item_a = Item(sku="A", name_ar="مادة أ", inventory_account_id=inv_a.id, cogs_account_id=cogs_a.id,
              sales_account_id=sales_a.id, cost_method=CostMethod.AVERAGE)
# Item B: NO sales_account_id -> should fall back to default_sales
item_b = Item(sku="B", name_ar="مادة ب", inventory_account_id=inv_b.id, cogs_account_id=cogs_b.id,
              sales_account_id=None, cost_method=CostMethod.AVERAGE)
session.add_all([item_a, item_b])
session.commit()

today = datetime.date.today()
session.add(InventoryMovement(item_id=item_a.id, warehouse_id=wh.id, direction=MovementDirection.IN,
                               quantity=100, unit_cost=Decimal("10"), movement_date=today, source_type="opening"))
session.add(InventoryMovement(item_id=item_b.id, warehouse_id=wh.id, direction=MovementDirection.IN,
                               quantity=100, unit_cost=Decimal("20"), movement_date=today, source_type="opening"))
session.commit()

invoice = Invoice(
    invoice_no="SI-1", kind=InvoiceKind.SALES, party_name="عميل تجريبي",
    invoice_date=today, currency_code="SYP", exchange_rate=Decimal("1"),
    status=InvoiceStatus.DRAFT, warehouse_id=wh.id,
)
invoice.lines = [
    InvoiceLine(item_id=item_a.id, quantity=5, unit_price=Decimal("50")),   # 250, cost 5*10=50
    InvoiceLine(item_id=item_b.id, quantity=3, unit_price=Decimal("80")),   # 240, cost 3*20=60
]
session.add(invoice)
session.commit()

entry = post_sales_invoice(session, invoice, is_cash=True)
session.commit()

lines_by_acc = {l.account_id: (l.debit, l.credit) for l in entry.lines}
print("journal lines by account:", {
    session.get(Account, acc_id).code: v for acc_id, v in lines_by_acc.items()
})

assert entry.is_balanced(), "entry not balanced"
# item A's own sales account got its revenue, NOT lumped into default_sales
assert lines_by_acc.get(sales_a.id) == (Decimal("0"), Decimal("250")), lines_by_acc.get(sales_a.id)
# item B (no sales_account_id) fell back to default_sales
assert lines_by_acc.get(default_sales.id) == (Decimal("0"), Decimal("240")), lines_by_acc.get(default_sales.id)
# each item's OWN cogs/inventory accounts used -- not just item A's for the whole invoice
assert lines_by_acc.get(cogs_a.id) == (Decimal("50"), Decimal("0"))
assert lines_by_acc.get(inv_a.id) == (Decimal("0"), Decimal("50"))
assert lines_by_acc.get(cogs_b.id) == (Decimal("60"), Decimal("0"))
assert lines_by_acc.get(inv_b.id) == (Decimal("0"), Decimal("60"))
print("T1 OK: multi-item sales invoice splits sales/COGS/inventory correctly per item's own accounts")

# --- return: same two items, partial return, verify same per-item split ---
return_invoice = Invoice(
    invoice_no="SR-1", kind=InvoiceKind.SALES_RETURN, party_name="عميل تجريبي",
    invoice_date=today, currency_code="SYP", exchange_rate=Decimal("1"),
    status=InvoiceStatus.DRAFT, original_invoice_id=invoice.id, warehouse_id=wh.id,
)
return_invoice.lines = [
    InvoiceLine(item_id=item_a.id, quantity=2, unit_price=Decimal("50")),  # 100, cost 2*10=20
    InvoiceLine(item_id=item_b.id, quantity=1, unit_price=Decimal("80")),  # 80, cost 1*20=20
]
session.add(return_invoice)
session.commit()

ret_entry = post_sales_return(session, return_invoice, is_cash=True)
session.commit()
ret_lines = {l.account_id: (l.debit, l.credit) for l in ret_entry.lines}
print("return journal lines by account:", {
    session.get(Account, acc_id).code: v for acc_id, v in ret_lines.items()
})
assert ret_entry.is_balanced()
assert ret_lines.get(sales_a.id) == (Decimal("100"), Decimal("0"))
assert ret_lines.get(default_sales.id) == (Decimal("80"), Decimal("0"))
assert ret_lines.get(inv_a.id) == (Decimal("20"), Decimal("0"))
assert ret_lines.get(cogs_a.id) == (Decimal("0"), Decimal("20"))
assert ret_lines.get(inv_b.id) == (Decimal("20"), Decimal("0"))
assert ret_lines.get(cogs_b.id) == (Decimal("0"), Decimal("20"))
print("T2 OK: sales return also splits correctly per item's own accounts")

print("ALL PER-ITEM ACCOUNT POSTING TESTS PASSED")
