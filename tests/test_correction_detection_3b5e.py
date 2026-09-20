"""
tests/test_correction_detection_3b5e.py
===========================================
PHASE 3B-5-E — Detection Implementation tests (Cluster 1 only). الأربعة
اختبارات المطلوبة حرفياً كما حُدِّدت بـ
3B-5-D_FINAL_REVIEW_AND_3B-5-E_SCOPE.md، بلا تخفيف:

    1. Coverage    — التسعة مسارات التي تُنشئ InventoryMovement، كل واحد
                      بمعزل، والـhook يجب أن يعمل مع كل واحد منها.
    2. Negative     — حركة بلا شقيقة لاحقة التاريخ لنفس (item, warehouse)
                      لا تُنتج CorrectionEvent إطلاقاً.
    3. Same-flush   — حركات متعددة من نفس الـflush لا تُبلِّغ إحداها عن
                      الأخرى أبداً (حماية أشقاء مستند واحد متعدد الأسطر).
    4. Reversal     — cancel_date بأثر رجعي يُفعِّل نفس آلية الاكتشاف
                      بالضبط، بلا أي فرع خاص بـsource_type داخل الـhook.

لا اختبار هنا لأي شيء أبعد من Detection — لا Impact Scope، لا
Recalculation، لا TieGroup/CandidateState، لا WITHDRAWN — كلها خارج نطاق
هذه الشريحة صراحة (راجع نفس ملف الـScope).
"""
import os, sys, datetime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Setting, Account, AccountType, Item, CostMethod, Warehouse,
    InventoryMovement, MovementDirection, Invoice, InvoiceLine, InvoiceKind,
    InvoiceStatus, CorrectionEvent, CorrectionEventRootCause,
    CorrectionEventStatus, RootCauseComponentType,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice, post_sales_invoice, get_default_warehouse
from app.services.returns import post_sales_return, post_purchase_return
from app.services.invoice_cancel import cancel_invoice
from app.services.inventory_transfer import transfer_stock
from app.services.opening_balances import (
    post_opening_inventory, reverse_opening_inventory, OpeningInventoryLineInput,
    CLEARING_ACCOUNT_SETTING_KEY,
)
from app.services.correction_detection import attach_detection_listeners

results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, label, detail))
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))


def fresh_session():
    attach_detection_listeners()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed(session, with_clearing_account=False):
    coa = create_default_chart_of_accounts(session)
    session.add(Setting(key="base_currency", value="SYP"))
    if with_clearing_account:
        equity = Account(code="3199", name_ar="أرصدة افتتاحية - توازن", account_type=AccountType.EQUITY)
        session.add(equity)
        session.flush()
        session.add(Setting(key=CLEARING_ACCOUNT_SETTING_KEY, value=str(equity.id)))
    session.commit()
    wh = get_default_warehouse(session)
    return coa, wh


def make_item(session, coa, sku):
    return create_item(
        session, sku=sku, name_ar=f"صنف {sku}", unit="قطعة",
        inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
        sales_account_id=coa["sales"].id, cost_method=CostMethod.AVERAGE,
    )


def candidate_for_movement(session, movement_id):
    """يرجّع (CorrectionEvent, CorrectionEventRootCause) لو وُجد جذر
    يشير لهذه الحركة تحديداً، وإلا (None, None)."""
    rc = session.query(CorrectionEventRootCause).filter_by(
        source_type="inventory_movement", source_id=movement_id,
    ).first()
    if rc is None:
        return None, None
    return session.get(CorrectionEvent, rc.correction_event_id), rc


def purchase(session, item, wh, qty_, price, d, ref):
    inv = Invoice(invoice_no=ref, kind=InvoiceKind.PURCHASE, party_name="مورد",
                  invoice_date=d, currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT,
                  warehouse_id=wh.id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=D_(str(qty_)), unit_price=D_(str(price)))]
    session.add(inv); session.commit()
    post_purchase_invoice(session, inv, is_cash=True)
    session.commit()
    return inv


def sale(session, item, wh, qty_, price, d, ref):
    inv = Invoice(invoice_no=ref, kind=InvoiceKind.SALES, party_name="زبون",
                  invoice_date=d, currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT,
                  warehouse_id=wh.id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=D_(str(qty_)), unit_price=D_(str(price)))]
    session.add(inv); session.commit()
    post_sales_invoice(session, inv, is_cash=True)
    session.commit()
    return inv


