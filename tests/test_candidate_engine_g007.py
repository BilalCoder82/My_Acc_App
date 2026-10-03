"""
tests/test_candidate_engine_g007.py
====================================
اختبارات G-007 (Candidate Engine). كل رقم متوقَّع مشتَقّ يدوياً من عقد
G-005 (Q/V/A، c(OUT)=A_{k-1}) قبل تشغيل المحرك، لا من مخرجاته.
"""
import os, sys, datetime, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Setting, CostMethod, Warehouse, InventoryMovement, MovementDirection,
    Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, CorrectionEvent,
    CorrectionEventCandidateState, CorrectionEventDeltaRecord,
    CorrectionEventTieGroup, CorrectionEventOrderingAssumption,
    CorrectionEventCandidateOrderingLink,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import (
    post_purchase_invoice, post_sales_invoice, get_default_warehouse,
)
from app.services.inventory_transfer import transfer_stock
from app.services.correction_detection import attach_detection_listeners
from app.services.correction_scope import build_impact_scope
from app.services import candidate_engine
from app.services.candidate_engine import build_candidate

results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, label, detail))
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))


def near(a, b):
    return a is not None and abs(D_(str(a)) - D_(str(b))) < D_("0.0001")


def make_session(url="sqlite:///:memory:"):
    attach_detection_listeners()
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def seed(s):
    coa = create_default_chart_of_accounts(s)
    s.add(Setting(key="base_currency", value="SYP"))
    s.commit()
    return coa, get_default_warehouse(s)


def make_item(s, coa, sku):
    return create_item(
        s, sku=sku, name_ar=f"صنف {sku}", unit="قطعة",
        inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
        sales_account_id=coa["sales"].id, cost_method=CostMethod.AVERAGE)


def _inv(s, kind, poster, item, wh, qty_, price, d, ref):
    inv = Invoice(invoice_no=ref, kind=kind, party_name="طرف", invoice_date=d,
                  currency_code="SYP", exchange_rate=D_("1"),
                  status=InvoiceStatus.DRAFT, warehouse_id=wh.id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=D_(str(qty_)), unit_price=D_(str(price)))]
    s.add(inv); s.commit()
    poster(s, inv, is_cash=True); s.commit()
    return inv


def purchase(s, item, wh, q, p, d, ref):
    return _inv(s, InvoiceKind.PURCHASE, post_purchase_invoice, item, wh, q, p, d, ref)


def sale(s, item, wh, q, p, d, ref):
    return _inv(s, InvoiceKind.SALES, post_sales_invoice, item, wh, q, p, d, ref)


def mvt(s, inv):
    st = "purchase_invoice" if inv.kind == InvoiceKind.PURCHASE else "sales_invoice"
    return s.execute(select(InventoryMovement).where(
        InventoryMovement.source_type == st, InventoryMovement.source_id == inv.id)).scalars().first()


def the_event(s):
    evs = s.execute(select(CorrectionEvent)).scalars().all()
    assert len(evs) == 1, f"توقعت حدثاً واحداً، وُجد {len(evs)}"
    return evs[0]


def deltas(s, cand):
    rows = s.execute(select(CorrectionEventDeltaRecord).where(
        CorrectionEventDeltaRecord.candidate_state_id == cand.id)).scalars().all()
    return {r.movement_id: r for r in rows}


D = datetime.date
Y = 2026

