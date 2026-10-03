"""
tests/test_correction_entry_builder.py
======================================
DL-006 — منشئ قيد التصحيح + سجل الربط. كل رقم متوقَّع مشتَقّ يدوياً من
G-005 (ΔCOGS = −delta_inventory_value لبيع خارجي؛ ΔInputs = ΔCOGS + ΔEnd)
قبل التشغيل، لا من مخرجات المنشئ.
"""
import os, sys, datetime, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine, select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Setting, CostMethod, Warehouse, InventoryMovement, MovementDirection,
    Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, CorrectionEvent,
    CorrectionEventStatus, CorrectionEventDeltaRecord, CorrectionEventDeltaJournalLink,
    CorrectionEventAccountingCorrection, JournalEntry, JournalEntryStatus,
    Account, AccountType, Item,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item, update_item, ItemEditError
from app.services.posting import post_purchase_invoice, post_sales_invoice, get_default_warehouse
from app.services.invoice_cancel import cancel_invoice
from app.services.inventory_transfer import transfer_stock
from app.services.correction_detection import attach_detection_listeners
from app.services.correction_scope import build_impact_scope
from app.services.candidate_engine import build_candidate
from app.services import correction_entry_builder as ceb
from app.services.correction_entry_builder import post_correction_entry, CorrectionEntryError

results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, label, detail))
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))


def near(a, b):
    return a is not None and abs(D_(str(a)) - D_(str(b))) < D_("0.005")


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


def make_item(s, coa, sku, cogs=None, inv=None):
    return create_item(
        s, sku=sku, name_ar=f"صنف {sku}", unit="قطعة",
        inventory_account_id=(inv or coa["inventory"]).id,
        cogs_account_id=(cogs or coa["cogs"]).id,
        sales_account_id=coa["sales"].id, cost_method=CostMethod.AVERAGE)


def _inv(s, kind, poster, item, wh, q, p, d, ref):
    inv = Invoice(invoice_no=ref, kind=kind, party_name="طرف", invoice_date=d,
                  currency_code="SYP", exchange_rate=D_("1"),
                  status=InvoiceStatus.DRAFT, warehouse_id=wh.id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=D_(str(q)), unit_price=D_(str(p)))]
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


def approve(s, ev, cand):
    ev.status = CorrectionEventStatus.APPROVED
    ev.approved_candidate_state_id = cand.id
    s.commit()


def entry_lines(s, entry):
    out = {}
    for l in entry.lines:
        out[(l.account_id, "D" if D_(str(l.debit_base)) > 0 else "C")] = \
            D_(str(l.debit_base)) if D_(str(l.debit_base)) > 0 else D_(str(l.credit_base))
    return out


def counts(s):
    return (
        s.execute(select(func.count()).select_from(JournalEntry).where(
            JournalEntry.source_type == "accounting_correction")).scalar(),
        s.execute(select(func.count()).select_from(CorrectionEventAccountingCorrection)).scalar(),
        s.execute(select(func.count()).select_from(CorrectionEventDeltaJournalLink)).scalar(),
    )


D = datetime.date
Y = 2026
TODAY = datetime.date.today()

# =========================================================================
# T1 — بيع يُعاد تقييمه بعد شراء رجعي: ΔCOGS=+20 ⟹ Dr COGS 20 / Cr Inventory 20
#   PA(10@10,Jan1) S1(4,Jan10,تكلفة 10) PB(10@20,Jan5) ⟹ S1 cost 15: Δ=-20
# =========================================================================
s, _e = make_session(); coa, wh = seed(s); item = make_item(s, coa, "T1")
purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
S1 = sale(s, item, wh, 4, 50, D(Y, 1, 10), "S1")
PB = purchase(s, item, wh, 10, 20, D(Y, 1, 5), "PB")
ev = the_event(s); build_impact_scope(s, ev.id)
cand = build_candidate(s, ev.id); s.commit(); approve(s, ev, cand)
res = post_correction_entry(s, ev.id, cand.id); s.commit()
je = res.journal_entry
lines = entry_lines(s, je)
check("T1.1 القيد: Dr COGS 20 / Cr Inventory 20 (لا غير)",
      lines == {(coa["cogs"].id, "D"): D_("20.00"), (coa["inventory"].id, "C"): D_("20.00")}, str(lines))
check("T1.2 POSTED ومتوازن، source_type=accounting_correction، ref JE-COR-000001",
      je.status == JournalEntryStatus.POSTED and je.is_balanced()
      and je.source_type == "accounting_correction" and je.ref_no == "JE-COR-000001", je.ref_no)
check("T1.3 DL-007: تاريخ القيد = تاريخ الترحيل الفعلي (اليوم)", je.entry_date == TODAY, str(je.entry_date))
check("T1.4 source_id = id الحدث، والعملة = العملة الأساسية بسعر 1",
      je.source_id == ev.id and je.currency_code == "SYP" and D_(str(je.exchange_rate)) == 1)