def latest_movement(session, item_id, wh_id, source_type):
    return session.execute(
        select(InventoryMovement).where(
            InventoryMovement.item_id == item_id, InventoryMovement.warehouse_id == wh_id,
            InventoryMovement.source_type == source_type,
        ).order_by(InventoryMovement.id.desc())
    ).scalars().first()


DAY1 = datetime.date(2026, 1, 1)
DAY2 = datetime.date(2026, 1, 2)
DAY5 = datetime.date(2026, 1, 5)
DAY20 = datetime.date(2026, 1, 20)

# ===========================================================================
# 1) COVERAGE — التسعة مسارات، كل واحد بمعزل بجلسة خاصة به
# ===========================================================================

# --- 1.1 post_purchase_invoice (posting.py) --------------------------------
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "COV-PUR")
purchase(s, item, wh, 10, 10, DAY20, "COV-PUR-A")          # الحركة اللاحقة الموجودة سلفاً
purchase(s, item, wh, 5, 12, DAY1, "COV-PUR-B")             # الحركة الجديدة، أقدم تاريخاً
m = latest_movement(s, item.id, wh.id, "purchase_invoice")
ev, rc = candidate_for_movement(s, m.id)
check("1.1 Coverage — post_purchase_invoice يُطلِق الاكتشاف",
      ev is not None and ev.status == CorrectionEventStatus.CANDIDATE
      and rc.component_type == RootCauseComponentType.NEW_MOVEMENT)

# --- 1.2 post_sales_invoice (posting.py) -----------------------------------
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "COV-SAL")
purchase(s, item, wh, 100, 10, DAY20, "COV-SAL-P")          # مخزون كافٍ + الحركة اللاحقة
sale(s, item, wh, 10, 15, DAY1, "COV-SAL-S")                 # بيع بتاريخ أقدم من الشراء الموجود
m = latest_movement(s, item.id, wh.id, "sales_invoice")
ev, rc = candidate_for_movement(s, m.id)
check("1.2 Coverage — post_sales_invoice يُطلِق الاكتشاف",
      ev is not None and ev.status == CorrectionEventStatus.CANDIDATE
      and rc.component_type == RootCauseComponentType.NEW_MOVEMENT)

# --- 1.3 post_sales_return (returns.py) ------------------------------------
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "COV-SRET")
purchase(s, item, wh, 50, 10, DAY1, "COV-SRET-P")
original_sale = sale(s, item, wh, 20, 15, DAY5, "COV-SRET-S")
purchase(s, item, wh, 5, 11, DAY20, "COV-SRET-LATER")        # الحركة اللاحقة الموجودة سلفاً
ret = Invoice(invoice_no="COV-SRET-R", kind=InvoiceKind.SALES_RETURN, party_name="زبون",
              invoice_date=DAY2, currency_code="SYP", exchange_rate=D_("1"),
              status=InvoiceStatus.DRAFT, original_invoice_id=original_sale.id)
ret.lines = [InvoiceLine(item_id=item.id, quantity=D_("5"), unit_price=D_("15"))]
s.add(ret); s.commit()
post_sales_return(s, ret, is_cash=True)
s.commit()
m = latest_movement(s, item.id, wh.id, "sales_return")
ev, rc = candidate_for_movement(s, m.id)
check("1.3 Coverage — post_sales_return يُطلِق الاكتشاف",
      ev is not None and ev.status == CorrectionEventStatus.CANDIDATE
      and rc.component_type == RootCauseComponentType.NEW_MOVEMENT)

# --- 1.4 post_purchase_return (returns.py) ----------------------------------
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "COV-PRET")
original_purchase = purchase(s, item, wh, 50, 10, DAY1, "COV-PRET-P")
purchase(s, item, wh, 5, 11, DAY20, "COV-PRET-LATER")        # الحركة اللاحقة الموجودة سلفاً
pret = Invoice(invoice_no="COV-PRET-R", kind=InvoiceKind.PURCHASE_RETURN, party_name="مورد",
               invoice_date=DAY2, currency_code="SYP", exchange_rate=D_("1"),
               status=InvoiceStatus.DRAFT, original_invoice_id=original_purchase.id)
pret.lines = [InvoiceLine(item_id=item.id, quantity=D_("5"), unit_price=D_("10"))]
s.add(pret); s.commit()
post_purchase_return(s, pret, is_cash=True)
s.commit()
m = latest_movement(s, item.id, wh.id, "purchase_return")
ev, rc = candidate_for_movement(s, m.id)
check("1.4 Coverage — post_purchase_return يُطلِق الاكتشاف",
      ev is not None and ev.status == CorrectionEventStatus.CANDIDATE
      and rc.component_type == RootCauseComponentType.NEW_MOVEMENT)

