"""
Item Queries — دليل المواد: استعلامات القراءة فقط
=====================================================
نفس مبدأ account_queries.py. لا قواعد تحقق هنا (تلك بـitem_edit.py) — فقط
قراءة.

**قاعدة مثبَّتة بالنقاش قبل التنفيذ (WORKFLOW.md §25)**: Item لا يخزن كمية أو
متوسط تكلفة أو قيمة مخزون كحقول مستقلة إطلاقاً — لاحظ أن `Item` بالأسفل
(models.py) لا يملك أصلاً أي عمود من هذا النوع. `get_item_stock_summary`
هي **المصدر الوحيد** لحساب هذه القيم، مشتقة حصراً من `InventoryMovement`
المرحّلة. **`app/services/posting.py` يستورد من هنا أيضاً بدل حساب متوسط
تكلفة مستقل بمكانين مختلفين** — نفس مبدأ "لا نكرر منطق المحاسبة بين
الواجهات والتقارير" المطبَّق مسبقاً على كشف الحساب (`ledger.py`).
"""

from __future__ import annotations
from decimal import Decimal
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Item, InventoryMovement, MovementDirection
from app.services.money import D


@dataclass
class ItemStockSummary:
    quantity: Decimal
    average_cost: Decimal
    inventory_value: Decimal


@dataclass(frozen=True)
class MovementFact:
    """PHASE 3B-5-B: بنية بيانات صغيرة read-only تحمل فقط الحقول الثلاثة
    التي تحتاجها accumulate_average_cost() فعلياً — لا استيراد لـSQLAlchemy
    أو app.models هنا، بقرار معتمد أن تكون pure-calculation APIs مستقلة
    عن الـORM حيثما كانت كلفة الفصل مبررة (Calculation 1 تحديداً؛ القرار
    لا يُطبَّق آلياً على كل حساب — انظر compute_invoice_totals() التي
    أُبقيت على مدخلها الحالي (Invoice) عمداً بلا تغيير)."""
    direction: MovementDirection
    quantity: Decimal
    unit_cost: Decimal


def accumulate_average_cost(movements: "list[MovementFact]") -> ItemStockSummary:
    """PHASE 3B-5-B — استخراج الحساب النقي من get_item_stock_summary() حرفياً
    بلا أي تغيير سلوكي. هذه الدالة لا تعرف شيئاً عن Session أو SQLAlchemy أو
    ترتيب الحركات — الترتيب يبقى مسؤولية المستدعي (get_item_stock_summary
    أدناه)، تماماً كما كان قبل هذا الاستخراج. Finding B (لا secondary sort
    key لحركات بنفس movement_date) لم يُحل هنا ولن يتأثر بهذا الاستخراج.

    قاعدة محاسبية أساسية غير معدَّلة (WORKFLOW.md §39): كل حركة *خروج*
    تُقيَّم بتكلفتها الخاصة المخزَّنة على الحركة نفسها (m.unit_cost) — لا
    بإعادة حساب متوسط جديد."""
    total_qty, total_cost = Decimal("0"), Decimal("0")
    for m in movements:
        if m.direction == MovementDirection.IN:
            total_qty += m.quantity
            total_cost += m.quantity * m.unit_cost
        else:
            total_qty -= m.quantity
            total_cost -= m.quantity * m.unit_cost

    if total_qty <= 0:
        return ItemStockSummary(quantity=total_qty, average_cost=Decimal("0"), inventory_value=Decimal("0"))
    avg_cost = total_cost / total_qty
    return ItemStockSummary(quantity=total_qty, average_cost=avg_cost, inventory_value=total_qty * avg_cost)


def get_item_stock_summary(session: Session, item_id: int, warehouse_id: int | None = None) -> ItemStockSummary:
    """الكمية الحالية ومتوسط التكلفة.

    **قرار معتمد (WORKFLOW.md §46)**: التكلفة منفصلة لكل مستودع، لا موحّدة
    على مستوى الشركة. `warehouse_id=None` يُرجع إجمالي كل المستودعات
    (تجميعي شرعي لأغراض العرض/التقرير فقط — مثال: "إجمالي ما تملكه
    الشركة") — **ممنوع استخدام `None` لأي قرار تسعير أو ترحيل فعلي**؛
    كل استدعاء محاسبي (COGS، مرتجع) يجب أن يمرّر `warehouse_id` صراحة
    (`_average_cost()` لا تقبل قيمة افتراضية أصلاً لهذا السبب بالضبط).

    قاعدة محاسبية أساسية (WORKFLOW.md §39): كل حركة *خروج* تُقيَّم بتكلفتها
    الخاصة المخزَّنة فعلياً على الحركة نفسها (movement.unit_cost) — لا
    بإعادة حساب متوسط جديد مستقل عند إعادة البناء. الحركة المرحّلة تمثّل
    حقيقة تاريخية ثابتة، ولا يجوز "تسعيرها بأثر رجعي". هذا يضمن أن هذه
    الدالة تنتج نفس القيمة تماماً التي استُخدمت فعلياً وقت الترحيل
    (بما فيها حالة مرتجع شراء مرتبط بفاتورة أصلية، حيث unit_cost تاريخي
    يختلف عمداً عن المتوسط الحالي وقت الإرجاع).

    PHASE 3B-5-B: الحساب نفسه استُخرج إلى accumulate_average_cost() (نقي،
    بلا Session). هذه الدالة تبقى مسؤولة حصراً عن: بناء الاستعلام، الترتيب
    الحالي (movement_date فقط — Finding B لم يُحل)، وتحويل الصفوف إلى
    MovementFact قبل تمريرها."""
    query = select(InventoryMovement).where(InventoryMovement.item_id == item_id)
    if warehouse_id is not None:
        query = query.where(InventoryMovement.warehouse_id == warehouse_id)
    movements = session.execute(query.order_by(InventoryMovement.movement_date)).scalars().all()

    facts = [
        MovementFact(direction=m.direction, quantity=D(m.quantity), unit_cost=D(m.unit_cost))
        for m in movements
    ]
    return accumulate_average_cost(facts)


def list_active_items(session: Session) -> list[Item]:
    """لقوائم اختيار المادة بفواتير جديدة — نفس دور list_postable_accounts
    بسند القيد. المادة غير النشطة لا تظهر هنا، لكنها تبقى ظاهرة بتاريخها
    (الفواتير والحركات القديمة) — التعطيل لا يخفي التاريخ إطلاقاً."""
    return session.query(Item).filter_by(is_active=True).order_by(Item.sku).all()


def list_all_items(session: Session) -> list[Item]:
    """لشاشة دليل المواد نفسها — تعرض النشط وغير النشط معاً (غير النشط
    يُعرَض رمادياً بالواجهة، نفس أسلوب دليل الحسابات)."""
    return session.query(Item).order_by(Item.sku).all()
