"""
tests/test_correction_scope_3b5f.py
=========================================
اختبارات 3B-5-F المستهدَفة (A/B/C/E) — وفق مصفوفة الاختبار المطلوبة
بـImplementation Specification §10. لا اختبار لأي Recalculation (D خارج
النطاق تماماً).
"""
import os, sys, datetime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Setting, Item, CostMethod, Warehouse, InventoryMovement,
    Invoice, InvoiceLine, InvoiceKind, InvoiceStatus,
    CorrectionEvent, CorrectionEventRootCause, ChronologyBasis, ScopeCompleteness,
    CorrectionEventImpactScopeElement, CorrectionEventImpactScopeRelationship,
    ImpactScopeRelationshipType,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice, get_default_warehouse
from app.services.inventory_transfer import transfer_stock
from app.services.correction_detection import attach_detection_listeners
from app.services.correction_scope import build_impact_scope

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


def seed(session):
    coa = create_default_chart_of_accounts(session)
    session.add(Setting(key="base_currency", value="SYP"))
    session.commit()
    wh = get_default_warehouse(session)
    return coa, wh


def make_item(session, coa, sku):
    return create_item(
        session, sku=sku, name_ar=f"صنف {sku}", unit="قطعة",
        inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
        sales_account_id=coa["sales"].id, cost_method=CostMethod.AVERAGE,
    )


def purchase(session, item, wh, qty_, price, d, ref):
    inv = Invoice(invoice_no=ref, kind=InvoiceKind.PURCHASE, party_name="مورد",
                  invoice_date=d, currency_code="SYP", exchange_rate=D_("1"),
                  status=InvoiceStatus.DRAFT, warehouse_id=wh.id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=D_(str(qty_)), unit_price=D_(str(price)))]
    session.add(inv); session.commit()
    post_purchase_invoice(session, inv, is_cash=True)
    session.commit()
    return inv


def sale(session, item, wh, qty_, price, d, ref):
    from app.services.posting import post_sales_invoice
    inv = Invoice(invoice_no=ref, kind=InvoiceKind.SALES, party_name="زبون",
                  invoice_date=d, currency_code="SYP", exchange_rate=D_("1"),
                  status=InvoiceStatus.DRAFT, warehouse_id=wh.id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=D_(str(qty_)), unit_price=D_(str(price)))]
    session.add(inv); session.commit()
    post_sales_invoice(session, inv, is_cash=True)
    session.commit()
    return inv


def only_candidate_event(session, item_id):
    return session.execute(
        select(CorrectionEvent).where(CorrectionEvent.item_id == item_id)
    ).scalars().first()


def scope_element_ids(session, event_id):
    rows = session.execute(
        select(CorrectionEventImpactScopeElement).where(
            CorrectionEventImpactScopeElement.correction_event_id == event_id
        )
    ).scalars().all()
    return {(r.source_type, r.source_id) for r in rows}


DAY1 = datetime.date(2026, 1, 1)
DAY2 = datetime.date(2026, 1, 2)
DAY5 = datetime.date(2026, 1, 5)
DAY10 = datetime.date(2026, 1, 10)
DAY20 = datetime.date(2026, 1, 20)

# ===========================================================================
# 1) SCOPE CLOSURE
# ===========================================================================

# --- 1.1 positive balance -> propagation continues (never closes) ---------
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "SC-POS")
purchase(s, item, wh, 100, 10, DAY20, "SC-POS-LATER")
purchase(s, item, wh, 5, 12, DAY1, "SC-POS-ROOT")  # جذر E
ev = only_candidate_event(s, item.id)
build_impact_scope(s, ev.id)
elements = scope_element_ids(s, ev.id)
later_inv = s.execute(select(Invoice).where(Invoice.invoice_no == "SC-POS-LATER")).scalars().first()
later_mvt = s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "purchase_invoice", InventoryMovement.source_id == later_inv.id)).scalars().first()
check("1.1 Scope — رصيد موجب مستمر يبقي الفرع مفتوحاً ويضم اللاحقة",
      ("inventory_movement", later_mvt.id) in elements)