links = s.execute(select(CorrectionEventDeltaJournalLink)).scalars().all()
by_treat = {l.treatment: l for l in links}
check("T1.5 ربط per-Delta: صف لكل Delta (2)، واحد cogs_entry بمبلغ +20 وواحد carried_by_documents بصفر",
      len(links) == 2 and near(by_treat["cogs_entry"].cogs_amount, 20)
      and near(by_treat["carried_by_documents"].cogs_amount, 0), str([(l.treatment, l.cogs_amount) for l in links]))
ac = s.execute(select(CorrectionEventAccountingCorrection)).scalars().one()
check("T1.6 AccountingCorrection يشير للقيد، والروابط للتصحيح نفسه",
      ac.journal_entry_id == je.id and all(l.accounting_correction_id == ac.id for l in links))
s.refresh(ev)
check("T1.7 حالة الحدث → ACCOUNTING_CORRECTION", ev.status == CorrectionEventStatus.ACCOUNTING_CORRECTION)
check("T1.8 النتيجة: links_count=2، cogs_links_count=1", res.links_count == 2 and res.cogs_links_count == 1)

# T1.9 ترحيل ثانٍ مرفوض (حالة) + حاجز DB على الربط المزدوج
before = counts(s)
try:
    post_correction_entry(s, ev.id, cand.id); ok = False
except CorrectionEntryError:
    ok = True
check("T1.9 ترحيل ثانٍ للحدث نفسه مرفوض، بلا أي كتابة", ok and counts(s) == before, str(counts(s)))
ev.status = CorrectionEventStatus.APPROVED; s.commit()
try:
    post_correction_entry(s, ev.id, cand.id); ok = False
except CorrectionEntryError as e:
    ok = "مرحَّل أصلاً" in str(e)
check("T1.10 حتى مع إعادة الحالة يدوياً إلى APPROVED: يرفض لوجود تصحيح مرحَّل", ok and counts(s) == before)
d0 = s.execute(select(CorrectionEventDeltaRecord)).scalars().first()
try:
    s.add(CorrectionEventDeltaJournalLink(
        delta_record_id=d0.id, accounting_correction_id=ac.id,
        treatment="cogs_entry", cogs_amount=D_("1")))
    s.commit(); ok = False
except IntegrityError:
    s.rollback(); ok = True
check("T1.11 UNIQUE(delta_record_id): Delta واحد لا يُربَط بقيدين (على مستوى DB)", ok)
try:
    s.add(CorrectionEventDeltaJournalLink(
        delta_record_id=d0.id, accounting_correction_id=ac.id,
        treatment="invented", cogs_amount=D_("0")))
    s.commit(); ok = False
except IntegrityError:
    s.rollback(); ok = True
check("T1.12 CHECK على treatment: قيمة مخترَعة مرفوضة على مستوى DB", ok)

# =========================================================================
# T2 — تصحيح قيمة الجذر PB: 20→30. ΔInputs=100، ΔCOGS=+40، ΔEnd=+60
#   ⟹ Dr COGS 40 / Cr Inventory 40، وهوية 100 = 40 + 60 من الأرقام نفسها
# =========================================================================
s, _e = make_session(); coa, wh = seed(s); item = make_item(s, coa, "T2")
purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
S1 = sale(s, item, wh, 4, 50, D(Y, 1, 10), "S1")
PB = purchase(s, item, wh, 10, 20, D(Y, 1, 5), "PB")
ev = the_event(s); build_impact_scope(s, ev.id)
mPB = mvt(s, PB)
cand = build_candidate(s, ev.id, {mPB.id: (D_("10"), D_("30"))}); s.commit(); approve(s, ev, cand)
res = post_correction_entry(s, ev.id, cand.id); s.commit()
lines = entry_lines(s, res.journal_entry)
check("T2.1 القيد: Dr COGS 40 / Cr Inventory 40",
      lines == {(coa["cogs"].id, "D"): D_("40.00"), (coa["inventory"].id, "C"): D_("40.00")}, str(lines))
deltas = s.execute(select(CorrectionEventDeltaRecord)).scalars().all()
d_in = sum(D_(str(d.delta_inventory_value)) for d in deltas if d.movement_id == mPB.id)
d_end = sum(D_(str(d.delta_inventory_value)) for d in deltas)
d_cogs = sum(D_(str(l.cogs_amount)) for l in s.execute(select(CorrectionEventDeltaJournalLink)).scalars())
check("T2.2 هوية G-005: ΔInputs(100) = ΔCOGS(قيد=40) + ΔEnd(ΣΔ=60)",
      near(d_in, 100) and near(d_cogs, 40) and near(d_end, 60) and near(d_in, d_cogs + d_end),
      f"{d_in},{d_cogs},{d_end}")
check("T2.3 صافي أثر القيد على حساب المخزون = −40 (ΔInputs المُرحَّل بمستنداته 100 ⟹ ΔEnd=+60)",
      near(sum((D_(str(l.credit_base)) - D_(str(l.debit_base))) for l in res.journal_entry.lines
               if l.account_id == coa["inventory"].id), 40))

