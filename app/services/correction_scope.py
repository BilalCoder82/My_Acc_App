"""
PHASE 3B-5-F — Impact Scope Construction
=============================================
يبني الرسم البياني (graph) لنطاق تأثير CorrectionEvent موجود مسبقاً
(بحالة CANDIDATE من 3B-5-E) — أين يمتد الأثر، لا ما هي القيمة المالية
المصحَّحة. يطبِّق حصراً القرارات المغلقة بـ3B-5-F_DESIGN_CLOSED_INDEX.md
(FD-001 REV1 إلى FD-006) وقراري ID-001/ID-002/ID-003 الإضافيين
(Implementation Design Decisions، لا FD جديدة).

FD-001 REV1 — Data-Exhaustion Termination (لا Inventory-State):
--------------------------------------------------------------------
القرار الأصلي (`total_qty<=0` كإغلاق) أُعيد فتحه وأُلغِيَ كلياً — مُثبَت
جبرياً (راجع `3B-5-F_FD001_REOPENED_DISCOVERY.md`) أن التصفير المتزامن
بين حالة حقيقية وحالة افتراضية **مستحيل بنيوياً** حين تحمل حركة الجذر
كمية غير صفرية (Case B — النمط التشغيلي الفعلي الوحيد الذي تُنتجه E
حالياً، بما أنها تكتشف عبر INSERT فقط). حتى الحالة التي أُثبِتت آمنة
نظرياً (Case A، هبوط دقيق للصفر) غير قابلة للاستخدام هنا: لا وسيلة
موثوقة وقت التشغيل لمعرفة أن حدثاً معيَّناً هو Case A فعلاً، والعبور
السالب غير آمن حتى ضمنها (يُخفي الفرق، لا يُلغيه).

**البديل المُعتمَد**: `Traversal termination is data-exhaustion-based,
not inventory-state-based`. لا `total_qty` بأي صياغة (`<=0`,`==0`,`<0`)
تُستخدَم كإغلاق. كل فرع (item, warehouse) يُجتاز **حتى نهاية البيانات
الحقيقية المعروفة** فقط — لا توقف مبكر بسبب أي حالة كمية. الاتجاه
الآمن المُعتمَد: الإفراط بالشمول (over-inclusion) مقبول، النقصان
(under-inclusion) غير مقبول — F يبني نطاقاً يُستهلَك لاحقاً بـG، لا
نتيجة حساب نهائية.

حد NO RECALCULATION (مطلق، من ميثاق F الأصلي) — يُطبَّق هنا حرفياً:
--------------------------------------------------------------------
هذه الوحدة لا تستدعي accumulate_average_cost() ولا get_item_stock_summary()
إطلاقاً، ولا تقرأ أو تحسب unit_cost/average_cost بأي مكان — ولم تعد
تحتاج أي رصيد كمي أصلاً بعد إلغاء الإغلاق الكمي (FD-001 REV1). التصحيح
المالي الفعلي يبقى معرفة تصميمية مغلقة لمرحلة G لاحقة منفصلة — لا
يُنفَّذ هنا.

ID-001 — Root Movement Membership:
-----------------------------------
حركة الجذر (المُشار إليها بـCorrectionEventRootCause) تُسجَّل هي نفسها
كـImpactScopeElement — بلا role/is_root جديد، بلا علاقة CAUSES اصطناعية؛
المرجع القائم عبر CorrectionEventRootCause يكفي لتحديد كونها الجذر.

ID-002 — Same-Date Atomic Group:
----------------------------------
كل الحركات المتعادلة بـmovement_date تُعامَل كمجموعة ذرية واحدة —
تُسجَّل معاً، بلا أي ترتيب داخلي مُخترَع (id/insertion order/database
row order). إن ظهرت مجموعة بأكثر من حركة واحدة ضمن الفرع المُجتاز
فعلياً لحدث معيَّن، يُصبح chronology_basis=ASSUMED لذلك الحدث (KNOWN
غير ذلك) — بلا أي علاقة تلقائية مع scope_completeness (FD-005).

ID-003 — Inter-Date Propagation Edge Semantics (مغلَق):
--------------------------------------------------------
حافة PROPAGATES_TO بين مجموعتي تاريخ متتاليتين تُنشأ فقط حين لا يوجد
غموض إسناد على الأقل بأحد الطرفين: 1×1 (حافة واحدة)، 1×N (fan-out)،
N×1 (fan-in). في حالة N×M (كلا الطرفين أكثر من عنصر) **لا تُنشأ أي
حافة إطلاقاً** — كل عناصر المجموعتين تبقى أعضاء Scope بصورة طبيعية،
وغياب الحافة بينهما لا يجعل scope_completeness=PARTIAL. لا اختيار
representative اصطناعي، لا آلية selective propagation جديدة.

FD-005 (مُعدَّلة تبعياً بـFD-001 REV1، لا إعادة فتح لجوهرها):
----------------------------------------------------------------
`COMPLETE` = وصل الـtraversal لنهاية كل البيانات الحقيقية المعروفة لكل
فرع مفتوح. بما أنه لا يوجد الآن أي إغلاق مبكر كمي، **كل traversal
مكتمل البيانات ينتهي COMPLETE** — لا حالة `PARTIAL` ناتجة عن الـ
traversal الكمي إطلاقاً بهذا التصميم. لو ظهر مستقبلاً سبب مشروع لعدم
إكمال الـtraversal (حد أداء مُعتمَد لاحقاً مثلاً)، يُستخدَم `PARTIAL`
وفق تصميم مستقل يُثبَت وقته — لا افتراض مُسبَق بطبيعته الآن.

نقطة تشغيلية غير محسومة، خارج نطاق هذه الوحدة عمداً:
-------------------------------------------------------------
متى/من يستدعي build_impact_scope() فعلياً لحدث CANDIDATE جديد أنشأته
correction_detection.py؟ هذه الوحدة **لا** تُستدعى تلقائياً من أي hook —
لا وصل مباشر بـcorrection_detection.py. ربطها بأي مشغِّل قرار سياسة
صريح خارج ميثاق F الأصلي ("NO POLICY DECISIONS: background jobs") —
لم يُتَّخذ هنا.

قيد أداء معروف، غير مُعالَج بهذا التصميم عمداً (راجع FD-001 REV1 §7):
-------------------------------------------------------------------------
بإلغاء الإغلاق الكمي، طول أي سلسلة traversal أصبح محكوماً فقط بطول
البيانات الحقيقية المعروفة — بلا حد أقصى مفروض هنا. أي حد أداء مستقبلي
قرار منفصل يحتاج Correctness Proof خاصاً به (هل يُعيد إدخال نفس خطأ
"التوقف المبكر غير المُثبَت" بصورة جديدة؟) — لا يُفتَرض آمناً بمجرد
تصنيفه أداءً.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    InventoryMovement, MovementDirection, StockTransfer,
    CorrectionEvent, CorrectionEventRootCause,
    CorrectionEventImpactScopeElement, CorrectionEventImpactScopeRelationship,
    ImpactScopeElementKind, ImpactScopeRelationshipType,
    ScopeCompleteness, ChronologyBasis,
)


# ---------------------------------------------------------------------------
# 1. استرجاع الحركات اللاحقة (لا حساب كمي أو مالي بهذه الوحدة إطلاقاً —
#    FD-001 REV1 ألغى الحاجة لأي رصيد كمي؛ راجع الـdocstring أعلاه)
# ---------------------------------------------------------------------------

def _fetch_subsequent_movements(session: Session, item_id: int, warehouse_id: int, from_date):
    """كل حركات (item, warehouse) بتاريخ >= from_date. الـORDER BY هنا
    لترتيب *المجموعات* بين بعضها فقط — لا يضمن أي ترتيب للصفوف المتعادلة
    بنفس التاريخ (SQL لا يضمن ذلك) — التجميع اللاحق (date-group) لا
    يعتمد على أي افتراض ترتيب صفّي إطلاقاً (Finding B يبقى غير محسوم)."""
    return session.execute(
        select(InventoryMovement).where(
            InventoryMovement.item_id == item_id,
            InventoryMovement.warehouse_id == warehouse_id,
            InventoryMovement.movement_date >= from_date,
        ).order_by(InventoryMovement.movement_date)
    ).scalars().all()


# ---------------------------------------------------------------------------
# 2. عبور StockTransfer (FD-002 — دلالي، لا زمني)
# ---------------------------------------------------------------------------

def find_transfer_destination(session: Session, out_movement: InventoryMovement) -> InventoryMovement | None:
    """لساق OUT تحويل، يُرجع ساق IN المقابلة — عبر هوية التحويل المشتركة
    (source_id) ومستودع الوجهة الصريح (StockTransfer.to_warehouse_id)،
    أبداً بمقارنة movement_date أو id أو ترتيب إدراج."""
    if out_movement.source_type != "stock_transfer":
        return None
    transfer = session.get(StockTransfer, out_movement.source_id)
    if transfer is None:
        return None
    return session.execute(
        select(InventoryMovement).where(
            InventoryMovement.source_type == "stock_transfer",
            InventoryMovement.source_id == transfer.id,
            InventoryMovement.warehouse_id == transfer.to_warehouse_id,
        )
    ).scalars().first()


# ---------------------------------------------------------------------------
# 3. تسجيل العضوية والعلاقات (FD-003، §5.4)
# ---------------------------------------------------------------------------

def record_scope_element(session: Session, correction_event_id: int, kind: ImpactScopeElementKind,
                          source_type: str, source_id: int) -> CorrectionEventImpactScopeElement:
    """ينشئ CorrectionEventImpactScopeElement — بتحقق يدوي من عدم التكرار
    أولاً (القيد الفريد موجود بالـschema أصلاً، لكن التحقق اليدوي يتجنب
    IntegrityError وسط traversal طويل، idempotent بأمان)."""
    existing = session.execute(
        select(CorrectionEventImpactScopeElement).where(
            CorrectionEventImpactScopeElement.correction_event_id == correction_event_id,
            CorrectionEventImpactScopeElement.source_type == source_type,
            CorrectionEventImpactScopeElement.source_id == source_id,
        )
    ).scalars().first()
    if existing is not None:
        return existing
    element = CorrectionEventImpactScopeElement(
        correction_event_id=correction_event_id, kind=kind,
        source_type=source_type, source_id=source_id,
    )
    session.add(element)
    session.flush()
    return element


def record_scope_relationship(session: Session, correction_event_id: int,
                               from_element: CorrectionEventImpactScopeElement,
                               to_element: CorrectionEventImpactScopeElement,
                               relationship_type: ImpactScopeRelationshipType) -> CorrectionEventImpactScopeRelationship:
    """ينشئ CorrectionEventImpactScopeRelationship — بتحقق يدوي من عدم
    تكرار نفس الحافة (from,to,type): لا حماية schema لهذا (§5.4، مؤكَّد
    تجريبياً: لا UniqueConstraint ولا CHECK على هذا الجدول)."""
    existing = session.execute(
        select(CorrectionEventImpactScopeRelationship).where(
            CorrectionEventImpactScopeRelationship.correction_event_id == correction_event_id,
            CorrectionEventImpactScopeRelationship.from_element_id == from_element.id,
            CorrectionEventImpactScopeRelationship.to_element_id == to_element.id,
            CorrectionEventImpactScopeRelationship.relationship_type == relationship_type,
        )
    ).scalars().first()
    if existing is not None:
        return existing
    rel = CorrectionEventImpactScopeRelationship(
        correction_event_id=correction_event_id,
        from_element_id=from_element.id, to_element_id=to_element.id,
        relationship_type=relationship_type,
    )
    session.add(rel)
    session.flush()
    return rel


# ---------------------------------------------------------------------------
# 4. Traversal الأساسي (A + B مدمجَين، مع حالة مشتركة لضمان termination)
# ---------------------------------------------------------------------------

@dataclass
class _TraversalState:
    """حالة مشتركة عبر كل فروع/مستودعات حدث تصحيح واحد. visited_movement_ids
    وvisited_edges يضمنان عدم التكرار/termination فقط — لا علاقة لهما
    بدلالة Scope نفسها (محكومة حصراً بـID-001/002/003 وFD-001 REV1).
    لا عداد فروع مغلقة/مفتوحة بعد الآن (FD-001 REV1) — كل فرع يُجتاز
    حتى نهاية بياناته بلا استثناء؛ scope_completeness=COMPLETE دائماً
    بهذا التصميم (راجع finalize_scope_metadata)."""
    visited_movement_ids: set = field(default_factory=set)
    visited_edges: set = field(default_factory=set)
    chronology_ambiguous: bool = False


def _link_groups(session, correction_event_id, from_elements, to_elements, state: _TraversalState) -> None:
    """حافة PROPAGATES_TO بين مجموعتي تاريخ متتاليتين — وفق ID-003
    (مغلَق): تُنشأ الحافة فقط حين لا يوجد غموض إسناد حقيقي على الأقل
    بأحد الطرفين:
        1×1  -> حافة واحدة
        1×N  -> fan-out من العنصر الوحيد لكل عناصر الهدف
        N×1  -> fan-in من كل عناصر المصدر للعنصر الوحيد
        N×M  -> لا حافة إطلاقاً (كلا الطرفين أكثر من عنصر: أي حافة محدَّدة
                تفترض إسناداً غير معروف بين مجهولين على الطرفين معاً).
    هذا لا يغيّر عضوية Scope ولا scope_completeness إطلاقاً — العناصر
    تبقى مُسجَّلة (record_scope_element) بصرف النظر عن نتيجة هذه الدالة؛
    القرار هنا يخص فقط: هل تُنشأ الحافة؟ لا: هل العنصر عضو؟"""
    if len(from_elements) > 1 and len(to_elements) > 1:
        return  # N×M -- لا حافة، بلا استثناء، بلا representative مُختلَق
    for src in from_elements:
        for dst in to_elements:
            edge_key = (src.id, dst.id, ImpactScopeRelationshipType.PROPAGATES_TO)
            if edge_key in state.visited_edges:
                continue
            state.visited_edges.add(edge_key)
            record_scope_relationship(session, correction_event_id, src, dst,
                                       ImpactScopeRelationshipType.PROPAGATES_TO)


def walk_warehouse_branch(session: Session, correction_event_id: int, item_id: int,
                           warehouse_id: int, start_date, state: _TraversalState,
                           predecessor_elements: list) -> None:
    """يبني Scope لفرع (item, warehouse) بدءاً من start_date (شاملة).
    يُسجِّل كل عنصر مكتشف، يعبر أي ساق OUT تحويل يواجهها (عبر
    find_transfer_destination)، ويستمر **حتى نفاد البيانات الحقيقية
    المعروفة فقط** — بلا أي إيقاف مبكر قائم على حالة كمية (FD-001 REV1:
    Data-exhaustion termination, not inventory-state termination).
    المجموعات الذرية (ID-002) تُسجَّل معاً، بلا ترتيب داخلي مُفتَرَض."""
    movements = _fetch_subsequent_movements(session, item_id, warehouse_id, start_date)

    groups: dict = {}
    for m in movements:
        groups.setdefault(m.movement_date, []).append(m)

    for group_date in sorted(groups.keys()):
        group = groups[group_date]
        if len(group) > 1:
            state.chronology_ambiguous = True

        group_elements = []
        for m in group:
            if m.id in state.visited_movement_ids:
                # مُسجَّلة مسبقاً (مثلاً: الجذر نفسه ضمن مجموعته الخاصة) —
                # لا إعادة تسجيل.
                continue
            state.visited_movement_ids.add(m.id)
            element = record_scope_element(
                session, correction_event_id, ImpactScopeElementKind.MOVEMENT,
                "inventory_movement", m.id,
            )
            group_elements.append((m, element))

        if group_elements:
            _link_groups(session, correction_event_id, predecessor_elements,
                         [el for _, el in group_elements], state)

        # عبور أي ساق OUT تحويل ضمن هذه المجموعة (FD-002)
        for m, element in group_elements:
            if m.source_type == "stock_transfer" and m.direction == MovementDirection.OUT:
                destination = find_transfer_destination(session, m)
                if destination is not None and destination.id not in state.visited_movement_ids:
                    state.visited_movement_ids.add(destination.id)
                    dest_element = record_scope_element(
                        session, correction_event_id, ImpactScopeElementKind.MOVEMENT,
                        "inventory_movement", destination.id,
                    )
                    _link_groups(session, correction_event_id, [element], [dest_element], state)
                    walk_warehouse_branch(
                        session, correction_event_id, item_id, destination.warehouse_id,
                        destination.movement_date, state, predecessor_elements=[dest_element],
                    )

        if group_elements:
            predecessor_elements = [el for _, el in group_elements]

    # انتهت البيانات المعروفة -- الفرع اكتمل بالكامل (FD-001 REV1: لا
    # إيقاف مبكر، فالوصول لهذه النقطة يعني استنفاد البيانات فعلياً).


# ---------------------------------------------------------------------------
# 5. Metadata الحدث (FD-005 — محوران مستقلان)
# ---------------------------------------------------------------------------

def finalize_scope_metadata(session: Session, correction_event_id: int, state: _TraversalState) -> None:
    """يكتب scope_completeness وchronology_basis بعد انتهاء كل الفروع —
    محوران مستقلان تماماً، لا اشتقاق أحدهما من الآخر. FD-001 REV1: بما
    أن كل فرع يُجتاز حتى نفاد بياناته الحقيقية فعلياً (لا إيقاف مبكر
    كمي)، scope_completeness=COMPLETE دائماً بهذا التصميم — لا حالة
    PARTIAL ناتجة عن الـtraversal الكمي؛ PARTIAL محجوزة لسبب خارجي
    مستقبلي (حد أداء مُعتمَد لاحقاً مثلاً)، غير مُطبَّقة هنا."""
    event = session.get(CorrectionEvent, correction_event_id)
    event.chronology_basis = (
        ChronologyBasis.ASSUMED if state.chronology_ambiguous else ChronologyBasis.KNOWN
    )
    event.scope_completeness = ScopeCompleteness.COMPLETE
    session.flush()


# ---------------------------------------------------------------------------
# 6. نقطة الدخول
# ---------------------------------------------------------------------------

def build_impact_scope(session: Session, correction_event_id: int) -> None:
    """نقطة الدخول الوحيدة — تُستدعى صراحة (لا تلقائياً، لا من أي hook)
    لبناء Impact Scope كاملاً لحدث تصحيح CANDIDATE موجود مسبقاً. راجع
    الملاحظة أعلى الملف بخصوص عدم حسم "متى/من يستدعيها" — قرار سياسة
    مؤجَّل عمداً."""
    root_cause = session.execute(
        select(CorrectionEventRootCause).where(
            CorrectionEventRootCause.correction_event_id == correction_event_id
        )
    ).scalars().first()
    if root_cause is None or root_cause.source_type != "inventory_movement":
        return  # خارج ما تدعمه E حالياً (Cluster 1 فقط) — لا شيء لبنائه

    root_movement = session.get(InventoryMovement, root_cause.source_id)
    if root_movement is None:
        return

    state = _TraversalState()
    root_element = record_scope_element(
        session, correction_event_id, ImpactScopeElementKind.MOVEMENT,
        "inventory_movement", root_movement.id,
    )  # ID-001: الجذر نفسه عنصر Scope، بلا CAUSES اصطناعية، بلا schema جديد
    state.visited_movement_ids.add(root_movement.id)

    walk_warehouse_branch(
        session, correction_event_id, root_movement.item_id, root_movement.warehouse_id,
        root_movement.movement_date, state, predecessor_elements=[root_element],
    )

    finalize_scope_metadata(session, correction_event_id, state)
