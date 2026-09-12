"""
PHASE 3B-5-A — Characterization Suite
========================================
هذا الملف **لا يختبر "الصحيح"** — يثبّت السلوك الحالي للنظام كما هو،
تمهيداً لأي عمل على Historical Corrections (3B-5-B/C/D). أي تغيير مستقبلي
يكسر أحد هذه الاختبارات يجب أن يكون تغييراً **واعياً موثَّقاً**، لا صدفة.

لا تعديل إنتاجي هنا إطلاقاً — قراءة/تشغيل فقط.
"""
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
from app.services.posting import post_sales_invoice, post_purchase_invoice, get_default_warehouse
from app.services.item_queries import get_item_stock_summary

results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, label, detail))
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))


def fresh_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_accounts(session):
    cash = Account(code="1101", name_ar="الصندوق", account_type=AccountType.ASSET)
    inv = Account(code="1210", name_ar="المخزون", account_type=AccountType.ASSET)
    cogs = Account(code="5110", name_ar="تكلفة المبيعات", account_type=AccountType.EXPENSE)
    sales = Account(code="4100", name_ar="المبيعات", account_type=AccountType.REVENUE)
    session.add_all([cash, inv, cogs, sales])
    session.commit()
    session.add(Setting(key="default_cash_account_id", value=str(cash.id)))
    session.add(Setting(key="default_sales_account_id", value=str(sales.id)))
    session.add(Setting(key="default_sales_tax_account_id", value=str(sales.id)))
    session.add(Setting(key="default_purchases_tax_account_id", value=str(sales.id)))
    session.add(Setting(key="base_currency", value="SYP"))
    session.commit()
    return cash, inv, cogs, sales


def make_item(session, inv, cogs, sales):
    item = Item(sku="X", name_ar="مادة اختبار", inventory_account_id=inv.id,
                cogs_account_id=cogs.id, sales_account_id=sales.id, cost_method=CostMethod.AVERAGE)
    session.add(item)
    session.commit()
    return item


_counter = {"n": 0}


def _ref(prefix):
    _counter["n"] += 1
    return f"{prefix}-{_counter['n']:05d}"


def make_sale(session, item, wh, qty, price, d):
    inv_ = Invoice(invoice_no=_ref("SI"), kind=InvoiceKind.SALES, party_name="عميل",
                    invoice_date=d, currency_code="SYP", exchange_rate=Decimal("1"),
                    status=InvoiceStatus.DRAFT, warehouse_id=wh.id)
    inv_.lines = [InvoiceLine(item_id=item.id, quantity=qty, unit_price=price)]
    session.add(inv_)
    session.commit()
    post_sales_invoice(session, inv_)
    return inv_


def make_purchase(session, item, wh, qty, price, d):
    inv_ = Invoice(invoice_no=_ref("PI"), kind=InvoiceKind.PURCHASE, party_name="مورد",
                    invoice_date=d, currency_code="SYP", exchange_rate=Decimal("1"),
                    status=InvoiceStatus.DRAFT, warehouse_id=wh.id)
    inv_.lines = [InvoiceLine(item_id=item.id, quantity=qty, unit_price=price)]
    session.add(inv_)
    session.commit()
    post_purchase_invoice(session, inv_)
    return inv_


# ---------------------------------------------------------------------------
# 1) get_item_stock_summary is a full O(n) recompute from scratch every call
#    — no cached running balance anywhere.
# ---------------------------------------------------------------------------
s = fresh_session()
cash, inv, cogs, sales = seed_accounts(s)
item = make_item(s, inv, cogs, sales)
wh = get_default_warehouse(s)
d0 = datetime.date(2026, 1, 1)
make_purchase(s, item, wh, 100, Decimal("10"), d0)
summary1 = get_item_stock_summary(s, item.id, warehouse_id=wh.id)
make_purchase(s, item, wh, 100, Decimal("20"), d0)
summary2 = get_item_stock_summary(s, item.id, warehouse_id=wh.id)
check(
    "1. average cost is recomputed over ALL historical movements on every call (no cache/running balance)",
    summary1.average_cost == Decimal("10") and summary2.average_cost == Decimal("15"),
    f"after 100@10: avg={summary1.average_cost}; after +100@20: avg={summary2.average_cost}",
)

# ---------------------------------------------------------------------------
# 2) Ordering key is InventoryMovement.movement_date ONLY (see
#    item_queries.py query.order_by(InventoryMovement.movement_date)) —
#    no secondary tiebreaker (e.g. id / created_at) for same-date movements.
#    This means the processing order of two same-day movements depends on
#    whatever order the DB returns them in for equal keys — not guaranteed
#    to be insertion order by SQL semantics, even though SQLite happens to
#    preserve it in practice for a simple unindexed scan.
# ---------------------------------------------------------------------------
s2 = fresh_session()
cash2, inv2, cogs2, sales2 = seed_accounts(s2)
item2 = make_item(s2, inv2, cogs2, sales2)
wh2 = get_default_warehouse(s2)
same_day = datetime.date(2026, 2, 1)
make_purchase(s2, item2, wh2, 10, Decimal("10"), same_day)
make_purchase(s2, item2, wh2, 10, Decimal("30"), same_day)
mvts = s2.query(InventoryMovement).filter_by(item_id=item2.id).order_by(InventoryMovement.movement_date).all()
check(
    "2. no explicit secondary sort key exists for same movement_date "
    "(observed order here happens to match insertion order, but the query has no ORDER BY id/created_at tiebreaker)",
    len({m.movement_date for m in mvts}) == 1 and len(mvts) == 2,
    f"both movements share movement_date={same_day}, DB-returned order of ids: {[m.id for m in mvts]}",
)

