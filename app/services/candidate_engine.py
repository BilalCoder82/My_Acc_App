"""G-007 — Candidate Engine (تصميم G-007_INSTRUCTIONS.md، عقد G-005، قرارات DL-002/003/004)

يبني CandidateState واحداً + DeltaRecord لكل حركة داخل ImpactScope حدث تصحيح
(بناه F مسبقاً)، بمقارنة تسلسل Baseline (كما رُحِّل فعلاً) بتسلسل Candidate
(نفس الحركات، نفس الترتيب الأساسي، مع تطبيق القيم المصحَّحة المُمرَّرة صراحة).

نطاق مقصود (لا أكثر):
- **Candidate واحد لكل استدعاء**، بافتراض ترتيب واحد صريح لكل TieGroup =
  ترتيب الترحيل الأصلي (تاريخ ثم id). يُسجَّل هذا الافتراض صراحة في
  OrderingAssumption/CandidateOrderingLink — لا ترتيب ضمني. توليد عدة
  Candidates بترتيبات بديلة (فضاء Q2.5 التوافقي) خارج هذه المرحلة.
- **القيم المصحَّحة تُمرَّر من المستدعي** (corrected_values)؛ المحرك لا يقرر
  ما الصحيح محاسبياً، فقط يحسب أثره وفق العقد.
- لا AccountingCorrection، لا JournalEntry، لا post_immediate، لا حساب تصحيح.

قواعد العقد المُطبَّقة:
- DL-002: c(OUT) = A_{k-1} = V_{k-1}/Q_{k-1} لنفس تسلسل Candidate، حين
  Q_{k-1} > 0 فقط. لا accumulate_average_cost()، لا تصفير عند Q<=0.
- DL-003: Q_{k-1} <= 0 قبل OUT ⟹ لا تكلفة بديلة. الصف يُوسَم
  cost_basis_exception=True و delta_inventory_value=NULL، والمستودع يُوسَم
  "ملوَّثاً" (V مجهولة من هنا فصاعداً): كل OUT لاحق في نفس المستودع يُوسَم
  استثناءً أيضاً، ولا نفترض أن تكلفة Candidate لتلك الحركة = تكلفة Baseline
  (هذا نفسه fallback). Transfer-IN مقابل لـOUT استثنائي يرث الاستثناء.
- Transfer: ساقا التحويل هوية واحدة — تكلفة ساق IN في Candidate = تكلفة ساق
  OUT في Candidate (لا القيمة المخزَّنة). لا JournalEntry، ولا COGS.
- DL-004: UNIQUE(candidate_state_id, movement_id) على مستوى DB.
- delta_inventory_value(m) = σ·q_cand·c_cand − σ·q_base·c_base.
- delta_quantity: قيمة regime-level تُخزَّن عند الصف الحامل (أول صف في كل
  مستودع، وكل صف تتغيّر عنده)، وNULL عند الباقي = "نفس آخر قيمة" (forward-fill).

حدود معروفة (موثَّقة، لا مخفية):
- حركات المرتجعات (sales_return/purchase_return) تُعامَل كحركات IN/OUT عادية
  بتكلفتها المخزَّنة — Returns خارج هذا المسار بقرار سابق (C)، ولا يُربَط
  المرتجع بتكلفة البيع الأصلي في Candidate.
- الذرّية: المحرك لا يستدعي commit أبداً. كل الحساب يجري بالذاكرة أولاً؛ لا
  كتابة قبل اكتمال الحساب. عند أي استثناء يجب على المستدعي rollback (لا شيء
  التُزِم من هنا أصلاً)، والتزامه هو نقطة الالتزام الوحيدة.
"""
from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CorrectionEvent, CorrectionEventCandidateOrderingLink,
    CorrectionEventCandidateState, CorrectionEventDeltaRecord,
    CorrectionEventImpactScopeElement, CorrectionEventOrderingAssumption,
    CorrectionEventTieGroup, InventoryMovement, MovementDirection,
)

_ZERO = Decimal("0")