# =========================================================================
# T3 — ΔCOGS سالب ⟹ قيد معكوس: Dr Inventory 12 / Cr COGS 12
#   PA(10@10,Jan1) S1(4,Jan10,تكلفة 10) PB(10@4,Jan5): A=140/20=7 ⟹ S1: -28-(-40)=+12
# =========================================================================
s, _e = make_session(); coa, wh = seed(s); item = make_item(s, coa, "T3")
purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
S1 = sale(s, item, wh, 4, 50, D(Y, 1, 10), "S1")
PB = purchase(s, item, wh, 10, 4, D(Y, 1, 5), "PB")
ev = the_event(s); build_impact_scope(s, ev.id)
cand = build_candidate(s, ev.id); s.commit(); approve(s, ev, cand)
res = post_correction_entry(s, ev.id, cand.id); s.commit()
lines = entry_lines(s, res.journal_entry)
check("T3.1 ΔCOGS=-12 ⟹ Dr Inventory 12 / Cr COGS 12 (معكوس)",
      lines == {(coa["inventory"].id, "D"): D_("12.00"), (coa["cogs"].id, "C"): D_("12.00")}, str(lines))
cl = [l for l in s.execute(select(CorrectionEventDeltaJournalLink)).scalars() if l.treatment == "cogs_entry"]
check("T3.2 cogs_amount في الربط سالب (-12)", len(cl) == 1 and near(cl[0].cogs_amount, -12))

# =========================================================================
# T4 — مثال التحويل بين مستودعين (T5 في G-007): ΔCOGS=+20
#   Dr COGS 20 / Cr Inventory 20؛ ساقا Transfer بلا قيد (ربط transfer_no_entry)
# =========================================================================
s, _e = make_session(); coa, wh1 = seed(s); item = make_item(s, coa, "T4")
wh2 = Warehouse(name_ar="W2"); s.add(wh2); s.commit()
purchase(s, item, wh1, 10, 10, D(Y, 1, 1), "PA")
tr = transfer_stock(s, item.id, wh1.id, wh2.id, 6, transfer_date=D(Y, 1, 5)); s.commit()
sale(s, item, wh2, 4, 50, D(Y, 1, 8), "S1")
purchase(s, item, wh1, 10, 20, D(Y, 1, 3), "PR")
ev = the_event(s); build_impact_scope(s, ev.id)
cand = build_candidate(s, ev.id); s.commit(); approve(s, ev, cand)
je_count_before = s.execute(select(func.count()).select_from(JournalEntry)).scalar()
res = post_correction_entry(s, ev.id, cand.id); s.commit()
lines = entry_lines(s, res.journal_entry)
check("T4.1 مثال Transfer: Dr COGS 20 / Cr Inventory 20",
      lines == {(coa["cogs"].id, "D"): D_("20.00"), (coa["inventory"].id, "C"): D_("20.00")}, str(lines))
tl = s.execute(select(CorrectionEventDeltaJournalLink)).scalars().all()
tt = sorted(l.treatment for l in tl)
check("T4.2 الربط: 4 صفوف = cogs_entry + carried_by_documents + 2×transfer_no_entry",
      tt == ["carried_by_documents", "cogs_entry", "transfer_no_entry", "transfer_no_entry"], str(tt))
check("T4.3 Transfer: لا COGS ولا قيد — قيد واحد جديد فقط (قيد التصحيح)",
      s.execute(select(func.count()).select_from(JournalEntry)).scalar() == je_count_before + 1
      and all(near(l.cogs_amount, 0) for l in tl if l.treatment == "transfer_no_entry"))
check("T4.4 B.3 على مستوى الشركة: ΔInputs(0)=ΔCOGS(+20)+ΔEnd(-20)",
      near(sum(D_(str(l.cogs_amount)) for l in tl), 20)
      and near(sum(D_(str(d.delta_inventory_value)) for d in
                   s.execute(select(CorrectionEventDeltaRecord)).scalars()), -20))

# =========================================================================
# T5 — بيعان متأثران ⟹ قيد واحد مُجمَّع (أربعة أسطر لا أكثر من سطرين)
#   PA(10@10,Jan1) S1(2,Jan10) S2(2,Jan12) PB(10@20,Jan5): A=15
#   S1: 10→15 (+10)، S2: مخزَّنة 10 (V=80,Q=8) → 15 (+10) ⟹ ΔCOGS=20
# =========================================================================
s, _e = make_session(); coa, wh = seed(s); item = make_item(s, coa, "T5")
purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
sale(s, item, wh, 2, 50, D(Y, 1, 10), "S1")
sale(s, item, wh, 2, 50, D(Y, 1, 12), "S2")
purchase(s, item, wh, 10, 20, D(Y, 1, 5), "PB")
ev = the_event(s); build_impact_scope(s, ev.id)
cand = build_candidate(s, ev.id); s.commit(); approve(s, ev, cand)
res = post_correction_entry(s, ev.id, cand.id); s.commit()
cl = [l for l in s.execute(select(CorrectionEventDeltaJournalLink)).scalars() if l.treatment == "cogs_entry"]
check("T5.1 قيد واحد بسطرين: Dr COGS 20 / Cr Inventory 20 (مُجمَّع)",
      len(res.journal_entry.lines) == 2
      and entry_lines(s, res.journal_entry) == {(coa["cogs"].id, "D"): D_("20.00"),
                                                (coa["inventory"].id, "C"): D_("20.00")})