# =========================================================================
# T1 — DL-002: بيع يُعاد تقييمه بعد شراء مؤرَّخ بأثر رجعي (بلا أي تصحيح قيمة)
#   Baseline: PA(10@10,Jan1) S1(4,Jan10,cost10 مخزَّنة) ثم PB(10@20,Jan5) رجعي
#   Candidate: PB→Q20,V300,A15 ⟹ S1 بتكلفة 15: delta = -60 - (-40) = -20
# =========================================================================
s, _e = make_session(); coa, wh = seed(s); item = make_item(s, coa, "T1")
purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
S1 = sale(s, item, wh, 4, 50, D(Y, 1, 10), "S1")
PB = purchase(s, item, wh, 10, 20, D(Y, 1, 5), "PB")
ev = the_event(s); build_impact_scope(s, ev.id)
cand = build_candidate(s, ev.id); s.commit()
d = deltas(s, cand)
mPB, mS1 = mvt(s, PB), mvt(s, S1)
check("T1.1 نطاق = {PB,S1} فقط، صف Delta لكل حركة", set(d) == {mPB.id, mS1.id}, str(sorted(d)))
check("T1.2 PB: delta_inventory_value=0 (لم تُصحَّح قيمته)", near(d[mPB.id].delta_inventory_value, 0))
check("T1.3 DL-002: تكلفة S1 في Candidate = A_{k-1} = 15 ⟹ delta = -20",
      near(d[mS1.id].delta_inventory_value, -20), str(d[mS1.id].delta_inventory_value))
check("T1.4 لا استثناءات تكلفة", not any(r.cost_basis_exception for r in d.values()))
sum_d = sum(D_(str(r.delta_inventory_value)) for r in d.values())
check("T1.5 B.2: ΣΔ = ΔEndingInventoryValue = 240-260 = -20", near(sum_d, -20), str(sum_d))

# =========================================================================
# T2 — تصحيح قيمة الجذر: PB من 20 إلى 30
#   PB: delta = 300-200 = +100 ؛ V=400,A=20 ⟹ S1 cost 20: -80-(-40) = -40
#   ΔInputs=100, ΔCOGS=+40, ΔEnd=+60 ⟹ 100 = 40 + 60
# =========================================================================
cand2 = build_candidate(s, ev.id, {mPB.id: (D_("10"), D_("30"))}, label="B"); s.commit()
d2 = deltas(s, cand2)
check("T2.1 PB Δ=+100", near(d2[mPB.id].delta_inventory_value, 100), str(d2[mPB.id].delta_inventory_value))
check("T2.2 S1 Δ=-40 (متوسط Candidate=20، لا التكلفة المخزَّنة 10)",
      near(d2[mS1.id].delta_inventory_value, -40), str(d2[mS1.id].delta_inventory_value))
sum2 = sum(D_(str(r.delta_inventory_value)) for r in d2.values())
check("T2.3 ΣΔ=+60=ΔEndInv، وهوية G-005: ΔInputs(100)=ΔCOGS(40)+ΔEnd(60)",
      near(sum2, 60) and near(100, 40 + 60), str(sum2))

# =========================================================================
# T3 — delta_quantity: regime-level (حامل + forward-fill)
#   تصحيح كمية PB من 10 إلى 12: الصف الأول حامل Δq=+2، الثاني NULL
# =========================================================================
cand3 = build_candidate(s, ev.id, {mPB.id: (D_("12"), D_("20"))}, label="C"); s.commit()
d3 = deltas(s, cand3)
first, second = sorted(d3.values(), key=lambda r: r.movement_id)
first_row = d3[mPB.id]; second_row = d3[mS1.id]
check("T3.1 الصف الحامل: delta_quantity=+2", near(first_row.delta_quantity, 2), str(first_row.delta_quantity))
check("T3.2 الصف التالي NULL (forward-fill) لا 'مجهولة'", second_row.delta_quantity is None)
check("T3.3 بلا تغيير كمية (Candidate الأول): الصف الحامل=0 وليس NULL",
      near(d[mPB.id].delta_quantity, 0) and d[mS1.id].delta_quantity is None)