# ---------------------------------------------------------------------------
# 3) Backdated purchase inserted AFTER a sale has already consumed the
#    pre-backdate average does NOT retroactively correct the sale's stored
#    unit_cost / COGS. The already-POSTED sale keeps its original (now
#    provably wrong under a "true" chronological average) unit_cost forever.
#    This is the central 3B-5 problem statement, reproduced concretely.
# ---------------------------------------------------------------------------
s3 = fresh_session()
cash3, inv3, cogs3, sales3 = seed_accounts(s3)
item3 = make_item(s3, inv3, cogs3, sales3)
wh3 = get_default_warehouse(s3)
d_jan = datetime.date(2026, 1, 10)
d_feb = datetime.date(2026, 2, 10)
d_jan_early = datetime.date(2026, 1, 1)  # earlier than d_jan, entered LATER in real time
make_purchase(s3, item3, wh3, 100, Decimal("10"), d_jan)      # avg = 10
sale = make_sale(s3, item3, wh3, 50, Decimal("99"), d_feb)     # COGS booked at avg=10
sale_movement = s3.query(InventoryMovement).filter_by(
    source_type="sales_invoice", source_id=sale.id).first()
cost_at_posting_time = sale_movement.unit_cost
# Now a forgotten purchase from BEFORE the Jan purchase is entered late,
# at a very different price:
make_purchase(s3, item3, wh3, 100, Decimal("50"), d_jan_early)
sale_movement_after = s3.query(InventoryMovement).filter_by(
    source_type="sales_invoice", source_id=sale.id).first()
current_true_average_including_backdated_entry = get_item_stock_summary(
    s3, item3.id, warehouse_id=wh3.id
)  # informational only — includes the still-unsold late-arriving stock
check(
    "3. a late-entered backdated purchase does NOT retroactively recompute an already-posted sale's stored unit_cost",
    sale_movement_after.unit_cost == cost_at_posting_time == Decimal("10"),
    f"sale's stored unit_cost stayed {sale_movement_after.unit_cost} even though a cheaper-dated, "
    f"higher-cost purchase was later inserted before it chronologically",
)

# ---------------------------------------------------------------------------
# 4) InventoryMovement rows are never UPDATEd or DELETEd anywhere in
#    app/services/*.py for correction purposes — the only observed
#    mutation pattern is an additive mirror-reversal (invoice_cancel.py,
#    opening_balances.py reverse_opening_inventory), never in-place edit.
# ---------------------------------------------------------------------------
import subprocess
grep = subprocess.run(
    ["grep", "-rn", r"InventoryMovement", "app/services/"],
    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    capture_output=True, text=True,
).stdout
has_update_or_delete = any(
    kw in line for line in grep.splitlines()
    for kw in ("session.delete", ".update(")
)
check(
    "4. no code path updates or deletes an existing InventoryMovement row (additive-only reversal pattern confirmed)",
    not has_update_or_delete,
)

# ---------------------------------------------------------------------------
# 5) The ONLY existing "does a correction break history" guard is
#    reverse_opening_inventory()'s per-(item,warehouse) check for a later
#    OUT movement. It exists solely for opening-inventory reversal — there
#    is NO equivalent guard for editing/cancelling a mid-stream purchase
#    invoice that a later sale's average cost already depended on.
# ---------------------------------------------------------------------------
grep_guard = subprocess.run(
    ["grep", "-rln", "history is never re-priced\\|History is never re-priced\\|WORKFLOW.md §39",
     "app/services/"],
    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    capture_output=True, text=True,
).stdout.split()
check(
    "5. the 'later movement already depended on this' guard exists only in opening_balances.py, "
    "not generalized to purchase-invoice cancellation/edit",
    "app/services/opening_balances.py" in "".join(grep_guard) and
    "app/services/invoice_cancel.py" not in "".join(grep_guard),
    f"files referencing the §39 rule/guard text: {grep_guard}",
)

print()
print(f"Characterization summary: {sum(1 for r in results if r[0]=='PASS')}/{len(results)} PASS")
failed = [r for r in results if r[0] == "FAIL"]
if failed:
    print("FAILURES (means current understanding of the code is WRONG — stop and re-verify before writing the report):")
    for r in failed:
        print(" -", r[1], r[2])
    sys.exit(1)