# --- 1.5 cancel_invoice (invoice_cancel.py) ---------------------------------
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "COV-CXL")
to_cancel = purchase(s, item, wh, 20, 10, DAY1, "COV-CXL-P")
purchase(s, item, wh, 5, 11, DAY20, "COV-CXL-LATER")         # الحركة اللاحقة الموجودة سلفاً
cancel_invoice(s, to_cancel, cancel_date=DAY2)
s.commit()
m = latest_movement(s, item.id, wh.id, "invoice_cancel")
ev, rc = candidate_for_movement(s, m.id)
check("1.5 Coverage — cancel_invoice يُطلِق الاكتشاف",
      ev is not None and ev.status == CorrectionEventStatus.CANDIDATE
      and rc.component_type == RootCauseComponentType.REVERSAL)

# --- 1.6/1.7 transfer_stock (inventory_transfer.py) — سطرا الإنشاء الاثنان -
s = fresh_session()
coa, wh_a = seed(s)
wh_b = Warehouse(name_ar="مستودع فرعي"); s.add(wh_b); s.commit()
item = make_item(s, coa, "COV-TRF")
purchase(s, item, wh_a, 50, 10, DAY1, "COV-TRF-P-A")
purchase(s, item, wh_a, 5, 11, DAY20, "COV-TRF-LATER-A")     # لاحقة بمستودع المصدر
purchase(s, item, wh_b, 5, 11, DAY20, "COV-TRF-LATER-B")     # لاحقة بمستودع الوجهة
transfer_stock(s, item.id, wh_a.id, wh_b.id, 10, transfer_date=DAY2)
s.commit()
m_out = latest_movement(s, item.id, wh_a.id, "stock_transfer")
m_in = latest_movement(s, item.id, wh_b.id, "stock_transfer")
ev_out, rc_out = candidate_for_movement(s, m_out.id)
ev_in, rc_in = candidate_for_movement(s, m_in.id)
check("1.6 Coverage — transfer_stock (الشق OUT) يُطلِق الاكتشاف",
      ev_out is not None and ev_out.status == CorrectionEventStatus.CANDIDATE)
check("1.7 Coverage — transfer_stock (الشق IN) يُطلِق الاكتشاف",
      ev_in is not None and ev_in.status == CorrectionEventStatus.CANDIDATE)

# --- 1.8 post_opening_inventory (opening_balances.py) -----------------------
s = fresh_session()
coa, wh = seed(s, with_clearing_account=True)
item = make_item(s, coa, "COV-OPN")
purchase(s, item, wh, 5, 11, DAY20, "COV-OPN-LATER")         # الحركة اللاحقة الموجودة سلفاً
post_opening_inventory(
    s, [OpeningInventoryLineInput(item_id=item.id, warehouse_id=wh.id,
                                    quantity=D_("10"), unit_cost_foreign=D_("9"))],
    opening_date=DAY1,
)
s.commit()
m = latest_movement(s, item.id, wh.id, "opening_inventory")
ev, rc = candidate_for_movement(s, m.id)
check("1.8 Coverage — post_opening_inventory يُطلِق الاكتشاف",
      ev is not None and ev.status == CorrectionEventStatus.CANDIDATE
      and rc.component_type == RootCauseComponentType.NEW_MOVEMENT)

# --- 1.9 reverse_opening_inventory (opening_balances.py) --------------------
s = fresh_session()
coa, wh = seed(s, with_clearing_account=True)
item = make_item(s, coa, "COV-ROPN")
opening_entry = post_opening_inventory(
    s, [OpeningInventoryLineInput(item_id=item.id, warehouse_id=wh.id,
                                    quantity=D_("10"), unit_cost_foreign=D_("9"))],
    opening_date=DAY1,
)
s.commit()
# حركة IN لاحقة (لا OUT — القيد الحارس الموجود بـreverse_opening_inventory
# يرفض العكس لو وُجدت حركة OUT بتاريخ >= opening_date، فلا نصطدم به هنا).
purchase(s, item, wh, 5, 11, DAY20, "COV-ROPN-LATER")
reverse_opening_inventory(s, opening_entry, reversal_date=DAY2)
s.commit()
m = latest_movement(s, item.id, wh.id, "opening_inventory_reverse")
ev, rc = candidate_for_movement(s, m.id)
check("1.9 Coverage — reverse_opening_inventory يُطلِق الاكتشاف",
      ev is not None and ev.status == CorrectionEventStatus.CANDIDATE
      and rc.component_type == RootCauseComponentType.NEW_MOVEMENT)