check("T5.2 صفّا cogs_entry بقيمة +10 لكل منهما", len(cl) == 2 and all(near(l.cogs_amount, 10) for l in cl))

# =========================================================================
# T6 — الحسابات من الصنف/القيد الأصلي لا من الافتراضيات: حسابات مخصَّصة
# =========================================================================
s, _e = make_session(); coa, wh = seed(s)
a_cogs = Account(code="5999", name_ar="تكلفة خاصة", account_type=AccountType.EXPENSE)
a_inv = Account(code="1999", name_ar="مخزون خاص", account_type=AccountType.ASSET)
s.add_all([a_cogs, a_inv]); s.commit()
item = create_item(s, sku="T6", name_ar="خاص", unit="قطعة",
                   inventory_account_id=a_inv.id, cogs_account_id=a_cogs.id,
                   sales_account_id=coa["sales"].id, cost_method=CostMethod.AVERAGE)
purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
sale(s, item, wh, 4, 50, D(Y, 1, 10), "S1")
purchase(s, item, wh, 10, 20, D(Y, 1, 5), "PB")
ev = the_event(s); build_impact_scope(s, ev.id)
cand = build_candidate(s, ev.id); s.commit(); approve(s, ev, cand)
res = post_correction_entry(s, ev.id, cand.id); s.commit()
check("T6.1 القيد بحسابات الصنف الخاصة (لا حسابات الدليل الافتراضية)",
      entry_lines(s, res.journal_entry) == {(a_cogs.id, "D"): D_("20.00"), (a_inv.id, "C"): D_("20.00")},
      str(entry_lines(s, res.journal_entry)))

# =========================================================================
# T7 — الشرط الأساسي: اختلاف حسابات إعادة الترحيل عن الأصلية ⟹ إلغاء كامل
#   نتجاوز حارس item_edit عمداً (UPDATE مباشر) لمحاكاة استيراد/سكربت.
# =========================================================================
def fresh_t1(sku):
    s, _e = make_session(); coa, wh = seed(s); item = make_item(s, coa, sku)
    purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
    sale(s, item, wh, 4, 50, D(Y, 1, 10), "S1")
    purchase(s, item, wh, 10, 20, D(Y, 1, 5), "PB")
    ev = the_event(s); build_impact_scope(s, ev.id)
    cand = build_candidate(s, ev.id); s.commit(); approve(s, ev, cand)
    return s, coa, item, ev, cand


s, coa, item, ev, cand = fresh_t1("T7a")
try:
    update_item(s, item, sku="T7a", name_ar="x", unit="قطعة", category=None,
                cost_method=CostMethod.AVERAGE, reorder_point=D_("0"),
                inventory_account_id=coa["cash"].id, sales_account_id=item.sales_account_id,
                cogs_account_id=item.cogs_account_id, is_active=True)
    ok = False
except ItemEditError:
    ok = True
check("T7.0 الثابت قائم: تغيير حساب صنف له حركات مرفوض في item_edit", ok)
s.rollback()

other_inv = Account(code="1888", name_ar="مخزون آخر", account_type=AccountType.ASSET)
s.add(other_inv); s.commit()
s.execute(update(Item).where(Item.id == item.id).values(inventory_account_id=other_inv.id)); s.commit()
before = counts(s); je_before = s.execute(select(func.count()).select_from(JournalEntry)).scalar()
try:
    post_correction_entry(s, ev.id, cand.id); ok, msg = False, ""
except CorrectionEntryError as e:
    ok, msg = True, str(e)
s.rollback(); s.refresh(ev)
check("T7.1 اختلاف حساب المخزون ⟹ CorrectionEntryError (اختلاف الحسابات)", ok and "اختلاف الحسابات" in msg, msg[:80])
check("T7.2 بلا أي أثر: لا قيد، لا تصحيح، لا ربط، لا قيود جديدة، والحالة APPROVED",
      counts(s) == before == (0, 0, 0)
      and s.execute(select(func.count()).select_from(JournalEntry)).scalar() == je_before
      and ev.status == CorrectionEventStatus.APPROVED)

s, coa, item, ev, cand = fresh_t1("T7b")
other_cogs = Account(code="5888", name_ar="تكلفة أخرى", account_type=AccountType.EXPENSE)
s.add(other_cogs); s.commit()
s.execute(update(Item).where(Item.id == item.id).values(cogs_account_id=other_cogs.id)); s.commit()
try:
    post_correction_entry(s, ev.id, cand.id); ok = False
except CorrectionEntryError as e:
    ok = "اختلاف الحسابات" in str(e)
s.rollback()
check("T7.3 اختلاف حساب التكلفة ⟹ مرفوض بلا أثر", ok and counts(s) == (0, 0, 0))