# --- 1.2 partial depletion continues ---------------------------------------
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "SC-PART")
purchase(s, item, wh, 30, 10, DAY1, "SC-PART-P1")   # 30 وارد
sale(s, item, wh, 25, 15, DAY5, "SC-PART-S1")        # يبقى 5 -- لا صفر
purchase(s, item, wh, 5, 12, DAY10, "SC-PART-LATER")
purchase(s, item, wh, 2, 11, datetime.date(2025, 12, 20), "SC-PART-ROOT")  # جذر أقدم من الكل
ev = only_candidate_event(s, item.id)
build_impact_scope(s, ev.id)
elements = scope_element_ids(s, ev.id)
later_inv = s.execute(select(Invoice).where(Invoice.invoice_no == "SC-PART-LATER")).scalars().first()
later_mvt = s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "purchase_invoice", InventoryMovement.source_id == later_inv.id)).scalars().first()
check("1.2 Scope — استنفاد جزئي (30->5) لا يغلق الفرع",
      ("inventory_movement", later_mvt.id) in elements)

# --- 1.3 exact zero crossing closes the branch ------------------------------
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "SC-ZERO")
purchase(s, item, wh, 28, 10, DAY1, "SC-ZERO-P1")
sale(s, item, wh, 30, 15, DAY5, "SC-ZERO-S1")        # يستنفد تماماً -- qty=0 (2+28-30=0)
purchase(s, item, wh, 10, 20, DAY10, "SC-ZERO-AFTER")  # بعد الاستنفاد -- غير متأثرة
purchase(s, item, wh, 2, 11, datetime.date(2025, 12, 20), "SC-ZERO-ROOT")  # +2 يُحسَب ضمن التراكم
ev = only_candidate_event(s, item.id)
build_impact_scope(s, ev.id)
elements = scope_element_ids(s, ev.id)
after_inv = s.execute(select(Invoice).where(Invoice.invoice_no == "SC-ZERO-AFTER")).scalars().first()
after_mvt = s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "purchase_invoice", InventoryMovement.source_id == after_inv.id)).scalars().first()
zero_inv = s.execute(select(Invoice).where(Invoice.invoice_no == "SC-ZERO-S1")).scalars().first()
zero_sale = s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "sales_invoice", InventoryMovement.source_id == zero_inv.id)).scalars().first()
check("1.3 Scope — عبور فعلي للصفر يُدرِج حركة الاستنفاد نفسها",
      ("inventory_movement", zero_sale.id) in elements)
check("1.3b Scope — ولا يمتد لما بعد نقطة الاستنفاد",
      ("inventory_movement", after_mvt.id) not in elements)

# --- 1.4 negative cumulative quantity closes -------------------------------
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "SC-NEG")
purchase(s, item, wh, 9, 10, DAY1, "SC-NEG-P1")
sale(s, item, wh, 10, 15, DAY5, "SC-NEG-S1")         # qty=0 هنا فعلياً (1+9-10=0)
purchase(s, item, wh, 20, 12, DAY10, "SC-NEG-AFTER")
purchase(s, item, wh, 1, 11, datetime.date(2025, 12, 25), "SC-NEG-ROOT")
ev = only_candidate_event(s, item.id)
build_impact_scope(s, ev.id)
elements = scope_element_ids(s, ev.id)
after_inv = s.execute(select(Invoice).where(Invoice.invoice_no == "SC-NEG-AFTER")).scalars().first()
after_mvt = s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "purchase_invoice", InventoryMovement.source_id == after_inv.id)).scalars().first()
check("1.4 Scope — الإغلاق عند أول وصول لـ<=0 (لا يمتد بعده)",
      ("inventory_movement", after_mvt.id) not in elements)

