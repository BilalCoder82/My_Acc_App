"""
PHASE 3B-5-E — Detection Implementation (Cluster 1 only)
============================================================
هذه الشريحة تبني السهم الأول فقط من دورة حياة CorrectionEvent:

    historical movement detected
            ↓
    CorrectionEvent(CANDIDATE) + CorrectionEventRootCause واحد

لا شيء آخر. لا Impact Scope (Cluster 2)، لا WalkAccumulator/Recalculation
(Cluster 3)، لا TieGroup/CandidateState (Cluster 4)، لا scope_completeness/
chronology_basis (Clusters 5-6)، لا WITHDRAWN (يحتاج امتداد منفصل لنفس آلية
الاكتشاف — راجع 3B-5-D_FINAL_REVIEW_AND_3B-5-E_SCOPE.md). هذه الحقول تبقى
NULL على كل صف تنشئه هذه الشريحة.

آلية الاكتشاف (Cluster 1):
---------------------------
كل حركة InventoryMovement جديدة تُفحَص، بعد أن تُكتَب فعلياً (بعد الـflush،
لا قبله — نحتاج id الحقيقي المولَّد)، مقابل الحركات *الموجودة سلفاً* في
القاعدة لنفس (item_id, warehouse_id): لو وُجدت حركة قائمة بتاريخ لاحق
(movement_date أكبر)، فهذا يعني أن تلك الحركة اللاحقة حسبت متوسطها التاريخي
دون أن تعرف بوجود هذه الحركة الجديدة الأقدم — سلسلة Opening→Movement→
Average Cost→COGS→Journal لديها بالتالي قد اعتمدت على ترتيب زمني أصبح
الآن غير صحيح. هذا هو الـCandidate.

اثنان من القرارات الحاسمة لهذه الشريحة بالتحديد (لا تُعاد لاحقاً):

1) نمط الكتابة: Raw Connection داخل after_flush، لا after_commit مؤجَّل.
   السبب: after_commit لا يملك اتصالاً بنفس معاملة الحركة التي وُلِدت منها
   (الاتصال الأصلي أُغلِق فعلياً عند نجاح commit) — أي كتابة CorrectionEvent
   هناك تصبح معاملة منفصلة كلياً، وانهيار العملية بين commit الحركة وaحتى
   قبل تنفيذ الكتابة المؤجَّلة يفقد الـCandidate بصمت، بلا أي أثر. الكتابة
   عبر connection الخاص بنفس الـflush (session.connection()) تبقى ضمن نفس
   معاملة قاعدة البيانات التي تكتب الحركة نفسها: إما أن يُكتَب الاثنان معاً
   عند commit، أو لا يُكتَب أي منهما عند rollback — ذرّية حقيقية، لا نافذة
   فقدان. البديل (إضافة كائنات ORM جديدة عبر session.add() داخل after_flush
   نفسها) غير مسموح به من SQLAlchemy أصلاً: خطة الـflush الحالية مُقفَلة
   عند هذه النقطة، وإضافة كائنات جديدة الآن تحتاج دورة flush ثانية متداخلة
   (nested-flush hazard) — بالضبط ما يحذّر منه Cluster 1. استخدام Core
   INSERT مباشرة على الـconnection يتفادى هذا كلياً لأنه لا يمر بخطة الـORM
   إطلاقاً.

2) نطاق before_flush/after_flush: مُلتقَط بالكامل session.info، يُصفَّر بعد
   كل after_flush — يطابق "نفس الـflush" حرفياً (لا يمتد لعدة عمليات flush
   ضمن نفس commit)، وهذا بالضبط ما يحتاج إليه اختبار Same-flush.

المستمِعان (before_flush و after_flush) يُثبَّتان مرة واحدة فقط، على
مستوى إعداد الجلسة (event.listen(Session, ...) — يستهدف الصنف الأساسي
Session، لا أي sessionmaker معيّن)، لا عند كل call site — يعملان تلقائياً
على أي جلسة بالتطبيق (app/db.py وأي fresh_session() باختبارات).

حد موثَّق لهذه الشريحة (PHASE 3B-5-F يجب أن يرث هذه الحقيقة كمُدخَل، لا
كذاكرة محادثة):
---------------------------------------------------------------------
E detects historical chronology changes introduced by INSERT of a new
InventoryMovement; it does not detect UPDATE-based changes to
movement_date on an existing InventoryMovement. The current production
code has no such UPDATE path.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import event, select, insert
from sqlalchemy.orm import Session

from app.models import (
    InventoryMovement, CorrectionEvent, CorrectionEventRootCause,
    CorrectionEventStatus, RootCauseComponentType,
)

_PENDING_KEY = "_phase3b5e_pending_new_movements"

_listeners_attached = False


def _before_flush(session: Session, flush_context, instances) -> None:
    """يلتقط كائنات InventoryMovement الجديدة *قبل* أن يُولَّد لها id —
    نحتفظ بالكائنات نفسها (لا الـids، لأنها غير موجودة بعد)، لنقرأ
    .id منها لاحقاً في after_flush بعد أن يملأه الـflush فعلياً."""
    pending = session.info.setdefault(_PENDING_KEY, [])
    for obj in session.new:
        if isinstance(obj, InventoryMovement) and obj not in pending:
            pending.append(obj)


def _after_flush(session: Session, flush_context) -> None:
    """لكل حركة جديدة انكتبت للتو بهذا الـflush: هل توجد حركة قائمة سلفاً
    بنفس (item_id, warehouse_id) وتاريخ لاحق؟ إن وُجدت، ولم يسبق تسجيل
    Candidate لنفس هذه الحركة الجذر تحديداً، اكتب CorrectionEvent(CANDIDATE)
    + CorrectionEventRootCause واحد — عبر connection نفس الـflush مباشرة."""
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return

    # استبعاد نفس الـflush: الأشقاء (حركات مستند واحد متعدد الأسطر) لا
    # يجوز أن يُبلِّغ أحدها عن الآخر أبداً.
    same_flush_ids = {m.id for m in pending if m.id is not None}
    if not same_flush_ids:
        return

    connection = session.connection()
    movements_table = InventoryMovement.__table__
    events_table = CorrectionEvent.__table__
    root_causes_table = CorrectionEventRootCause.__table__

    for movement in pending:
        if movement.id is None:
            continue  # حماية دفاعية — لم يُكتَب فعلياً لسبب ما

        # عدم تكرار Candidate لنفس root cause (نفس الحركة المُحفِّزة)
        already_flagged = connection.execute(
            select(root_causes_table.c.id).where(
                root_causes_table.c.source_type == "inventory_movement",
                root_causes_table.c.source_id == movement.id,
            ).limit(1)
        ).first()
        if already_flagged is not None:
            continue

        blocking_query = select(movements_table.c.id).where(
            movements_table.c.item_id == movement.item_id,
            movements_table.c.warehouse_id == movement.warehouse_id,
            movements_table.c.movement_date > movement.movement_date,
            movements_table.c.id.notin_(same_flush_ids),
        ).limit(1)
        blocking = connection.execute(blocking_query).first()
        if blocking is None:
            continue

        component_type = (
            RootCauseComponentType.REVERSAL
            if movement.source_type == "invoice_cancel"
            else RootCauseComponentType.NEW_MOVEMENT
        )

        result = connection.execute(
            insert(events_table).values(
                item_id=movement.item_id,
                trigger_type="detection",
                status=CorrectionEventStatus.CANDIDATE,
                created_at=datetime.utcnow(),
            )
        )
        correction_event_id = result.inserted_primary_key[0]

        connection.execute(
            insert(root_causes_table).values(
                correction_event_id=correction_event_id,
                component_type=component_type,
                source_type="inventory_movement",
                source_id=movement.id,
            )
        )


def attach_detection_listeners() -> None:
    """تُستدعى مرة واحدة فقط (من app/db.py عند تحميل الوحدة) — تُثبِّت
    المستمِعين على مستوى صنف Session الأساسي، لا لكل sessionmaker/جلسة."""
    global _listeners_attached
    if _listeners_attached:
        return
    event.listen(Session, "before_flush", _before_flush)
    event.listen(Session, "after_flush", _after_flush)
    _listeners_attached = True