# جانب الشراء (إعادة ترحيل المستندات المصحَّحة): حساب المخزون في قيد الشراء الأصلي
s, coa, item, ev, cand = fresh_t1("T7c")
pb = s.execute(select(Invoice).where(Invoice.invoice_no == "PB")).scalars().one()
pb_entry = s.get(JournalEntry, pb.journal_entry_id)
pb_mv = mvt(s, pb)
s.execute(update(Item).where(Item.id == item.id).values(inventory_account_id=coa["cash"].id)); s.commit()
s.refresh(item)
try:
    ceb._prove_invoice_accounts(s, pb, pb_entry, InvoiceKind.PURCHASE, {item.id}); ok = False
except CorrectionEntryError as e:
    ok = "اختلاف الحسابات" in str(e)
check("T7.4 جانب الشراء: حساب مخزون إعادة الترحيل ≠ قيد الشراء الأصلي ⟹ رفض", ok)

# =========================================================================
# T8 — DL-007: تاريخ قابل للتعديل، ولا يسبق قيد البيع الأصلي
# =========================================================================
s, coa, item, ev, cand = fresh_t1("T8a")
try:
    post_correction_entry(s, ev.id, cand.id, entry_date=D(Y, 1, 9)); ok = False
except CorrectionEntryError as e:
    ok = "أسبق" in str(e)
s.rollback()
check("T8.1 تاريخ أسبق من قيد البيع الأصلي (Jan 10) مرفوض، بلا أثر", ok and counts(s) == (0, 0, 0))
res = post_correction_entry(s, ev.id, cand.id, entry_date=D(Y, 12, 31)); s.commit()
check("T8.2 تعديل المحاسب لتاريخ القيد مقبول (2026-12-31)", res.journal_entry.entry_date == D(Y, 12, 31))

# =========================================================================
# T9 — الرفض: DL-003 / حالة غير معتمدة / مرشَّح خاطئ / Zero-effect / مرتجع-إلغاء
# =========================================================================
s, coa, item, ev, cand = fresh_t1("T9a")
dd = s.execute(select(CorrectionEventDeltaRecord)).scalars().all()
sale_delta = [d for d in dd if D_(str(d.delta_inventory_value)) < 0][0]
sale_delta.cost_basis_exception = True; sale_delta.delta_inventory_value = None; s.commit()
try:
    post_correction_entry(s, ev.id, cand.id); ok = False
except CorrectionEntryError as e:
    ok = "cost_basis_exception" in str(e)
s.rollback()
check("T9.1 DL-003: Candidate فيه استثناء تكلفة ⟹ رفض الحدث كاملاً بلا أثر", ok and counts(s) == (0, 0, 0))

s, coa, item, ev, cand = fresh_t1("T9b")
ev.status = CorrectionEventStatus.PENDING; s.commit()
try:
    post_correction_entry(s, ev.id, cand.id); ok = False
except CorrectionEntryError as e:
    ok = "APPROVED" in str(e)
s.rollback()
check("T9.2 حدث غير معتمَد (PENDING) ⟹ رفض", ok and counts(s) == (0, 0, 0))
ev.status = CorrectionEventStatus.APPROVED; ev.approved_candidate_state_id = cand.id + 99; s.commit()
try:
    post_correction_entry(s, ev.id, cand.id); ok = False
except CorrectionEntryError as e:
    ok = "ليس المعتمَد" in str(e)
s.rollback()
check("T9.3 Candidate ليس المعتمَد للحدث ⟹ رفض", ok and counts(s) == (0, 0, 0))

# Zero-effect: لا بيوع في النطاق
s, _e = make_session(); coa, wh = seed(s); item = make_item(s, coa, "T9c")
purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
purchase(s, item, wh, 10, 10, D(Y, 1, 5), "PB")
purchase(s, item, wh, 5, 10, D(Y, 1, 3), "PC")
ev = the_event(s); build_impact_scope(s, ev.id)
cand = build_candidate(s, ev.id); s.commit(); approve(s, ev, cand)
try:
    post_correction_entry(s, ev.id, cand.id); ok = False
except CorrectionEntryError as e:
    ok = "Zero-effect" in str(e)
s.rollback()
check("T9.4 ΔCOGS=0 ⟹ لا قيد (Zero-effect غير محسومة)، بلا أثر", ok and counts(s) == (0, 0, 0))

# إلغاء فاتورة داخل النطاق (invoice_cancel خارج مسار Candidate)
s, _e = make_session(); coa, wh = seed(s); item = make_item(s, coa, "T9d")
purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
S1 = sale(s, item, wh, 4, 50, D(Y, 1, 10), "S1")
cancel_invoice(s, S1, D(Y, 1, 11)); s.commit()
purchase(s, item, wh, 10, 20, D(Y, 1, 5), "PB")
ev = the_event(s); build_impact_scope(s, ev.id)
cand = build_candidate(s, ev.id); s.commit(); approve(s, ev, cand)
try:
    post_correction_entry(s, ev.id, cand.id); ok = False
except CorrectionEntryError as e:
    ok = "غير مدعوم" in str(e) or "مُلغاة" in str(e)