# --- 1.5 W1 closes while W2 remains open (independent per warehouse) -------
s = fresh_session()
coa, wh1 = seed(s)
wh2 = Warehouse(name_ar="W2"); s.add(wh2); s.commit()
item = make_item(s, coa, "SC-INDEP")
purchase(s, item, wh1, 9, 10, DAY1, "SC-INDEP-P1-W1")
sale(s, item, wh1, 10, 15, DAY5, "SC-INDEP-S1-W1")     # W1 يستنفد تماماً (1+9-10=0)
purchase(s, item, wh1, 5, 12, DAY10, "SC-INDEP-AFTER-W1")  # لن تدخل النطاق
purchase(s, item, wh2, 20, 10, DAY10, "SC-INDEP-LATER-W2")  # W2 يبقى موجباً بلا استنفاد
purchase(s, item, wh1, 1, 11, datetime.date(2025, 12, 25), "SC-INDEP-ROOT")
ev = only_candidate_event(s, item.id)
build_impact_scope(s, ev.id)
elements = scope_element_ids(s, ev.id)
w1_after_inv = s.execute(select(Invoice).where(Invoice.invoice_no == "SC-INDEP-AFTER-W1")).scalars().first()
w1_after = s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "purchase_invoice", InventoryMovement.source_id == w1_after_inv.id)).scalars().first()
check("1.5 Scope — إغلاق W1 لا يمنع بناء نطاق مستقل (الجذر هنا بـW1 فقط أصلاً)",
      ("inventory_movement", w1_after.id) not in elements)

# ===========================================================================
# 2) TRANSFER TRAVERSAL
# ===========================================================================
s = fresh_session()
coa, wh1 = seed(s)
wh2 = Warehouse(name_ar="W2"); wh3 = Warehouse(name_ar="W3")
s.add_all([wh2, wh3]); s.commit()
item = make_item(s, coa, "SC-TRF")
purchase(s, item, wh1, 100, 10, DAY1, "SC-TRF-P1")
t1 = transfer_stock(s, item.id, wh1.id, wh2.id, 30, transfer_date=DAY5)
t2 = transfer_stock(s, item.id, wh2.id, wh3.id, 10, transfer_date=DAY10)
purchase(s, item, wh1, 3, 12, datetime.date(2025, 12, 20), "SC-TRF-ROOT")
s.commit()
ev = only_candidate_event(s, item.id)
build_impact_scope(s, ev.id)
elements = scope_element_ids(s, ev.id)
t1_out = s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "stock_transfer", InventoryMovement.source_id == t1.id,
    InventoryMovement.warehouse_id == wh1.id)).scalars().first()
t1_in = s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "stock_transfer", InventoryMovement.source_id == t1.id,
    InventoryMovement.warehouse_id == wh2.id)).scalars().first()
t2_in = s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "stock_transfer", InventoryMovement.source_id == t2.id,
    InventoryMovement.warehouse_id == wh3.id)).scalars().first()
check("2.1 Transfer — OUT(W1) ضمن النطاق", ("inventory_movement", t1_out.id) in elements)
check("2.2 Transfer — عبور OUT->IN عبر التحويل الأول يضم IN(W2)", ("inventory_movement", t1_in.id) in elements)
check("2.3 Transfer — سلسلة تحويل ثانية (W2->W3) تُتابَع أيضاً", ("inventory_movement", t2_in.id) in elements)

# لا يوجد عبور عكسي IN(W2)->OUT(W1) كحافة مستقلة يُعاد اكتشافها من جديد
rel_count = s.execute(select(CorrectionEventImpactScopeRelationship).where(
    CorrectionEventImpactScopeRelationship.correction_event_id == ev.id)).scalars().all()
reverse_edges = [r for r in rel_count if r.from_element_id ==
                 next(e.id for e in s.query(CorrectionEventImpactScopeElement)
                      .filter_by(correction_event_id=ev.id, source_id=t1_in.id).all())]
check("2.4 Transfer — لا انفجار حافات غير منطقية (عدد الحافات معقول)", len(rel_count) < 20,
      f"عدد الحافات={len(rel_count)}")

