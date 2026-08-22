"""
Stock Transfer — تحويل بين مستودعات
======================================
حركة داخلية بحتة: خروج من مستودع + دخول لمستودع آخر بنفس الكلفة تماماً،
بدون أي قيد محاسبي (لا تغيّر قيمة المخزون الإجمالية ولا الأرباح).
كلفة الوحدة تبقى كما هي (لا إعادة تقييم عند النقل).
"""

from __future__ import annotations
from datetime import date
from sqlalchemy.orm import Session

from app.models import StockTransfer, InventoryMovement, MovementDirection, Item, Warehouse
from app.services.money import qty


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

    # نستخدم نفس تكلفة آخر حركة دخول معروفة للمادة — لا إعادة تقييم عند النقل
    last_in = session.query(InventoryMovement).filter_by(
        item_id=item_id
    ).filter(InventoryMovement.direction == MovementDirection.IN).order_by(
        InventoryMovement.movement_date.desc()
    ).first()
    unit_cost = last_in.unit_cost if last_in else 0

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