s.rollback()
check("T9.5 نطاق فيه إلغاء/مرتجع ⟹ رفض (لا رقم صامت لمسار غير معرَّف)", ok and counts(s) == (0, 0, 0))

# جلسة فيها تغييرات غير مُفلَّشة
s, coa, item, ev, cand = fresh_t1("T9e")
ev_id, cand_id = ev.id, cand.id     # تحميل الخصائص قبل الإضافة (وصول خاصية منتهية الصلاحية يُشغِّل autoflush)
s.add(Account(code="7777", name_ar="معلَّق", account_type=AccountType.EXPENSE))
try:
    post_correction_entry(s, ev_id, cand_id); ok = False
except CorrectionEntryError as e:
    ok = "غير مُفلَّشة" in str(e)
s.rollback()
check("T9.6 جلسة بتغييرات معلَّقة ⟹ رفض صريح (لا commit ضمني لتغييرات غريبة)", ok)

# =========================================================================
# T10 — الذرّية: فشل منتصف الكتابة ⟹ لا قيد ولا ربط ولا تصحيح ولا تغيير حالة
# =========================================================================
def inject(attr, label):
    s, coa, item, ev, cand = fresh_t1("AT" + attr)
    orig = getattr(ceb, attr)
    def boom(*a, **k):
        raise RuntimeError("فشل مُحاكى")
    setattr(ceb, attr, boom)
    try:
        post_correction_entry(s, ev.id, cand.id); raised = False
    except RuntimeError:
        raised = True
    finally:
        setattr(ceb, attr, orig)
    s.rollback(); s.refresh(ev)
    check(f"{label}.1 الفشل يُرفَع للمستدعي", raised)
    check(f"{label}.2 لا JournalEntry ولا تصحيح ولا ربط، والحدث ما زال APPROVED",
          counts(s) == (0, 0, 0) and ev.status == CorrectionEventStatus.APPROVED, str(counts(s)))
    # ويمكن إعادة المحاولة بنجاح بعد زوال السبب
    r = post_correction_entry(s, ev.id, cand.id); s.commit()
    check(f"{label}.3 إعادة المحاولة بعد الفشل تنجح بقيد واحد فقط",
          counts(s) == (1, 1, 2) and r.journal_entry.is_balanced(), str(counts(s)))


inject("_write_links", "T10a (فشل بعد إنشاء القيد وقبل الربط)")
inject("_verify_posted", "T10b (فشل بعد القيد والربط وتغيير الحالة)")

# =========================================================================
# T11 — مسار قاعدة بيانات ملف حقيقي (حجز ref_no باتصال مستقل، لا database is locked)
# =========================================================================
tmp = tempfile.mkdtemp(); path = os.path.join(tmp, "t11.db")
s, _e = make_session(f"sqlite:///{path}"); coa, wh = seed(s); item = make_item(s, coa, "T11")
purchase(s, item, wh, 10, 10, D(Y, 1, 1), "PA")
S1 = sale(s, item, wh, 4, 50, D(Y, 1, 10), "S1")
purchase(s, item, wh, 10, 20, D(Y, 1, 5), "PB")
ev = the_event(s); build_impact_scope(s, ev.id)
cand = build_candidate(s, ev.id); s.commit(); approve(s, ev, cand)
orig = ceb._verify_posted
def boom(*a, **k): raise RuntimeError("فشل مُحاكى")
ceb._verify_posted = boom
try:
    post_correction_entry(s, ev.id, cand.id); raised = False
except RuntimeError:
    raised = True
finally:
    ceb._verify_posted = orig
s2 = sessionmaker(bind=_e)()
check("T11.1 ملف حقيقي: فشل منتصف الكتابة ⟹ جلسة ثانية لا ترى شيئاً", raised and counts(s2) == (0, 0, 0), str(counts(s2)))
s2.close()
res = post_correction_entry(s, ev.id, cand.id); s.commit()
s3 = sessionmaker(bind=_e)()
check("T11.2 ملف حقيقي: النجاح يُثبَّت ويظهر لجلسة ثانية (قيد 1، تصحيح 1، ربط 2)",
      counts(s3) == (1, 1, 2), str(counts(s3)))
s3.close()

# =========================================================================
# T12 — BLOCKER المراجعة: إثبات مطابقة حساب **الصنف نفسه** في فاتورة متعددة البنود
#   قيد الفاتورة مُجمَّع حسب الحساب؛ فحص "الحساب موجود في القيد" يُخدَع حين
#   يستخدمه صنف آخر في الفاتورة نفسها. المطلوب: رفض كامل بلا قيد/ربط/تغيير حالة.
# =========================================================================
def multi_inv(s, kind, poster, wh, lines, d, ref):
    inv = Invoice(invoice_no=ref, kind=kind, party_name="طرف", invoice_date=d,
                  currency_code="SYP", exchange_rate=D_("1"),
                  status=InvoiceStatus.DRAFT, warehouse_id=wh.id)
    inv.lines = [InvoiceLine(item_id=it.id, quantity=D_(str(q)), unit_price=D_(str(p)))
                 for it, q, p in lines]
    s.add(inv); s.commit()
    poster(s, inv, is_cash=True); s.commit()
    return inv