# ===========================================================================
# 2) NEGATIVE — حركة بلا شقيقة لاحقة التاريخ لا تُنتج CorrectionEvent
# ===========================================================================
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "NEG")
purchase(s, item, wh, 10, 10, DAY1, "NEG-P1")
purchase(s, item, wh, 5, 12, DAY2, "NEG-P2")                  # أحدث من P1، لا أقدم — لا Candidate لأي منهما
count_events = s.query(CorrectionEvent).count()
check("2. Negative — لا CorrectionEvent إطلاقاً بلا حركة لاحقة التاريخ فعلياً",
      count_events == 0, f"عدد CorrectionEvent الفعلي={count_events}")

# ===========================================================================
# 3) SAME-FLUSH — حركتان من نفس الـflush، نفس (item, warehouse)، تاريخان
#    مختلفان — يجب ألا تُبلِّغ إحداهما عن الأخرى (حماية أشقاء مستند واحد)
# ===========================================================================
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "SFL")
early_sibling = InventoryMovement(
    item_id=item.id, warehouse_id=wh.id, direction=MovementDirection.IN,
    quantity=D_("10"), unit_cost=D_("10"), movement_date=DAY1,
    source_type="manual_test", source_id=None,
)
late_sibling = InventoryMovement(
    item_id=item.id, warehouse_id=wh.id, direction=MovementDirection.IN,
    quantity=D_("5"), unit_cost=D_("11"), movement_date=DAY20,
    source_type="manual_test", source_id=None,
)
s.add_all([early_sibling, late_sibling])
s.commit()  # flush واحد فقط يحمل الاثنين معاً
ev_early, _ = candidate_for_movement(s, early_sibling.id)
ev_late, _ = candidate_for_movement(s, late_sibling.id)
check("3. Same-flush — الشقيقان لا يُبلِّغ أحدهما عن الآخر",
      ev_early is None and ev_late is None,
      f"early={ev_early}, late={ev_late}")

# تحقق إضافي: بعد هذا الـflush، لو أُضيفت حركة *جديدة* أقدم من كليهما
# بـflush منفصل لاحق، يجب أن تُكتشَف بشكل طبيعي (الاستثناء يخص نفس الـflush
# فقط، لا كل الحركات الأحدث تاريخاً للأبد).
later_check_movement = InventoryMovement(
    item_id=item.id, warehouse_id=wh.id, direction=MovementDirection.IN,
    quantity=D_("1"), unit_cost=D_("9"), movement_date=datetime.date(2025, 12, 1),
    source_type="manual_test", source_id=None,
)
s.add(later_check_movement)
s.commit()
ev_post, _ = candidate_for_movement(s, later_check_movement.id)
check("3b. Same-flush — الاستثناء يخص نفس الـflush فقط، لا يمتد لاحقاً",
      ev_post is not None and ev_post.status == CorrectionEventStatus.CANDIDATE)

# ===========================================================================
# 4) REVERSAL — cancel_date بأثر رجعي يُفعِّل نفس الآلية بالضبط
# ===========================================================================
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "REV")
to_cancel = purchase(s, item, wh, 20, 10, DAY1, "REV-P")
purchase(s, item, wh, 5, 11, DAY20, "REV-LATER")               # الحركة اللاحقة الموجودة سلفاً
cancel_invoice(s, to_cancel, cancel_date=DAY2)                  # عكس بأثر رجعي (لكن ≥ تاريخ الأصل)
s.commit()
reversal_movement = latest_movement(s, item.id, wh.id, "invoice_cancel")
ev, rc = candidate_for_movement(s, reversal_movement.id)
check("4. Reversal — cancel_date بأثر رجعي يُكتشَف بنفس آلية الحركات العادية",
      ev is not None and ev.status == CorrectionEventStatus.CANDIDATE
      and rc.component_type == RootCauseComponentType.REVERSAL
      and rc.source_type == "inventory_movement" and rc.source_id == reversal_movement.id)

print()
print(f"Detection (3B-5-E) test summary: {sum(1 for r in results if r[0]=='PASS')}/{len(results)} PASS")
failed = [r for r in results if r[0] == "FAIL"]
if failed:
    print("FAILURES:")
    for r in failed:
        print(" -", r[1], r[2])
    sys.exit(1)
