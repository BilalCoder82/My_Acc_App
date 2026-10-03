"""DL-006 — منشئ قيد تصحيح التكلفة التاريخية

يحوّل Candidate معتمَداً (G-007) إلى قيد تصحيح واحد + سجل ربط per-Delta.

القرارات المرجعية (01_PROGRAMMER_COLLABORATION_PROTOCOL.md):
- DL-002/003/004/005: عقد التكلفة التاريخية (يُنتجها candidate_engine).
- DL-006: القيد = Dr COGS / Cr Inventory بمقدار ΔCOGS فقط (سالب ⟹ معكوس)،
  بحسابات الصنف **كما في قيد البيع الأصلي**؛ أثر المدخلات (ΔInputs) يُرحَّل
  بالمستندات المُصحَّحة نفسها لا بهذا القيد. الثابت: حسابات الصنف غير قابلة
  للتغيير بعد أول حركة (item_edit.py)، و**التحقق الدفاعي** هنا يحرس تجاوزه.
- DL-007: تاريخ القيد = تاريخ الترحيل الفعلي (اليوم افتراضياً)، قابل للتعديل
  من المحاسب عبر entry_date؛ لا يجوز أن يسبق تاريخ أي قيد بيع أصلي متأثر
  (نفس قاعدة reverse()).

الرياضيات (G-005 B.3): ΔInputs = ΔCOGS + ΔEnd. لبيع خارجي OUT:
delta_inventory_value = −Δ(q·c) ⟹ ΔCOGS(m) = −delta_inventory_value(m).

الرفض (كله أو لا شيء، قبل أي كتابة):
- أي صف cost_basis_exception أو delta_inventory_value=NULL (DL-003).
- Delta بلا حدث معتمَد: يلزم status=APPROVED و approved_candidate_state_id.
- حركة في النطاق من نوع غير مدعوم (مرتجع/إلغاء/غيره) — المرتجعات خارج مسار
  Candidate بقرار C؛ لا نُصدر رقماً صامتاً لمسار غير معرَّف.
- بيع متأثر مُلغى (له قيد عكسي)، أو بلا قيد أصلي.
- **اختلاف حسابات إعادة الترحيل عن حسابات القيد الأصلي** (الشرط الأساسي).
- ΔCOGS الكلي = 0 (سياسة Zero-effect غير محسومة — لا نخترع حلاً).

الذرّية: لا قيد ولا ربط ولا تغيير حالة جزئي. أي فشل بعد بدء الكتابة ⟹
session.rollback() ثم رفع الاستثناء. المُنشئ لا يستدعي commit بعد الكتابة —
التزام المستدعي هو نقطة الالتزام الوحيدة (نفس عقد G-007).

ملاحظتان تشغيليتان:
1. قبل أول كتابة يُنفَّذ session.commit() لتفريغ أي معاملة معلَّقة، لأن
   post_immediate() يحجز ref_no باتصال مستقل (BEGIN IMMEDIATE) ويتصادم مع
   أي معاملة مفتوحة على ملف SQLite حقيقي — نفس النمط في posting.py. الجلسة
   يجب أن تخلو من تغييرات غير مُفلَّشة (new/dirty/deleted) وإلا نرفض.
2. فشل بعد حجز ref_no يترك فجوة في تسلسل JE-COR (الحجز مستقل عن معاملة
   المستند بتصميم §4) — فجوة ترقيم لا قيداً جزئياً.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import product

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CorrectionEvent, CorrectionEventAccountingCorrection,
    CorrectionEventCandidateState, CorrectionEventDeltaJournalLink,
    CorrectionEventDeltaRecord, CorrectionEventStatus, Invoice, InvoiceKind,
    InventoryMovement, Item, JournalEntry, JournalEntryStatus, JournalLine,
    MovementDirection,
)
from app.services.journal_edit import AccountingIntent, LineIntent, post_immediate
from app.services.invoice_calc import compute_invoice_totals
from app.services.money import D, money
from app.services.posting import calculate_cogs, get_base_currency

_ZERO = Decimal("0")
_TOL = Decimal("0.0001")

# أنواع مصدر الحركة المدعومة في النطاق. ما عداها (sales_return/purchase_return/
# invoice_cancel/…) يُرفَض: لا يُربَط مرتجع/إلغاء بتكلفة البيع الأصلي في
# Candidate (قرار C)، فأي ΔCOGS محسوب بجوارها غير مكتمل اقتصادياً.
_SUPPORTED_SOURCES = {
    "purchase_invoice", "sales_invoice", "stock_transfer",
    "opening_balance", "opening_inventory",
}

TREAT_COGS = "cogs_entry"
TREAT_DOCS = "carried_by_documents"
TREAT_TRANSFER = "transfer_no_entry"

SOURCE_TYPE = "accounting_correction"


class CorrectionEntryError(Exception):
    """رفض صريح — لا أثر على القاعدة."""


@dataclass
class _LinkSpec:
    delta_id: int
    treatment: str
    cogs_amount: Decimal


@dataclass
class _Plan:
    links: list[_LinkSpec] = field(default_factory=list)
    # (account_id, side) -> مبلغ موجب مُقرَّب لمنزلتين، side ∈ {"D","C"}
    lines: dict[tuple[int, str], Decimal] = field(default_factory=dict)
    min_entry_date: date | None = None
    sale_count: int = 0


@dataclass
class CorrectionEntryResult:
    journal_entry: JournalEntry
    accounting_correction: CorrectionEventAccountingCorrection
    links_count: int
    cogs_links_count: int


def _dec(x) -> Decimal:
    return D(x)


def _require_clean_session(session: Session) -> None:
    if session.new or session.dirty or session.deleted:
        raise CorrectionEntryError(
            "الجلسة تحوي تغييرات غير مُفلَّشة — نظّفها (commit/rollback) قبل ترحيل "
            "قيد التصحيح: حجز ref_no يتصادم مع معاملة معلَّقة."
        )


def _load_event_and_deltas(session, event_id, candidate_id):
    event = session.get(CorrectionEvent, event_id)
    if event is None:
        raise CorrectionEntryError(f"CorrectionEvent غير موجود: {event_id}")
    cand = session.get(CorrectionEventCandidateState, candidate_id)
    if cand is None or cand.correction_event_id != event.id:
        raise CorrectionEntryError(
            f"Candidate {candidate_id} غير تابع للحدث {event_id}"
        )
    if event.status != CorrectionEventStatus.APPROVED:
        raise CorrectionEntryError(
            f"الحدث بحالة {event.status.value} — الترحيل يتطلب APPROVED"
        )
    if event.approved_candidate_state_id != cand.id:
        raise CorrectionEntryError(
            f"Candidate {candidate_id} ليس المعتمَد للحدث "
            f"(المعتمَد: {event.approved_candidate_state_id})"
        )
    existing = session.execute(
        select(CorrectionEventAccountingCorrection).where(
            CorrectionEventAccountingCorrection.correction_event_id == event.id)
    ).scalars().first()
    if existing is not None:
        raise CorrectionEntryError(
            f"للحدث {event_id} تصحيح محاسبي مرحَّل أصلاً (قيد {existing.journal_entry_id})"
        )
    deltas = list(session.execute(
        select(CorrectionEventDeltaRecord)
        .where(CorrectionEventDeltaRecord.candidate_state_id == cand.id)
        .order_by(CorrectionEventDeltaRecord.id)
    ).scalars().all())
    if not deltas:
        raise CorrectionEntryError("Candidate بلا Delta — لا شيء يُرحَّل")
    return event, cand, deltas


def _invoice_entry(session, mv: InventoryMovement, kind: InvoiceKind):
    """→ (Invoice, JournalEntry) للمستند الأصلي المرحَّل غير المعكوس."""
    inv = session.get(Invoice, mv.source_id) if mv.source_id is not None else None
    if inv is None or inv.kind != kind:
        raise CorrectionEntryError(
            f"حركة {mv.id}: فاتورة المصدر ({mv.source_type} #{mv.source_id}) غير موجودة"
        )
    if inv.journal_entry_id is None:
        raise CorrectionEntryError(
            f"حركة {mv.id}: الفاتورة {inv.invoice_no} بلا قيد أصلي"
        )
    entry = session.get(JournalEntry, inv.journal_entry_id)
    if entry is None or entry.status != JournalEntryStatus.POSTED:
        raise CorrectionEntryError(
            f"حركة {mv.id}: قيد الفاتورة {inv.invoice_no} غير مرحَّل"
        )
    reversal = session.execute(
        select(JournalEntry.id).where(JournalEntry.is_reversal_of == entry.id)
    ).first()
    if reversal is not None:
        raise CorrectionEntryError(
            f"الفاتورة {inv.invoice_no} مُلغاة/معكوسة — الإلغاء خارج مسار "
            f"Candidate (قرار C)؛ لا يُصدَر ΔCOGS لها"
        )
    return inv, entry


# ---------------------------------------------------------------------------
# إثبات مطابقة حساب **الصنف نفسه** (لا مجرد وجود الحساب في القيد)
# ---------------------------------------------------------------------------
# قيد الفاتورة مُجمَّع حسب الحساب (posting.py): لا يحمل أي أثر لأي صنف استخدم
# أي حساب. فحص "الحساب موجود في القيد" يُخدَع حين يستخدمه صنف آخر في الفاتورة
# نفسها (UPDATE مباشر يجعل حساب تكلفة الصنف A = حساب الصنف B ⟹ يبدو الحساب
# "موجوداً"، بينما الأثر الأصلي لـA كان على حساب آخر). الدليل الوحيد المتاح:
#   إعادة بناء القسم المُجمَّع من القيد (COGS / Inventory) من مساهمات الأصناف
#   الفعلية في المستند الأصلي (مبالغ + ترتيب أول ظهور للحساب، كما يفعل
#   posting.py) بحسابات الأصناف **الحالية**، ومقارنته بسطور القيد المحفوظة؛
#   ثم التحقق من **أن لا تخصيص بديل لحسابات الأصناف يُنتج القيد نفسه** بفرق
#   في صنف مطلوب إثباته. وجود أي تخصيص بديل = القيد لا يُثبت المطابقة ⟹ رفض
#   (لا يُعتبَر فحص الوجود كافياً). الحالات غير القابلة للإثبات تُرفَض عمداً:
#   صنف مطلوب بمساهمة صفرية (غير مرئي في القيد)، أو تخصيصان متكافئان
#   (مثل صنفين بمبلغين متساويين على حسابين مُبدَّلين)، أو مساحة بحث كبيرة.
_AMOUNT_TOL = Decimal("0.01")
_MAX_ASSIGNMENTS = 20000


def _aggregate_sequence(contribs, assign, *, drop_zero):
    """نفس تجميع posting.py: حساب -> مجموع، بترتيب أول ظهور (يشمل المساهمات
    الصفرية في الترتيب)، ثم (للبيع فقط) حذف المجاميع الصفرية."""
    order: list[int] = []
    totals: dict[int, Decimal] = {}
    for item_id, amount in contribs:
        acc = assign[item_id]
        if acc not in totals:
            order.append(acc)
            totals[acc] = _ZERO
        totals[acc] += amount
    seq = [(acc, totals[acc]) for acc in order]
    if drop_zero:
        seq = [(a, t) for a, t in seq if t != 0]
    return seq


def _seq_matches(expected, actual, n_contribs) -> bool:
    if len(expected) != len(actual):
        return False
    tol = _AMOUNT_TOL * max(1, n_contribs)
    return all(ea == aa and abs(ev - av) <= tol
               for (ea, ev), (aa, av) in zip(expected, actual))


def _prove_side(label, contribs, current, needed, actual_for, candidate_accounts,
                *, drop_zero, invoice_no, entry_ref):
    """يُثبت تخصيص حسابات الأصناف لجهة واحدة (COGS أو Inventory).
    contribs: [(item_id, amount)] بترتيب أسطر الفاتورة. current: item_id->حساب
    الصنف الحالي. needed: أصناف يجب إثبات حسابها. actual_for(expected_len)->
    القسم الفعلي من القيد كقائمة (account, amount)."""
    by_item: dict[int, Decimal] = {}
    for item_id, amount in contribs:
        by_item[item_id] = by_item.get(item_id, _ZERO) + amount
    for it in needed:
        if it not in by_item:
            raise CorrectionEntryError(
                f"الصنف {it} لا مساهمة له في {invoice_no} — لا يُثبَت حسابه ({label})")
        if by_item[it] == 0:
            raise CorrectionEntryError(
                f"غير قابل للإثبات ({label}): مساهمة الصنف {it} في {entry_ref} صفرية — "
                f"لا أثر مرئياً لحسابه في القيد المُجمَّع؛ أُلغيت العملية كاملة")

    # التخصيص الحالي يجب أن يُعيد إنتاج القسم الفعلي حرفياً
    cur_seq = _aggregate_sequence(contribs, current, drop_zero=drop_zero)
    if not _seq_matches(cur_seq, actual_for(len(cur_seq)), len(contribs)):
        raise CorrectionEntryError(
            f"اختلاف الحسابات ({label}): حسابات الأصناف الحالية في {invoice_no} لا تُعيد "
            f"إنتاج قسم {label} من قيد المستند الأصلي {entry_ref} — أُلغيت العملية كاملة، "
            f"لا ترحيل جزئي")

    # لا تخصيص بديل يُنتج القيد نفسه بفرق في صنف مطلوب
    free = [i for i, t in by_item.items() if t != 0]       # الصفريون غير مطلوبين: يثبتون
    cand = sorted(set(candidate_accounts))
    if len(cand) ** max(1, len(free)) > _MAX_ASSIGNMENTS:
        raise CorrectionEntryError(
            f"غير قابل للإثبات ({label}): مساحة التخصيصات في {invoice_no} أكبر من الحد "
            f"({_MAX_ASSIGNMENTS}) — يُرفَض بدل افتراض المطابقة")
    for combo in product(cand, repeat=len(free)):
        alt = dict(current)
        alt.update(dict(zip(free, combo)))
        if all(alt[i] == current[i] for i in needed):
            continue                                         # لا يختلف في صنف مطلوب
        alt_seq = _aggregate_sequence(contribs, alt, drop_zero=drop_zero)
        if _seq_matches(alt_seq, actual_for(len(alt_seq)), len(contribs)):
            diff = sorted(i for i in needed if alt[i] != current[i])
            raise CorrectionEntryError(
                f"غير قابل للإثبات ({label}): القيد المُجمَّع {entry_ref} يُنتَج أيضاً بتخصيص "
                f"مختلف لحسابات الأصناف {diff} — لا دليل يميّز حساب كل صنف؛ فحص وجود "
                f"الحساب في القيد لا يكفي. أُلغيت العملية كاملة")


def _entry_lines_ordered(session, entry):
    return list(session.execute(
        select(JournalLine).where(JournalLine.entry_id == entry.id)
        .order_by(JournalLine.id)).scalars().all())


def _prove_invoice_accounts(session, inv: Invoice, entry: JournalEntry, kind: InvoiceKind,
                            needed_items: set[int]) -> None:
    items = {i: session.get(Item, i) for i in
             {l.item_id for l in inv.lines} | set(needed_items)}
    for i, it in items.items():
        if it is None:
            raise CorrectionEntryError(f"الصنف {i} غير موجود")
    lines = _entry_lines_ordered(session, entry)

    if kind == InvoiceKind.SALES:
        for it in items.values():
            if it.cogs_account_id is None or it.inventory_account_id is None:
                raise CorrectionEntryError(f"الصنف {it.sku}: حسابات المخزون/التكلفة غير محدَّدة")
        movs = list(session.execute(
            select(InventoryMovement).where(
                InventoryMovement.source_type == "sales_invoice",
                InventoryMovement.source_id == inv.id,
                InventoryMovement.direction == MovementDirection.OUT,
            ).order_by(InventoryMovement.id)).scalars().all())
        if not movs:
            raise CorrectionEntryError(f"{inv.invoice_no}: لا حركات OUT — لا دليل على المساهمات")
        contribs = [(m.item_id, calculate_cogs(_dec(m.unit_cost), _dec(m.quantity)))
                    for m in movs]
        cogs_cur = {i: it.cogs_account_id for i, it in items.items()}
        inv_cur = {i: it.inventory_account_id for i, it in items.items()}
        debits = [(l.account_id, money(l.debit_base)) for l in lines[1:]
                  if money(l.debit_base) > 0]               # السطر 0 = نقد/ذمم
        credits = [(l.account_id, money(l.credit_base)) for l in lines
                   if money(l.credit_base) > 0]
        _prove_side("COGS", contribs, cogs_cur, needed_items,
                    lambda n: debits, [a for a, _ in debits],
                    drop_zero=True, invoice_no=inv.invoice_no, entry_ref=entry.ref_no)
        _prove_side("Inventory", contribs, inv_cur, needed_items,
                    lambda n: credits[-n:] if n else [], [a for a, _ in credits],
                    drop_zero=True, invoice_no=inv.invoice_no, entry_ref=entry.ref_no)
    else:
        for it in items.values():
            if it.inventory_account_id is None:
                raise CorrectionEntryError(f"الصنف {it.sku}: حساب المخزون غير محدَّد")
        totals = compute_invoice_totals(inv)
        contribs = [(lt.line.item_id, money(lt.net_after_all_discounts)) for lt in totals.lines]
        has_tax = any(money(lt.tax_amount) != 0 for lt in totals.lines)
        debits = [(l.account_id, money(l.debit)) for l in lines if money(l.debit) > 0]
        if has_tax and debits:
            debits = debits[:-1]                            # آخر سطر مدين = الضريبة
        inv_cur = {i: it.inventory_account_id for i, it in items.items()}
        _prove_side("Inventory", contribs, inv_cur, needed_items,
                    lambda n: debits, [a for a, _ in debits],
                    drop_zero=False, invoice_no=inv.invoice_no, entry_ref=entry.ref_no)


def _plan(session: Session, deltas: list[CorrectionEventDeltaRecord]) -> _Plan:
    """قراءة فقط. يبني الخطة أو يرفض. لا كتابة هنا إطلاقاً.

    ترتيب العمل: (1) تصنيف كل Delta وجمع الفواتير المتأثرة، (2) **إثبات**
    مطابقة حساب كل صنف مطلوب لقيد مستنده الأصلي (_prove_invoice_accounts)،
    (3) فقط بعد نجاح كل الإثباتات تُبنى أسطر القيد من حسابات الأصناف المُثبَتة."""
    mv_by_id = {
        m.id: m for m in session.execute(
            select(InventoryMovement).where(
                InventoryMovement.id.in_([d.movement_id for d in deltas]))
        ).scalars().all()
    }
    plan = _Plan()
    sale_cogs: list[tuple[int, Decimal]] = []            # (item_id, ΔCOGS)
    # invoice_id -> (invoice, entry, أصناف مطلوب إثبات حسابها)
    sales_inv: dict[int, tuple[Invoice, JournalEntry, set[int]]] = {}
    purch_inv: dict[int, tuple[Invoice, JournalEntry]] = {}
    transfer_sum = _ZERO

    for d in deltas:
        mv = mv_by_id.get(d.movement_id)
        if mv is None:
            raise CorrectionEntryError(f"Delta {d.id}: الحركة {d.movement_id} غير موجودة")
        if d.cost_basis_exception or d.delta_inventory_value is None:
            raise CorrectionEntryError(
                f"Delta {d.id} (حركة {mv.id}) يحمل cost_basis_exception — "
                f"مستبعَد من أي تصحيح نهائي (DL-003): أُلغي الحدث كاملاً"
            )
        if mv.source_type not in _SUPPORTED_SOURCES:
            raise CorrectionEntryError(
                f"حركة {mv.id} من نوع غير مدعوم في التصحيح: '{mv.source_type}' "
                f"(المرتجعات/الإلغاء خارج مسار Candidate — قرار C)"
            )
        val = _dec(d.delta_inventory_value)

        if mv.source_type == "stock_transfer":
            transfer_sum += val
            plan.links.append(_LinkSpec(d.id, TREAT_TRANSFER, _ZERO))
        elif mv.direction == MovementDirection.OUT:
            # sales_invoice OUT (الوحيد المدعوم هنا): ΔCOGS = −Δinventory_value
            if mv.source_type != "sales_invoice":
                raise CorrectionEntryError(
                    f"حركة OUT {mv.id} من نوع '{mv.source_type}' بلا تعريف COGS")
            d_cogs = -val
            if abs(d_cogs) > _TOL:
                inv, entry = _invoice_entry(session, mv, InvoiceKind.SALES)
                rec = sales_inv.setdefault(inv.id, (inv, entry, set()))
                rec[2].add(mv.item_id)
                sale_cogs.append((mv.item_id, d_cogs))
                plan.sale_count += 1
                if plan.min_entry_date is None or entry.entry_date > plan.min_entry_date:
                    plan.min_entry_date = entry.entry_date
            plan.links.append(_LinkSpec(d.id, TREAT_COGS, d_cogs))
        else:
            # IN خارجي: مدخلات — يحملها المستند المُصحَّح لا هذا القيد
            if abs(val) > _TOL:
                if mv.source_type != "purchase_invoice":
                    raise CorrectionEntryError(
                        f"حركة IN {mv.id} من نوع '{mv.source_type}' بقيمة مصحَّحة "
                        f"غير صفرية: إعادة ترحيلها غير مدعومة هنا")
                inv, entry = _invoice_entry(session, mv, InvoiceKind.PURCHASE)
                purch_inv[inv.id] = (inv, entry)
            plan.links.append(_LinkSpec(d.id, TREAT_DOCS, _ZERO))

    if abs(transfer_sum) > _TOL:
        raise CorrectionEntryError(
            f"Σ Δ التحويلات = {transfer_sum} ≠ 0 — يخالف G-005 B.3؛ Candidate غير سليم"
        )

    # ---- إثبات حسابات الأصناف (DL-006: لا ترحيل قبل الإثبات) -----------------
    for inv, entry, needed in sales_inv.values():
        _prove_invoice_accounts(session, inv, entry, InvoiceKind.SALES, needed)
    for inv, entry in purch_inv.values():
        # إعادة ترحيل مستند الشراء تعيد ترحيل **كل** أصنافه، فكلها مطلوبة
        _prove_invoice_accounts(session, inv, entry, InvoiceKind.PURCHASE,
                                {l.item_id for l in inv.lines})

    # ---- بناء الأسطر من حسابات الأصناف المُثبَتة ------------------------------
    pair_net: dict[tuple[int, int], Decimal] = {}
    item_cache: dict[int, Item] = {}
    for item_id, d_cogs in sale_cogs:
        it = item_cache.get(item_id) or session.get(Item, item_id)
        item_cache[item_id] = it
        key = (it.cogs_account_id, it.inventory_account_id)
        pair_net[key] = pair_net.get(key, _ZERO) + d_cogs

    for (cogs_acc, inv_acc), net in pair_net.items():
        amount = money(net)
        if amount == 0:
            continue
        if amount > 0:
            dr, cr = (cogs_acc, "D"), (inv_acc, "C")
        else:
            dr, cr = (inv_acc, "D"), (cogs_acc, "C")
        amount = abs(amount)
        plan.lines[dr] = plan.lines.get(dr, _ZERO) + amount
        plan.lines[cr] = plan.lines.get(cr, _ZERO) + amount

    if not plan.lines:
        raise CorrectionEntryError(
            "ΔCOGS الكلي = 0 — لا قيد تصحيح. سياسة Zero-effect غير محسومة "
            "(G-006 OPEN)؛ لا يُرحَّل ولا يُختلَق حل"
        )
    return plan


def _resolve_entry_date(entry_date: date | None, plan: _Plan) -> date:
    d = entry_date or date.today()                   # DL-007: تاريخ الترحيل الفعلي
    if plan.min_entry_date is not None and d < plan.min_entry_date:
        raise CorrectionEntryError(
            f"تاريخ القيد {d} أسبق من تاريخ قيد بيع أصلي متأثر "
            f"({plan.min_entry_date}) — غير مسموح (نفس قاعدة reverse())"
        )
    return d


def _build_intent(session, event_id: int, entry_date: date, plan: _Plan) -> AccountingIntent:
    lines: list[LineIntent] = []
    # مدين أولاً ثم دائن، بترتيب حسابات ثابت (قابلية التنبؤ في الاختبارات)
    for (acc, side) in sorted(plan.lines, key=lambda k: (k[1] != "D", k[0])):
        amt = plan.lines[(acc, side)]
        dr, cr = (amt, _ZERO) if side == "D" else (_ZERO, amt)
        lines.append(LineIntent(account_id=acc, debit_raw=dr, credit_raw=cr,
                                debit_base=dr, credit_base=cr))
    return AccountingIntent(
        entry_date=entry_date, currency_code=get_base_currency(session),
        exchange_rate=Decimal("1"), source_type=SOURCE_TYPE,
        description=f"قيد تصحيح تكلفة تاريخية — حدث تصحيح #{event_id}",
        source_id=event_id, lines=lines,
    )


def _write_links(session, correction: CorrectionEventAccountingCorrection,
                 plan: _Plan) -> None:
    for spec in plan.links:
        session.add(CorrectionEventDeltaJournalLink(
            delta_record_id=spec.delta_id,
            accounting_correction_id=correction.id,
            treatment=spec.treatment, cogs_amount=spec.cogs_amount,
        ))
    session.flush()


def _verify_posted(session, entry: JournalEntry, correction, plan: _Plan,
                   deltas: list[CorrectionEventDeltaRecord]) -> None:
    """فحص ذاتي قبل تسليم الاستدعاء: القيد = الخطة حرفياً، والربط مكتمل."""
    session.expire(entry, ["lines"])
    if entry.status != JournalEntryStatus.POSTED or not entry.is_balanced():
        raise CorrectionEntryError("القيد المرحَّل غير POSTED أو غير متوازن")
    got: dict[tuple[int, str], Decimal] = {}
    for l in entry.lines:
        if money(l.debit_base) > 0:
            got[(l.account_id, "D")] = got.get((l.account_id, "D"), _ZERO) + money(l.debit_base)
        if money(l.credit_base) > 0:
            got[(l.account_id, "C")] = got.get((l.account_id, "C"), _ZERO) + money(l.credit_base)
    if got != plan.lines:
        raise CorrectionEntryError(f"أسطر القيد لا تطابق الخطة: {got} ≠ {plan.lines}")
    link_delta_ids = set(session.execute(
        select(CorrectionEventDeltaJournalLink.delta_record_id).where(
            CorrectionEventDeltaJournalLink.accounting_correction_id == correction.id)
    ).scalars().all())
    if link_delta_ids != {d.id for d in deltas}:
        raise CorrectionEntryError("سجل الربط لا يغطي كل Deltas الـCandidate")


def post_correction_entry(
    session: Session, correction_event_id: int, candidate_state_id: int,
    entry_date: date | None = None,
) -> CorrectionEntryResult:
    """يرحِّل قيد التصحيح + الربط + حالة الحدث كوحدة واحدة (كله أو لا شيء).

    لا commit بعد الكتابة — المستدعي يلتزم بعد النجاح. أي استثناء بعد بدء
    الكتابة ⟹ rollback كامل داخل هذه الدالة."""
    _require_clean_session(session)
    event, cand, deltas = _load_event_and_deltas(
        session, correction_event_id, candidate_state_id)
    plan = _plan(session, deltas)                       # رفض مبكر، قراءة فقط
    when = _resolve_entry_date(entry_date, plan)
    intent = _build_intent(session, event.id, when, plan)
    event_id = event.id
    delta_ids = [d.id for d in deltas]

    session.commit()   # تفريغ معاملة القراءة قبل حجز ref_no (انظر ملاحظة 1)
    try:
        entry = post_immediate(session, intent)
        correction = CorrectionEventAccountingCorrection(
            correction_event_id=event_id, journal_entry_id=entry.id)
        session.add(correction)
        session.flush()
        _write_links(session, correction, plan)
        ev = session.get(CorrectionEvent, event_id)
        ev.status = CorrectionEventStatus.ACCOUNTING_CORRECTION
        session.flush()
        fresh = list(session.execute(
            select(CorrectionEventDeltaRecord)
            .where(CorrectionEventDeltaRecord.id.in_(delta_ids))).scalars().all())
        _verify_posted(session, entry, correction, plan, fresh)
    except Exception:
        session.rollback()
        raise

    return CorrectionEntryResult(
        journal_entry=entry, accounting_correction=correction,
        links_count=len(plan.links),
        cogs_links_count=sum(1 for s in plan.links if s.treatment == TREAT_COGS),
    )
