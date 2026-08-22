"""
Opening Balances — أرصدة افتتاحية (حسابات ومواد)
====================================================
إجراء قياسي موحّد يُستخدم مرة واحدة لكل عميل جديد له تاريخ محاسبي سابق،
قبل تسجيل أي فاتورة. بدون هذا، أي كشف حساب أو تقرير مخزون لاحق يكون
ناقصاً أو مضللاً.

ملاحظة: رصيد الحسابات الافتتاحي (opening balance للـChart of Accounts)
يُسجَّل بقيد يدوي عادي (JournalEntry بـsource_type='opening_balance')،
لا يحتاج دالة خاصة — هذا التوثيق كافٍ. أما رصيد المواد فيحتاج دالة لأنه
يمس InventoryMovement + المتوسط المرجّح مباشرة.
"""

from __future__ import annotations
from datetime import date
from sqlalchemy.orm import Session

from app.models import InventoryMovement, MovementDirection, Item
from app.services.posting import get_default_warehouse


def set_item_opening_balance(
    session: Session, item_id: int, quantity: float, unit_cost: float,
    as_of_date: date | None = None,
) -> InventoryMovement:
    """
    يسجّل رصيد افتتاحي لمادة معيّنة. يجب استدعاؤها قبل أي حركة بيع/شراء
    لهذه المادة، وبتاريخ أقدم من أي فاتورة — وإلا ينكسر ترتيب حساب
    المتوسط المرجّح (average cost يعتمد على الترتيب الزمني للحركات).
    """
    item = session.get(Item, item_id)
    if item is None:
        raise ValueError(f"مادة غير موجودة: id={item_id}")

    existing_opening = session.query(InventoryMovement).filter_by(
        item_id=item_id, source_type="opening_balance"
    ).first()
    if existing_opening is not None:
        raise ValueError(
            f"رصيد افتتاحي مسجَّل مسبقاً للمادة '{item.name_ar}' — "
            "لا يُسمح بتكراره، عدّل السطر الموجود مباشرة إن لزم."
        )

    movement = InventoryMovement(
        item_id=item_id,
        warehouse_id=get_default_warehouse(session).id,
        direction=MovementDirection.IN,
        quantity=quantity,
        unit_cost=unit_cost,
        movement_date=as_of_date or date.today(),
        source_type="opening_balance",
        note="رصيد افتتاحي",
    )
    session.add(movement)
    session.flush()
    return movement