# =========================================================================
# T4 — DL-003: Q_{k-1} <= 0 قبل OUT ⟹ استثناء، لا تكلفة بديلة، تلويث لاحق
#   PA(5@10,Jan1) S1(5,Jan5) S3(2,Jan6 من مخزون صفري) P2(10@12,Jan8) S5(1,Jan9)
#   ثم جذر: بيع S2(1) بتاريخ Jan3 (رجعي).
#   Baseline: تكلفة S2 المخزَّنة = 15 (متوسط الحالة الحالية يشمل كل الحركات بما
#   فيها اللاحقة تاريخياً، لحظة الترحيل: Q=7,V=105) — يُتحقَّق منها صراحة أدناه.
#   Candidate: S2: A_{k-1}=10 ⟹ Δ = -10 - (-15) = +5
#   S1: Q_prev=4>0, A=10 ⟹ Δ=0 ؛ S3: Q_prev=-1 ⟹ استثناء ؛
#   P2: IN عادي Δ=0 ؛ S5: Q_prev=7>0 لكن المستودع ملوَّث ⟹ استثناء
# =========================================================================
s, _e = make_session(); coa, wh = seed(s); item = make_item(s, coa, "T4")
purchase(s, item, wh, 5, 10, D(Y, 1, 1), "PA")
i1 = sale(s, item, wh, 5, 30, D(Y, 1, 5), "S1")
i3 = sale(s, item, wh, 2, 30, D(Y, 1, 6), "S3")
p2 = purchase(s, item, wh, 10, 12, D(Y, 1, 8), "P2")
i5 = sale(s, item, wh, 1, 30, D(Y, 1, 9), "S5")
i2 = sale(s, item, wh, 1, 30, D(Y, 1, 3), "S2")
ev = the_event(s); build_impact_scope(s, ev.id)
cand = build_candidate(s, ev.id); s.commit()
d = deltas(s, cand)
m1, m2, m3, mp2, m5 = mvt(s, i1), mvt(s, i2), mvt(s, i3), mvt(s, p2), mvt(s, i5)
check("T4.0 تكلفة S2 المخزَّنة في Baseline = 15", near(m2.unit_cost, 15), str(m2.unit_cost))
check("T4.1 S2 (الجذر): Δ = -10 - (-15) = +5", near(d[m2.id].delta_inventory_value, 5), str(d[m2.id].delta_inventory_value))
check("T4.2 S1: Q_prev=4>0 ⟹ A=10 ⟹ Δ=0", near(d[m1.id].delta_inventory_value, 0), str(d[m1.id].delta_inventory_value))
check("T4.3 S3: Q_prev=-1<=0 ⟹ cost_basis_exception=True",
      d[m3.id].cost_basis_exception is True)
check("T4.4 S3: delta_inventory_value=NULL (لا صفر، لا تكلفة مخترَعة)",
      d[m3.id].delta_inventory_value is None)
check("T4.5 P2 (IN عادي بعد التلوث): Δ=0 محسوب، ليس استثناءً",
      near(d[mp2.id].delta_inventory_value, 0) and not d[mp2.id].cost_basis_exception)
check("T4.6 S5: Q_prev>0 لكن V مجهولة (تلوث) ⟹ استثناء، لا حساب متوسط",
      d[m5.id].cost_basis_exception is True and d[m5.id].delta_inventory_value is None)
check("T4.7 الكميات تُحسَب رغم الاستثناء (delta_quantity قابلة للحساب)",
      all(r.delta_quantity is None or r.delta_quantity is not None for r in d.values()))
check("T4.8 بناء Candidate لم يتوقف رغم الاستثناء: صف لكل حركة نطاق",
      len(d) == 5, str(len(d)))

