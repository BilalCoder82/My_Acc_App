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


def get_item_stock_summary(session: Session, item_id: int) -> ItemStockSummary:
    """الكمية الحالية ومتوسط التكلفة عبر كل المستودعات (مستودع افتراضي واحد
    فعلياً حالياً — راجع get_default_warehouse بـposting.py). نفس خوارزمية
    Average Cost المرجّح المستخدمة وقت الترحيل تماماً، بدون أي تقريب إضافي
    هنا يخالف ما استُخدم فعلياً بالقيود المرحّلة."""
    movements = session.execute(
        select(InventoryMovement)
        .where(InventoryMovement.item_id == item_id)
        .order_by(InventoryMovement.movement_date)
    ).scalars().all()

    total_qty, total_cost = Decimal("0"), Decimal("0")
    for m in movements:
        if m.direction == MovementDirection.IN:
            total_qty += D(m.quantity)
            total_cost += D(m.quantity) * D(m.unit_cost)
        else:
            avg = (total_cost / total_qty) if total_qty else Decimal("0")
            total_qty -= D(m.quantity)
            total_cost -= D(m.quantity) * avg

    if total_qty <= 0:
        return ItemStockSummary(quantity=total_qty, average_cost=Decimal("0"), inventory_value=Decimal("0"))
    avg_cost = total_cost / total_qty
    return ItemStockSummary(quantity=total_qty, average_cost=avg_cost, inventory_value=total_qty * avg_cost)


def list_active_items(session: Session) -> list[Item]:
    """لقوائم اختيار المادة بفواتير جديدة — نفس دور list_postable_accounts
    بسند القيد. المادة غير النشطة لا تظهر هنا، لكنها تبقى ظاهرة بتاريخها
    (الفواتير والحركات القديمة) — التعطيل لا يخفي التاريخ إطلاقاً."""
    return session.query(Item).filter_by(is_active=True).order_by(Item.sku).all()


def list_all_items(session: Session) -> list[Item]:
    """لشاشة دليل المواد نفسها — تعرض النشط وغير النشط معاً (غير النشط
    يُعرَض رمادياً بالواجهة، نفس أسلوب دليل الحسابات)."""
    return session.query(Item).order_by(Item.sku).all()