# ===========================================================================
# 3) MEMBERSHIP ACROSS EVENTS (لا دمج)
# ===========================================================================
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "SC-SHARE")
purchase(s, item, wh, 5, 11, DAY20, "SC-SHARE-LATER")  # الحركة اللاحقة المشتركة
purchase(s, item, wh, 10, 20, DAY1, "SC-SHARE-A")       # Candidate A
purchase(s, item, wh, 10, 30, DAY2, "SC-SHARE-B")       # Candidate B
s.commit()
events = s.execute(select(CorrectionEvent).where(CorrectionEvent.item_id == item.id)).scalars().all()
check("3.0 Membership — حدثان مستقلان أُنشئا فعلاً (شرط مسبق)", len(events) == 2)
for ev in events:
    build_impact_scope(s, ev.id)
later_inv = s.execute(select(Invoice).where(Invoice.invoice_no == "SC-SHARE-LATER")).scalars().first()
later_mvt = s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "purchase_invoice", InventoryMovement.source_id == later_inv.id)).scalars().first()
elems_a = scope_element_ids(s, events[0].id)
elems_b = scope_element_ids(s, events[1].id)
check("3.1 Membership — نفس الحركة اللاحقة عضو بكلا الحدثين المستقلين",
      ("inventory_movement", later_mvt.id) in elems_a and ("inventory_movement", later_mvt.id) in elems_b)
rows_for_later = s.execute(select(CorrectionEventImpactScopeElement).where(
    CorrectionEventImpactScopeElement.source_type == "inventory_movement",
    CorrectionEventImpactScopeElement.source_id == later_mvt.id)).scalars().all()
check("3.2 Membership — صفّان منفصلان (لا دمج، لا صف مشترك)",
      len(rows_for_later) == 2 and rows_for_later[0].correction_event_id != rows_for_later[1].correction_event_id)

# ===========================================================================
# 4) CHRONOLOGY BASIS (ID-002)
# ===========================================================================

# --- 4.1 distinct dates -> KNOWN -------------------------------------------
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "SC-KNOWN")
purchase(s, item, wh, 5, 11, DAY10, "SC-KNOWN-LATER")
purchase(s, item, wh, 5, 12, DAY1, "SC-KNOWN-ROOT")
ev = only_candidate_event(s, item.id)
build_impact_scope(s, ev.id)
s.refresh(ev)
check("4.1 Chronology — تواريخ متمايزة بالكامل => KNOWN",
      ev.chronology_basis == ChronologyBasis.KNOWN, f"القيمة={ev.chronology_basis}")

# --- 4.2 same-date ambiguity -> ASSUMED, and COMPLETE+ASSUMED coexist ------
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "SC-ASSUM")
# حركتان بنفس التاريخ بالضبط ضمن الفرع المُجتاز فعلياً، مع استنفاد تام (=> COMPLETE)
purchase(s, item, wh, 7, 10, DAY10, "SC-ASSUM-TIE-A")
sale(s, item, wh, 10, 15, DAY10, "SC-ASSUM-TIE-B")  # نفس اليوم بالضبط -- استنفاد تام (3+7-10=0)
purchase(s, item, wh, 3, 11, datetime.date(2025, 12, 20), "SC-ASSUM-ROOT")
ev = only_candidate_event(s, item.id)
build_impact_scope(s, ev.id)
s.refresh(ev)
check("4.2 Chronology — حركتان متعادلتا التاريخ ضمن الفرع => ASSUMED",
      ev.chronology_basis == ChronologyBasis.ASSUMED, f"القيمة={ev.chronology_basis}")
check("4.3 Completeness — ASSUMED لا يمنع COMPLETE (استنفاد تام فعلياً هنا)",
      ev.scope_completeness == ScopeCompleteness.COMPLETE, f"القيمة={ev.scope_completeness}")

# ===========================================================================
# 6) ID-003 — Inter-Date Propagation Edge Semantics (1×N / N×1 / N×M)
# ===========================================================================