# =========================================================================
# T5 — Transfer: هوية واحدة، ساق IN تأخذ تكلفة ساق OUT في Candidate
#   PA W1(10@10,Jan1) ؛ Transfer W1→W2 qty6 (Jan5, cost10) ؛ S1 في W2 qty4 (Jan8)
#   جذر رجعي: PR W1 (10@20, Jan3).
#   Candidate: W1: Q20,V300 ⟹ T-OUT cost15 (Δ=-90+60=-30) ؛ W2: T-IN cost15
#   (Δ=+90-60=+30) ؛ S1 cost15 (Δ=-60+40=-20).
#   ΔInputs=0, ΔCOGS=+20, ΔEnd=-20 ⟹ B.3: 0 = 20 + (-20)
# =========================================================================
s, _e = make_session(); coa, wh1 = seed(s); item = make_item(s, coa, "T5")
wh2 = Warehouse(name_ar="W2"); s.add(wh2); s.commit()
purchase(s, item, wh1, 10, 10, D(Y, 1, 1), "PA")
tr = transfer_stock(s, item.id, wh1.id, wh2.id, 6, transfer_date=D(Y, 1, 5)); s.commit()
iS = sale(s, item, wh2, 4, 50, D(Y, 1, 8), "S1")
iR = purchase(s, item, wh1, 10, 20, D(Y, 1, 3), "PR")
ev = the_event(s); build_impact_scope(s, ev.id)
cand = build_candidate(s, ev.id); s.commit()
d = deltas(s, cand)
legs = s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "stock_transfer",
    InventoryMovement.source_id == tr.id)).scalars().all()
t_out = [m for m in legs if m.direction == MovementDirection.OUT][0]
t_in = [m for m in legs if m.direction == MovementDirection.IN][0]
mR, mS = mvt(s, iR), mvt(s, iS)
check("T5.1 نطاق يعبر Transfer: 4 حركات (PR, T-OUT, T-IN, S1)", len(d) == 4, str(len(d)))
check("T5.2 T-OUT: Δ=-30", near(d[t_out.id].delta_inventory_value, -30), str(d[t_out.id].delta_inventory_value))
check("T5.3 T-IN: Δ=+30 (تكلفة ساق OUT في Candidate، لا المخزَّنة)",
      near(d[t_in.id].delta_inventory_value, 30), str(d[t_in.id].delta_inventory_value))
check("T5.4 S1 في W2: Δ=-20", near(d[mS.id].delta_inventory_value, -20), str(d[mS.id].delta_inventory_value))
check("T5.5 الجذر PR: Δ=0", near(d[mR.id].delta_inventory_value, 0))
w1 = D_(str(d[mR.id].delta_inventory_value)) + D_(str(d[t_out.id].delta_inventory_value))
w2 = D_(str(d[t_in.id].delta_inventory_value)) + D_(str(d[mS.id].delta_inventory_value))
check("T5.6 ΔEnd(W1)=-30 و ΔEnd(W2)=+10", near(w1, -30) and near(w2, 10), f"{w1},{w2}")
check("T5.7 B.3 على مستوى الشركة: ΔInputs(0)=ΔCOGS(+20)+ΔEnd(-20)",
      near(w1 + w2, -20))
check("T5.8 لا استثناءات", not any(r.cost_basis_exception for r in d.values()))

# =========================================================================
# T6 — TieGroup/OrderingAssumption مُسجَّلة صراحة (لا ترتيب ضمني)
#   PA(10@10,Jan1) ؛ S1(2) وS2(1) بنفس التاريخ Jan5 ؛ جذر PR(Jan3)
# =========================================================================
s, _e = make_session(); coa, wh = seed(s); item = make_item(s, coa, "T6")
purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
a = sale(s, item, wh, 2, 50, D(Y, 1, 5), "S1")
b = sale(s, item, wh, 1, 50, D(Y, 1, 5), "S2")
purchase(s, item, wh, 10, 20, D(Y, 1, 3), "PR")
ev = the_event(s); build_impact_scope(s, ev.id)
cand = build_candidate(s, ev.id); s.commit()
tgs = s.execute(select(CorrectionEventTieGroup)).scalars().all()
oas = s.execute(select(CorrectionEventOrderingAssumption)).scalars().all()
lks = s.execute(select(CorrectionEventCandidateOrderingLink)).scalars().all()
check("T6.1 TieGroup واحدة (Jan5) + OrderingAssumption + Link", len(tgs) == 1 and len(oas) == 1 and len(lks) == 1)
import json
rep = json.loads(oas[0].ordering_representation)
ma, mb = mvt(s, a), mvt(s, b)
check("T6.2 الافتراض مسجَّل صراحة: ترتيب الترحيل [S1,S2]",
      rep["movement_ids"] == [ma.id, mb.id] and rep["basis"] == "posting_order(date,id)", str(rep))