def mk_acc(s, code, name, typ=AccountType.EXPENSE):
    a = Account(code=code, name_ar=name, account_type=typ); s.add(a); s.commit(); return a


def event_of(s, item):
    evs = [e for e in s.execute(select(CorrectionEvent)).scalars() if e.item_id == item.id]
    assert len(evs) == 1, f"أحداث الصنف {item.sku}: {len(evs)}"
    return evs[0]


def total_entries(s):
    return s.execute(select(func.count()).select_from(JournalEntry)).scalar()


def assert_full_reject(label, s, ev, cand, needle, entries_before):
    try:
        post_correction_entry(s, ev.id, cand.id); ok, msg = False, ""
    except CorrectionEntryError as e:
        ok, msg = True, str(e)
    # القياس **قبل** أي rollback من الاختبار: لو قُبلت العملية خطأً يجب أن يظهر أثرها
    c_now, n_now, st_now = counts(s), total_entries(s), ev.status
    s.rollback(); s.refresh(ev)
    check(f"{label}.a رُفضت العملية بالسبب المتوقَّع ({needle})", ok and needle in msg, msg[:110])
    check(f"{label}.b لا قيد ولا تصحيح ولا ربط، لا قيود جديدة، والحدث ما زال APPROVED",
          c_now == (0, 0, 0) and n_now == entries_before
          and st_now == CorrectionEventStatus.APPROVED and ev.status == CorrectionEventStatus.APPROVED,
          f"{c_now}, entries {n_now}/{entries_before}, {st_now}")


def sales_fixture(cogs_accs, inv_accs, qtys, event_item_idx=0):
    """فاتورة بيع واحدة بعدة بنود (صنف لكل بند)، كل صنف بحسابه، تكلفة مخزَّنة 10.
    ثم شراء رجعي للصنف event_item_idx بسعر 20 ⟹ A=15 ⟹ ΔCOGS(صنف الحدث)=+5×كمية."""
    s, _e = make_session(); coa, wh = seed(s)
    items = []
    for k, (c, i) in enumerate(zip(cogs_accs, inv_accs)):
        items.append(make_item(s, coa, f"M{k}", cogs=c(s), inv=i(s, coa)))
    for k, it in enumerate(items):
        purchase(s, it, wh, 10, 10, D(Y, 1, 1), f"PA{k}")
    multi_inv(s, InvoiceKind.SALES, post_sales_invoice, wh,
              [(it, q, 50) for it, q in zip(items, qtys)], D(Y, 1, 10), "S-MULTI")
    purchase(s, items[event_item_idx], wh, 10, 20, D(Y, 1, 5), "PB")
    ev = event_of(s, items[event_item_idx]); build_impact_scope(s, ev.id)
    cand = build_candidate(s, ev.id); s.commit(); approve(s, ev, cand)
    return s, coa, items, ev, cand


_cx, _cy = {}, {}
def acc_cogs(code, name):
    cache = {}
    def f(s):
        if code not in cache:
            cache[code] = mk_acc(s, code, name)
        return cache[code]
    return f

# ---- T12.0 خط أساس: الفاتورة المتعددة سليمة ⟹ تُقبل، وبحساب الصنف الأصلي ------
fx, fy = acc_cogs("5901", "تكلفة X"), acc_cogs("5902", "تكلفة Y")
same_inv = lambda s, coa: coa["inventory"]
s, coa, (A, B), ev, cand = sales_fixture([fx, fy], [same_inv, same_inv], [4, 2])
X, Yacc = fx(s), fy(s)
res = post_correction_entry(s, ev.id, cand.id); s.commit()
check("T12.0 فاتورة متعددة البنود سليمة (A:40، B:20) ⟹ تُقبل: Dr COGS-X 20 / Cr Inventory 20 (حساب A الأصلي لا B)",
      entry_lines(s, res.journal_entry) == {(X.id, "D"): D_("20.00"), (coa["inventory"].id, "C"): D_("20.00")},
      str(entry_lines(s, res.journal_entry)))

# ---- T12.1 السيناريو المطلوب حرفياً: حساب تكلفة A يصبح حساب تكلفة B ------------
fx, fy = acc_cogs("5901", "تكلفة X"), acc_cogs("5902", "تكلفة Y")
s, coa, (A, B), ev, cand = sales_fixture([fx, fy], [same_inv, same_inv], [4, 2])
X, Yacc = fx(s), fy(s)
s.execute(update(Item).where(Item.id == A.id).values(cogs_account_id=Yacc.id)); s.commit()
check("T12.1.0 شرط السيناريو: حساب الصنف Y موجود فعلاً في قيد الفاتورة (فحص الوجود كان سيمرّ)",
      any(l.account_id == Yacc.id and D_(str(l.debit_base)) > 0
          for l in s.get(JournalEntry, s.execute(select(Invoice.journal_entry_id).where(
              Invoice.invoice_no == "S-MULTI")).scalar()).lines))
assert_full_reject("T12.1", s, ev, cand, "اختلاف الحسابات (COGS)", total_entries(s))