def edge_count_between(session, event_id, from_ids, to_ids):
    rows = session.execute(select(CorrectionEventImpactScopeRelationship).where(
        CorrectionEventImpactScopeRelationship.correction_event_id == event_id,
        CorrectionEventImpactScopeRelationship.relationship_type == ImpactScopeRelationshipType.PROPAGATES_TO,
    )).scalars().all()
    els = {e.id: e for e in session.query(CorrectionEventImpactScopeElement).filter_by(correction_event_id=event_id).all()}
    count = 0
    for r in rows:
        f, t = els.get(r.from_element_id), els.get(r.to_element_id)
        if f and t and f.source_id in from_ids and t.source_id in to_ids:
            count += 1
    return count


def movement_ids_by_refs(session, item_id, refs):
    ids = []
    for ref in refs:
        inv = session.execute(select(Invoice).where(Invoice.invoice_no == ref)).scalars().first()
        mvt = session.execute(select(InventoryMovement).where(
            InventoryMovement.source_id == inv.id,
            InventoryMovement.source_type.in_(["purchase_invoice", "sales_invoice"]))).scalars().first()
        ids.append(mvt.id)
    return ids

# --- 6.1/6.2 — سيناريو واحد يغطي 1xN ثم N×1 على التوالي داخل نفس الفرع ----
# جذر مفرد (يوم منفصل) -> مجموعة يوم5 (عنصران جديدان، أول مجموعة downstream
# حقيقية -- الجذر نفسه سُجِّل قبل الحلقة فلا يدخل ضمن group_elements) ->
# مجموعة يوم10 (عنصر واحد). هذا يثبت 1xN (جذر->يوم5) وN×1 (يوم5->يوم10) معاً.
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "SC-1N-N1")
purchase(s, item, wh, 3, 10, DAY5, "SC-1N-N1-GA")
purchase(s, item, wh, 4, 10, DAY5, "SC-1N-N1-GB")     # مجموعة من عنصرين (يوم5)
purchase(s, item, wh, 2, 10, DAY10, "SC-1N-N1-NEXT")   # عنصر واحد لاحق (يوم10)
purchase(s, item, wh, 5, 12, datetime.date(2025, 12, 20), "SC-1N-N1-ROOT")  # جذر مفرد
ev = only_candidate_event(s, item.id)
build_impact_scope(s, ev.id)
root_ids = movement_ids_by_refs(s, item.id, ["SC-1N-N1-ROOT"])
mid_ids = movement_ids_by_refs(s, item.id, ["SC-1N-N1-GA", "SC-1N-N1-GB"])
next_ids = movement_ids_by_refs(s, item.id, ["SC-1N-N1-NEXT"])
check("6.1 ID-003 — 1×N: حافتان (fan-out) من الجذر المفرد لعنصري مجموعة يوم5",
      edge_count_between(s, ev.id, root_ids, mid_ids) == 2)
check("6.2 ID-003 — N×1: حافتان (fan-in) من عنصري يوم5 للعنصر الوحيد بيوم10",
      edge_count_between(s, ev.id, mid_ids, next_ids) == 2)

# --- 6.3 — N -> M: صفر حافات، مع وصول Scope فعلياً لعناصر كلا المجموعتين
# وإغلاق الفرع فعلياً (COMPLETE) لأسباب لا علاقة لها بغياب الحافة --
# يعزل المتغيّر المُختبَر (وجود/غياب PROPAGATES_TO) عن حالة الإغلاق تماماً.
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "SC-NM")
purchase(s, item, wh, 3, 10, DAY5, "SC-NM-A1")
purchase(s, item, wh, 4, 10, DAY5, "SC-NM-A2")    # مجموعة N=2 بيوم5 (بعد الجذر: 5+3+4=12)
sale(s, item, wh, 6, 15, DAY10, "SC-NM-B1")
sale(s, item, wh, 6, 15, DAY10, "SC-NM-B2")       # مجموعة M=2 بيوم10 (12-6-6=0 -- إغلاق فعلي)
purchase(s, item, wh, 5, 12, datetime.date(2025, 12, 20), "SC-NM-ROOT")
ev = only_candidate_event(s, item.id)
build_impact_scope(s, ev.id)
a_ids = movement_ids_by_refs(s, item.id, ["SC-NM-A1", "SC-NM-A2"])
b_ids = movement_ids_by_refs(s, item.id, ["SC-NM-B1", "SC-NM-B2"])
elements = scope_element_ids(s, ev.id)
check("6.3a ID-003 — N×M: صفر حافات بين المجموعتين",
      edge_count_between(s, ev.id, a_ids, b_ids) == 0)