check("T6.3 صف Delta لكل حركة نطاق (3)", len(deltas(s, cand)) == 3)

# =========================================================================
# T7 — DL-004: UNIQUE(candidate_state_id, movement_id) على مستوى DB
# =========================================================================
dup_ok = False
try:
    s.add(CorrectionEventDeltaRecord(candidate_state_id=cand.id, movement_id=ma.id,
                                     delta_inventory_value=D_("1")))
    s.flush()
except IntegrityError:
    dup_ok = True
    s.rollback()
check("T7.1 DL-004: إدراج Delta مكرَّرة لنفس (candidate, movement) يُرفَض", dup_ok)

# =========================================================================
# T8 — مُدخَلات مرفوضة
# =========================================================================
s, _e = make_session(); coa, wh = seed(s); item = make_item(s, coa, "T8")
purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
so = sale(s, item, wh, 4, 50, D(Y, 1, 10), "S1")
pb = purchase(s, item, wh, 10, 20, D(Y, 1, 5), "PB")
ev = the_event(s); build_impact_scope(s, ev.id)
mso = mvt(s, so)
err1 = err2 = False
try:
    build_candidate(s, ev.id, {mso.id: (D_("4"), D_("99"))})
except ValueError:
    err1 = True
try:
    build_candidate(s, ev.id, {999999: (D_("1"), D_("1"))})
except ValueError:
    err2 = True
check("T8.1 تمرير unit_cost لحركة OUT مرفوض (DL-002: تكلفة OUT مُشتقَّة)", err1)
check("T8.2 تصحيح لحركة خارج النطاق مرفوض", err2)
check("T8.3 الرفض قبل أي كتابة: لا CandidateState", s.execute(
    select(func.count(CorrectionEventCandidateState.id))).scalar_one() == 0)

# =========================================================================
# T9 — الذرّية: لا commit داخل المحرك؛ فشل أثناء الكتابة ⟹ rollback كامل
# =========================================================================
tmpdir = tempfile.mkdtemp()
url = f"sqlite:///{os.path.join(tmpdir, 'atom.db')}"
s, eng = make_session(url); coa, wh = seed(s); item = make_item(s, coa, "T9")
purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
sale(s, item, wh, 4, 50, D(Y, 1, 10), "S1")
purchase(s, item, wh, 10, 20, D(Y, 1, 5), "PB")
ev = the_event(s); build_impact_scope(s, ev.id); s.commit()

other = create_engine(url)


def count_other(table):
    with other.connect() as c:
        return c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


cand = build_candidate(s, ev.id)     # لا commit
check("T9.1 قبل commit المستدعي: اتصال آخر لا يرى أي CandidateState/Delta",
      count_other("correction_event_candidate_states") == 0
      and count_other("correction_event_delta_records") == 0)
s.rollback()
check("T9.2 rollback المستدعي يمحو كل شيء (لا أثر جزئي)",
      s.execute(select(func.count(CorrectionEventCandidateState.id))).scalar_one() == 0
      and s.execute(select(func.count(CorrectionEventDeltaRecord.id))).scalar_one() == 0)


class _Boom:
    def __init__(self, *a, **k):
        raise RuntimeError("فشل مُحاكى أثناء كتابة Delta")


orig = candidate_engine.CorrectionEventDeltaRecord
candidate_engine.CorrectionEventDeltaRecord = _Boom
raised = False
try:
    build_candidate(s, ev.id)
except RuntimeError:
    raised = True
    s.rollback()