# ---- T12.2 نفس الثغرة على جهة المخزون (حسابات مخزون مختلفة للصنفين) ------------
fx, fy = acc_cogs("5901", "تكلفة X"), acc_cogs("5902", "تكلفة Y")
ia = lambda s, coa: mk_acc(s, "1901", "مخزون A", AccountType.ASSET)
ib = lambda s, coa: mk_acc(s, "1902", "مخزون B", AccountType.ASSET)
s, coa, (A, B), ev, cand = sales_fixture([fx, fx], [ia, ib], [4, 2])
IA = s.execute(select(Account).where(Account.code == "1901")).scalars().one()
IB = s.execute(select(Account).where(Account.code == "1902")).scalars().one()
s.execute(update(Item).where(Item.id == A.id).values(inventory_account_id=IB.id)); s.commit()
assert_full_reject("T12.2", s, ev, cand, "اختلاف الحسابات (Inventory)", total_entries(s))

# ---- T12.3 تبديل متبادل بين الصنفين بمبلغين متساويين: يكشفه ترتيب أسطر القيد ----
fx, fy = acc_cogs("5901", "تكلفة X"), acc_cogs("5902", "تكلفة Y")
s, coa, (A, B), ev, cand = sales_fixture([fx, fy], [same_inv, same_inv], [2, 2])
X, Yacc = fx(s), fy(s)
s.execute(update(Item).where(Item.id == A.id).values(cogs_account_id=Yacc.id))
s.execute(update(Item).where(Item.id == B.id).values(cogs_account_id=X.id)); s.commit()
assert_full_reject("T12.3", s, ev, cand, "اختلاف الحسابات (COGS)", total_entries(s))

# ---- T12.4 القيد المُجمَّع لا يُثبت المطابقة (تخصيصان ينتجان القيد نفسه) ----------
#   A(X,10) B(Y,10) C(X,10): القيد [X:20, Y:10] يُنتَج أيضاً بـA→X,B→X,C→Y.
#   صنف الحدث C. لا عبث بالحسابات هنا ⟹ الرفض سببه عدم كفاية الدليل وحده.
fx, fy = acc_cogs("5901", "تكلفة X"), acc_cogs("5902", "تكلفة Y")
s, coa, (A, B, C), ev, cand = sales_fixture([fx, fy, fx], [same_inv] * 3, [1, 1, 1], event_item_idx=2)
assert_full_reject("T12.4", s, ev, cand, "غير قابل للإثبات (COGS)", total_entries(s))

# ---- T12.5 جهة الشراء: مستند شراء متعدد البنود يُعاد ترحيله كاملاً ----------------
def purchase_fixture(tamper):
    fa, fb = acc_cogs("5901", "تكلفة X"), acc_cogs("5902", "تكلفة Y")
    s, _e = make_session(); coa, wh = seed(s)
    A = make_item(s, coa, "PA", cogs=fa(s), inv=ia(s, coa))
    B = make_item(s, coa, "PB", cogs=fb(s), inv=ib(s, coa))
    purchase(s, A, wh, 10, 10, D(Y, 1, 1), "PA-A"); purchase(s, B, wh, 10, 10, D(Y, 1, 1), "PA-B")
    sale(s, A, wh, 4, 50, D(Y, 1, 10), "S-A")
    PM = multi_inv(s, InvoiceKind.PURCHASE, post_purchase_invoice, wh,
                   [(A, 10, 20), (B, 5, 7)], D(Y, 1, 5), "PM")
    ev = event_of(s, A); build_impact_scope(s, ev.id)
    mPM = s.execute(select(InventoryMovement).where(
        InventoryMovement.source_type == "purchase_invoice", InventoryMovement.source_id == PM.id,
        InventoryMovement.item_id == A.id)).scalars().one()
    cand = build_candidate(s, ev.id, {mPM.id: (D_("10"), D_("30"))}); s.commit(); approve(s, ev, cand)
    if tamper:
        IA_ = s.execute(select(Account).where(Account.code == "1901")).scalars().one()
        s.execute(update(Item).where(Item.id == B.id).values(inventory_account_id=IA_.id)); s.commit()
    return s, coa, A, B, ev, cand

s, coa, A, B, ev, cand = purchase_fixture(False)
res = post_correction_entry(s, ev.id, cand.id); s.commit()
check("T12.5.0 مستند شراء متعدد البنود سليم (A:200، B:35، حسابا مخزون مختلفان) ⟹ يُقبل (ΔCOGS=+40)",
      any(D_(str(l.debit_base)) == D_("40.00") for l in res.journal_entry.lines), str(entry_lines(s, res.journal_entry)))
s, coa, A, B, ev, cand = purchase_fixture(True)
assert_full_reject("T12.5", s, ev, cand, "اختلاف الحسابات (Inventory)", total_entries(s))

passed = sum(1 for r in results if r[0] == "PASS")
print(f"\nCorrection Entry Builder (DL-006) test summary: {passed}/{len(results)} PASS")
sys.exit(0 if passed == len(results) else 1)