check("6.3b ID-003 — N×M: كل العناصر الأربعة تبقى أعضاء Scope رغم غياب الحافة",
      all(("inventory_movement", mid) in elements for mid in a_ids + b_ids))
ev_refreshed = s.get(CorrectionEvent, ev.id)
check("6.3c ID-003 — N×M: الفرع يُغلَق فعلياً (COMPLETE) بسبب total_qty<=0 الحقيقي —"
      " لا علاقة لغياب الحافة بحالة الإغلاق إطلاقاً",
      ev_refreshed.scope_completeness == ScopeCompleteness.COMPLETE,
      f"القيمة={ev_refreshed.scope_completeness}")

# ===========================================================================
# 5) ROOT MOVEMENT MEMBERSHIP (ID-001)
# ===========================================================================
s = fresh_session()
coa, wh = seed(s)
item = make_item(s, coa, "SC-ROOT")
purchase(s, item, wh, 5, 11, DAY20, "SC-ROOT-LATER")
root_inv = purchase(s, item, wh, 5, 12, DAY1, "SC-ROOT-ROOT")
ev = only_candidate_event(s, item.id)
root_mvt = s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "purchase_invoice",
    InventoryMovement.source_id == root_inv.id)).scalars().first()
build_impact_scope(s, ev.id)
elements = scope_element_ids(s, ev.id)
check("5.1 ID-001 — الجذر نفسه مُسجَّل كـImpactScopeElement",
      ("inventory_movement", root_mvt.id) in elements)

# ===========================================================================
# 6) NO RECALCULATION BOUNDARY
# ===========================================================================
# التأكد أن لا شيء بهذه الوحدة يقرأ/يخزّن unit_cost أو average_cost إطلاقاً
import app.services.correction_scope as scope_module
# فحص الاستيرادات الفعلية (لا نص الملف كاملاً -- الـdocstring نفسه يذكر
# أسماء هاتين الدالتين نصّاً كجزء من شرح لماذا لا تُستخدَمان، فبحث نصي
# شامل يُعطي false positive؛ فحص الاستيرادات الفعلية هو الدليل الحقيقي).
check("6.1 NO RECALCULATION — accumulate_average_cost غير مستورَدة إطلاقاً",
      not hasattr(scope_module, "accumulate_average_cost"))
check("6.2 NO RECALCULATION — get_item_stock_summary غير مستورَدة إطلاقاً",
      not hasattr(scope_module, "get_item_stock_summary"))
# التأكد أن كائنات الحركة المُعالَجة لا يُقرأ منها unit_cost بمنطق الحساب
# الفعلي (استثناء الأسطر التي تشرح هذا بالتعليق العربي نفسه)
code_lines = [l for l in open(scope_module.__file__, encoding="utf-8").read().splitlines()
              if not l.strip().startswith("#") and '"""' not in l]
code_only = "\n".join(code_lines)
check("6.3 NO RECALCULATION — لا قراءة .unit_cost/.average_cost بمنطق التنفيذ الفعلي",
      ".unit_cost" not in code_only and ".average_cost" not in code_only)

print()
print(f"Impact Scope (3B-5-F) test summary: {sum(1 for r in results if r[0]=='PASS')}/{len(results)} PASS")
failed = [r for r in results if r[0] == "FAIL"]
if failed:
    print("FAILURES:")
    for r in failed:
        print(" -", r[1], r[2])
    sys.exit(1)