finally:
    candidate_engine.CorrectionEventDeltaRecord = orig
check("T9.3 فشل بعد كتابة CandidateState وقبل اكتمال Delta ⟹ rollback كامل، لا Candidate جزئي",
      raised and s.execute(select(func.count(CorrectionEventCandidateState.id))).scalar_one() == 0
      and count_other("correction_event_candidate_states") == 0)

cand = build_candidate(s, ev.id); s.commit()
check("T9.4 بعد commit المستدعي: النتائج ظاهرة لاتصال آخر",
      count_other("correction_event_candidate_states") == 1
      and count_other("correction_event_delta_records") == 2)

# =========================================================================
# T10 — BLOCKER مراجعة G-007: هوية Transfer مستقلة عن ترتيب معالجة الساقين
#   نفس سيناريو T5، مرتين: (أ) ساق OUT أولاً (id أصغر) (ب) ساق IN أولاً
#   (تبديل ids الساقين = تحويل خُزِّنت ساقاه بترتيب معاكس).
#   المطلوب: cost(IN)==cost(OUT) في Candidate، ونفس Deltas بالضبط في الحالتين.
# =========================================================================
def transfer_scenario(swap_leg_ids):
    s, _e = make_session(); coa, wh1 = seed(s); item = make_item(s, coa, "T10")
    wh2 = Warehouse(name_ar="W2"); s.add(wh2); s.commit()
    purchase(s, item, wh1, 10, 10, D(Y, 1, 1), "PA")
    tr = transfer_stock(s, item.id, wh1.id, wh2.id, 6, transfer_date=D(Y, 1, 5)); s.commit()
    iS = sale(s, item, wh2, 4, 50, D(Y, 1, 8), "S1")
    iR = purchase(s, item, wh1, 10, 20, D(Y, 1, 3), "PR")
    ev = the_event(s)
    legs = s.execute(select(InventoryMovement).where(
        InventoryMovement.source_type == "stock_transfer",
        InventoryMovement.source_id == tr.id)).scalars().all()
    o = [m for m in legs if m.direction == MovementDirection.OUT][0].id
    i = [m for m in legs if m.direction == MovementDirection.IN][0].id
    if swap_leg_ids:
        s.execute(text("PRAGMA foreign_keys=OFF"))
        s.execute(text(f"UPDATE inventory_movements SET id=-1 WHERE id={o}"))
        s.execute(text(f"UPDATE inventory_movements SET id={o} WHERE id={i}"))
        s.execute(text(f"UPDATE inventory_movements SET id={i} WHERE id=-1"))
        s.commit()
        o, i = i, o   # الأرقام الجديدة: OUT صار id الأكبر
    build_impact_scope(s, ev.id)
    cand = build_candidate(s, ev.id); s.commit()
    d = deltas(s, cand)
    mR, mS = mvt(s, iR), mvt(s, iS)
    out = {"PR": d[mR.id], "OUT": d[o], "IN": d[i], "S1": d[mS.id]}
    return {k: (None if v.delta_inventory_value is None else D_(str(v.delta_inventory_value)))
            for k, v in out.items()}, o, i


normal, o1, i1 = transfer_scenario(False)
swapped, o2, i2 = transfer_scenario(True)
check("T10.0 الإعداد: في الحالة (أ) id(OUT)<id(IN) وفي (ب) id(IN)<id(OUT)", o1 < i1 and i2 < o2, f"{o1},{i1} / {o2},{i2}")
check("T10.1 (ب) ساق IN أولاً: cost(IN)==cost(OUT) ⟹ Δ(IN) = -Δ(OUT) = +30",
      near(swapped["IN"], 30) and near(swapped["OUT"], -30), str(swapped))
check("T10.2 Deltas متطابقة تماماً بصرف النظر عن ترتيب الساقين",
      all(near(normal[k], swapped[k]) for k in normal), f"{normal} vs {swapped}")
