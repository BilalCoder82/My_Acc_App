"""
Stock Transfer — تحويل بين مستودعات
======================================
حركة داخلية بحتة: خروج من مستودع + دخول لمستودع آخر، بدون أي قيد
محاسبي (لا تغيّر قيمة المخزون الإجمالية للشركة ككل، لكنها **تغيّر متوسط
تكلفة مستودع الوجهة** — راجع WORKFLOW.md §46.4).

قاعدة التكلفة (بعد قرار §46: التكلفة منفصلة لكل مستودع): الوحدات
المنقولة تخرج من A بمتوسط تكلفة A **الحالي تحديداً وقت النقل**، وتدخل B
بنفس هذه القيمة، فتندمج طبيعياً ضمن متوسط B المرجّح (كأي دخول عادي — لا
معاملة خاصة إضافية). **لا تُستخدَم أبداً** تكلفة "آخر شراء للمادة
بالنظام" بمعزل عن المستودع (كان هذا خللاً حقيقياً مُكتشَفاً، راجع §46.5).
"""

from __future__ import annotations
from datetime import date
from sqlalchemy.orm import Session

from app.models import StockTransfer, InventoryMovement, MovementDirection, Item, Warehouse
from app.services.money import qty
from app.services.item_queries import get_item_stock_summary


def _next_transfer_no(session: Session) -> str:
    count = session.query(StockTransfer).count()
    return f"TRF-{count + 1:05d}"


def transfer_stock(
    session: Session, item_id: int, from_warehouse_id: int, to_warehouse_id: int,
    quantity, transfer_date: date | None = None, note: str | None = None,
) -> StockTransfer:
    if from_warehouse_id == to_warehouse_id:
        raise ValueError("لا يمكن التحويل من وإلى نفس المستودع")

    item = session.get(Item, item_id)
    if item is None:
        raise ValueError(f"مادة غير موجودة: id={item_id}")
    for wh_id in (from_warehouse_id, to_warehouse_id):
        if session.get(Warehouse, wh_id) is None:
            raise ValueError(f"مستودع غير موجود: id={wh_id}")

    q = qty(quantity)
    if q <= 0:
        raise ValueError("كمية التحويل يجب أن تكون أكبر من صفر")

    transfer = StockTransfer(
        transfer_no=_next_transfer_no(session),
        transfer_date=transfer_date or date.today(),
        item_id=item_id, from_warehouse_id=from_warehouse_id,
        to_warehouse_id=to_warehouse_id, quantity=q, note=note,
    )
    session.add(transfer)
    session.flush()

    # تكلفة مستودع المصدر الحالية تحديداً (WORKFLOW.md §46.4/§46.5) — لا
    # "آخر شراء للمادة بالنظام" بمعزل عن المستودع (كان هذا الخلل المُكتشَف).
    # لو كان مستودع المصدر فارغاً من هذه المادة أصلاً (متوسط=0)، هذا يعكس
    # الواقع بصدق بدل اختلاق سعر من مكان آخر لا علاقة له بهذا المستودع.
    unit_cost = get_item_stock_summary(session, item_id, warehouse_id=from_warehouse_id).average_cost

    session.add_all([
        InventoryMovement(
            item_id=item_id, warehouse_id=from_warehouse_id, direction=MovementDirection.OUT,
            quantity=q, unit_cost=unit_cost, movement_date=transfer.transfer_date,
            source_type="stock_transfer", source_id=transfer.id,
        ),
        InventoryMovement(
            item_id=item_id, warehouse_id=to_warehouse_id, direction=MovementDirection.IN,
            quantity=q, unit_cost=unit_cost, movement_date=transfer.transfer_date,
            source_type="stock_transfer", source_id=transfer.id,
        ),
    ])
    session.flush()
    return transfer


def get_stock_balance(session: Session, item_id: int, warehouse_id: int | None = None):
    """رصيد مادة — إجمالي كل المستودعات إذا warehouse_id=None، أو رصيد مستودع محدد."""
    from decimal import Decimal
    q = session.query(InventoryMovement).filter_by(item_id=item_id)
    if warehouse_id is not None:
        q = q.filter_by(warehouse_id=warehouse_id)
    balance = Decimal("0")
    for m in q.all():
        balance += qty(m.quantity) if m.direction == MovementDirection.IN else -qty(m.quantity)
    return balance