def _dec(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _sign(m: InventoryMovement) -> int:
    return 1 if m.direction == MovementDirection.IN else -1


def _load_scope_movements(session: Session, event_id: int) -> list[InventoryMovement]:
    ids = session.execute(
        select(CorrectionEventImpactScopeElement.source_id).where(
            CorrectionEventImpactScopeElement.correction_event_id == event_id,
            CorrectionEventImpactScopeElement.source_type == "inventory_movement",
        )
    ).scalars().all()
    if not ids:
        return []
    return list(session.execute(
        select(InventoryMovement).where(InventoryMovement.id.in_(ids))
    ).scalars().all())


def _prefix_state(session: Session, item_id: int, warehouse_id: int,
                  first_scope_date, scope_ids: set[int]) -> tuple[Decimal, Decimal]:
    """(Q0, V0) قبل أول حركة نطاق في المستودع: كل حركات (item, wh) خارج النطاق
    بتاريخ أقدم — مشتركة حرفياً بين Baseline وCandidate (لا تتأثر بالتصحيح).
    V0 تُجمَع بقيمها المخزَّنة (نفس تعريف G-005: σ·q·c)."""
    rows = session.execute(
        select(InventoryMovement).where(
            InventoryMovement.item_id == item_id,
            InventoryMovement.warehouse_id == warehouse_id,
        )
    ).scalars().all()
    q0, v0 = _ZERO, _ZERO
    for m in rows:
        if m.id in scope_ids:
            continue
        if m.movement_date >= first_scope_date:
            # عدم انتهاك استنفاد البيانات في F: حركة خارج النطاق بعد بدايته
            raise ValueError(
                f"النطاق ليس لاحقة متصلة في مستودع {warehouse_id}: حركة {m.id} "
                f"خارج النطاق بتاريخ {m.movement_date} >= {first_scope_date}"
            )
        q0 += _sign(m) * _dec(m.quantity)
        v0 += _sign(m) * _dec(m.quantity) * _dec(m.unit_cost)
    return q0, v0


def build_candidate(
    session: Session,
    correction_event_id: int,
    corrected_values: dict[int, tuple] | None = None,
    label: str = "A",
) -> CorrectionEventCandidateState:
    """corrected_values: {movement_id: (quantity, unit_cost)} — القيم المصحَّحة
    لحركات IN. للـOUT يُسمَح بتصحيح الكمية فقط (unit_cost=None)؛ تكلفة OUT
    مُشتقَّة (DL-002) وليست مُدخَلاً — تمريرها خطأ يُرفَض صراحة."""
    corrected_values = corrected_values or {}
    event = session.get(CorrectionEvent, correction_event_id)
    if event is None:
        raise ValueError(f"CorrectionEvent غير موجود: {correction_event_id}")

    scope = _load_scope_movements(session, correction_event_id)
    if not scope:
        raise ValueError("ImpactScope فارغ — نفِّذ build_impact_scope أولاً")
    scope_ids = {m.id for m in scope}
    by_id = {m.id: m for m in scope}

    for mid, (cq, cc) in corrected_values.items():
        if mid not in by_id:
            raise ValueError(f"تصحيح لحركة خارج النطاق: {mid}")
        if by_id[mid].direction == MovementDirection.OUT and cc is not None:
            raise ValueError(
                f"حركة OUT {mid}: unit_cost مُشتقَّة (DL-002) وليست مُدخَلاً للتصحيح"
            )

    # ---- 1) الحالة الابتدائية لكل مستودع (خارج النطاق، مشتركة) -------------
    wh_ids = sorted({m.warehouse_id for m in scope})
    state: dict[int, dict] = {}
    for wh in wh_ids:
        wh_scope = [m for m in scope if m.warehouse_id == wh]
        first_date = min(m.movement_date for m in wh_scope)
        q0, v0 = _prefix_state(session, event.item_id, wh, first_date, scope_ids)
        state[wh] = {"Qc": q0, "Vc": v0, "Qb": q0, "tainted": False,
                     "prev_dq": None}

    # ---- 2) الترتيب: افتراض صريح واحد = ترتيب الترحيل (تاريخ ثم id) --------
    # الترتيب **داخل كل مستودع** فقط. أما بين المستودعات فالمعالجة تتبع
    # التبعية الوحيدة المعتمَدة (Transfer): ساق IN تنتظر ساق OUT المقابلة
    # (G-007 مراجعة: هوية Transfer مستقلة عن ترتيب id/معالجة الساقين).
    ordered = sorted(scope, key=lambda m: (m.movement_date, m.id))
    queues: dict[int, list[InventoryMovement]] = {wh: [] for wh in wh_ids}
    for m in ordered:
        queues[m.warehouse_id].append(m)
    out_leg: dict[int, int] = {                       # source_id -> id ساق OUT في النطاق
        m.source_id: m.id for m in scope
        if m.source_type == "stock_transfer" and m.direction == MovementDirection.OUT
    }
    for mid, (cq, cc) in corrected_values.items():
        mv = by_id[mid]
        if (cc is not None and mv.direction == MovementDirection.IN
                and mv.source_type == "stock_transfer" and mv.source_id in out_leg):
            raise ValueError(
                f"حركة Transfer-IN {mid}: unit_cost = تكلفة ساق OUT في Candidate "
                f"(هوية Transfer) وليست مُدخَلاً للتصحيح"
            )

    # TieGroups: (مستودع، تاريخ) بأكثر من حركة نطاق
    groups: dict[tuple[int, object], list[int]] = defaultdict(list)
    for m in ordered:
        groups[(m.warehouse_id, m.movement_date)].append(m.id)
    tie_groups = {k: v for k, v in groups.items() if len(v) > 1}

    # ---- 3) الحساب بالذاكرة (لا كتابة بعد) -----------------------------------
    transfer_cost: dict[int, Decimal | None] = {}   # source_id -> تكلفة Candidate لساق OUT
    rows_out: list[dict] = []

    processed: set[int] = set()

    def process(m: InventoryMovement) -> None:
        st = state[m.warehouse_id]
        sgn = _sign(m)
        q_base = _dec(m.quantity)
        c_base = _dec(m.unit_cost)
        cq, cc = corrected_values.get(m.id, (None, None))
        q_cand = _dec(cq) if cq is not None else q_base

        exception = False
        c_cand: Decimal | None

        if m.direction == MovementDirection.IN:
            if m.source_type == "stock_transfer" and m.source_id in transfer_cost:
                c_cand = transfer_cost[m.source_id]      # هوية Transfer: تكلفة ساق OUT
                if c_cand is None:
                    exception = True                      # ورث استثناء المصدر
            elif cc is not None:
                c_cand = _dec(cc)
            else:
                c_cand = c_base
            if exception:
                st["tainted"] = True
        else:  # OUT
            if st["tainted"] or st["Qc"] <= 0:
                # DL-003: لا تكلفة بديلة. تلويث المستودع من هنا فصاعداً.
                exception = True
                c_cand = None
                st["tainted"] = True
            else:
                c_cand = st["Vc"] / st["Qc"]              # DL-002: A_{k-1}
            if m.source_type == "stock_transfer":
                transfer_cost[m.source_id] = c_cand

        # الكمية: تُحدَّث دائماً (لا تعتمد على التكلفة)
        st["Qb"] += sgn * q_base
        st["Qc"] += sgn * q_cand

        # القيمة التراكمية للـCandidate
        if c_cand is not None and not st["tainted"]:
            st["Vc"] += sgn * q_cand * c_cand
        elif c_cand is not None and st["tainted"] and m.direction == MovementDirection.IN:
            # IN عادي بعد تلوث: V مجهولة أصلاً؛ لا نراكم قيمة (تبقى غير معرَّفة)
            pass

        # delta_inventory_value
        if exception or c_cand is None:
            d_val = None
        else:
            d_val = sgn * q_cand * c_cand - sgn * q_base * c_base

        # delta_quantity: regime-level — صف حامل عند أول صف ولكل تغيّر
        dq = st["Qc"] - st["Qb"]
        if st["prev_dq"] is None or dq != st["prev_dq"]:
            d_qty = dq
        else:
            d_qty = None
        st["prev_dq"] = dq

        rows_out.append({"movement_id": m.id, "delta_quantity": d_qty,
                         "delta_inventory_value": d_val,
                         "cost_basis_exception": exception})
        processed.add(m.id)

    def _waits_for_out(m: InventoryMovement) -> bool:
        return (m.direction == MovementDirection.IN and m.source_type == "stock_transfer"
                and m.source_id in out_leg and out_leg[m.source_id] not in processed)

    pos = {wh: 0 for wh in wh_ids}
    remaining = len(ordered)
    while remaining:
        progressed = False
        for wh in wh_ids:
            q = queues[wh]
            while pos[wh] < len(q) and not _waits_for_out(q[pos[wh]]):
                process(q[pos[wh]])
                pos[wh] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            # دورة تبعية (تحويلان بنفس التاريخ بين مستودعين بترتيبين متقاطعين):
            # لا ترتيب صحيح يمكن اختراعه — فشل صريح لا أرقام صامتة.
            raise ValueError(
                "دورة تبعية بين ساقي Transfer في نفس التاريخ — لا يمكن تحديد "
                "ترتيب معالجة بلا افتراض جديد"
            )

    # ---- 4) الكتابة (بعد اكتمال الحساب كاملاً؛ بلا commit) --------------------
    candidate = CorrectionEventCandidateState(
        correction_event_id=correction_event_id, label=label)
    session.add(candidate)
    session.flush()

    for (wh, d), mids in tie_groups.items():
        note = f"warehouse={wh}"
        tg = session.execute(
            select(CorrectionEventTieGroup).where(
                CorrectionEventTieGroup.correction_event_id == correction_event_id,
                CorrectionEventTieGroup.tied_movement_date == d,
                CorrectionEventTieGroup.note == note,
            )
        ).scalars().first()
        if tg is None:
            tg = CorrectionEventTieGroup(
                correction_event_id=correction_event_id,
                tied_movement_date=d, note=note)
            session.add(tg)
            session.flush()
        oa = CorrectionEventOrderingAssumption(
            tie_group_id=tg.id,
            ordering_representation=json.dumps(
                {"basis": "posting_order(date,id)", "movement_ids": mids}))
        session.add(oa)
        session.flush()
        session.add(CorrectionEventCandidateOrderingLink(
            candidate_state_id=candidate.id,
            ordering_assumption_id=oa.id, tie_group_id=tg.id))

    for r in rows_out:
        session.add(CorrectionEventDeltaRecord(
            candidate_state_id=candidate.id, **r))
    session.flush()          # لا commit — نقطة الالتزام الوحيدة للمستدعي
    return candidate