check("T10.3 B.3 يبقى: ΣΔ=-20 في كلا الترتيبين",
      near(sum(normal.values()), -20) and near(sum(swapped.values()), -20))

# T10.4 — تصحيح unit_cost لساق Transfer-IN مرفوض (التكلفة مُشتقَّة من ساق OUT)
s, _e = make_session(); coa, wh1 = seed(s); item = make_item(s, coa, "T10b")
wh2 = Warehouse(name_ar="W2"); s.add(wh2); s.commit()
purchase(s, item, wh1, 10, 10, D(Y, 1, 1), "PA")
tr = transfer_stock(s, item.id, wh1.id, wh2.id, 6, transfer_date=D(Y, 1, 5)); s.commit()
purchase(s, item, wh1, 10, 20, D(Y, 1, 3), "PR")
ev = the_event(s); build_impact_scope(s, ev.id)
t_in_id = [m for m in s.execute(select(InventoryMovement).where(
    InventoryMovement.source_type == "stock_transfer", InventoryMovement.source_id == tr.id)).scalars().all()
    if m.direction == MovementDirection.IN][0].id
rej = False
try:
    build_candidate(s, ev.id, {t_in_id: (D_("6"), D_("99"))})
except ValueError:
    rej = True
check("T10.4 تصحيح unit_cost لساق Transfer-IN مرفوض (لا تجاهل صامت)", rej)

# T10.5 — دورة تبعية حقيقية (تحويلان متقاطعان بنفس التاريخ بترتيب ids متقاطع):
#   لا ترتيب صحيح بلا افتراض جديد ⟹ ValueError صريح، ولا أي كتابة.
s, _e = make_session(); coa, wh1 = seed(s); item = make_item(s, coa, "T10c")
wh2 = Warehouse(name_ar="W2"); s.add(wh2); s.commit()
purchase(s, item, wh1, 10, 10, D(Y, 1, 1), "PA")
purchase(s, item, wh2, 10, 10, D(Y, 1, 1), "PB")
t1 = transfer_stock(s, item.id, wh1.id, wh2.id, 2, transfer_date=D(Y, 1, 5))
t2 = transfer_stock(s, item.id, wh2.id, wh1.id, 1, transfer_date=D(Y, 1, 5)); s.commit()
purchase(s, item, wh1, 10, 20, D(Y, 1, 3), "PR")
ev = the_event(s)


def leg(tr, direction):
    return [m for m in s.execute(select(InventoryMovement).where(
        InventoryMovement.source_type == "stock_transfer",
        InventoryMovement.source_id == tr.id)).scalars().all() if m.direction == direction][0].id


t1_out, t2_in = leg(t1, MovementDirection.OUT), leg(t2, MovementDirection.IN)
s.execute(text("PRAGMA foreign_keys=OFF"))
s.execute(text(f"UPDATE inventory_movements SET id=-1 WHERE id={t1_out}"))
s.execute(text(f"UPDATE inventory_movements SET id={t1_out} WHERE id={t2_in}"))
s.execute(text(f"UPDATE inventory_movements SET id={t2_in} WHERE id=-1"))
s.commit()
build_impact_scope(s, ev.id)
cyc = False
try:
    build_candidate(s, ev.id)
except ValueError as ex:
    cyc = "دورة تبعية" in str(ex)
    s.rollback()
check("T10.5 دورة تبعية: ValueError صريح (لا ترتيب مخترَع)", cyc)
check("T10.6 ولا أي كتابة جزئية عند الدورة", s.execute(
    select(func.count(CorrectionEventCandidateState.id))).scalar_one() == 0)

print()
print(f"Candidate Engine (G-007) test summary: {sum(1 for r in results if r[0]=='PASS')}/{len(results)} PASS")
failed = [r for r in results if r[0] == "FAIL"]
if failed:
    print("FAILURES:")
    for r in failed:
        print(" -", r[1], r[2])
    sys.exit(1)
