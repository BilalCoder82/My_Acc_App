"""
Item Edit Service — دليل المواد: إنشاء/تعديل/تفعيل
========================================================
نفس مبدأ account_edit.py بالضبط: كل القواعد هنا، الواجهة لا تتحقق من شيء.

**القواعد المطبَّقة** (متفَق عليها بمراجعة التصميم قبل التنفيذ):
- SKU لا يتكرر، الاسم مطلوب، حد إعادة الطلب ≥ 0
- طريقة التكلفة: Average فقط مسموحة حالياً — FIFO موجودة بالـenum لكن غير
  مُنفَّذة بأي منطق ترحيل فعلياً، فرفضها هنا صريح بدل قبولها بصمت ثم نتائج
  خاطئة لاحقاً
- حساب المخزون وحساب تكلفة المبيعات **إلزاميان** — يُستخدَمان مباشرة بمحرّك
  الترحيل (posting.py/returns.py)، وفاتورة عليها مادة بلا هذين الحسابين
  ستفشل عند الترحيل لا عند الإدخال، وهذا أسوأ توقيت لاكتشاف الخطأ
- حساب المبيعات **اختياري** — **الآن فعّال فعلياً بمحرّك الترحيل**
  (`posting.py`/`returns.py`، بعد إغلاق فجوة كانت موثَّقة سابقاً هنا، راجع
  WORKFLOW.md §27): لو حُدِّد لمادة، تُستخدَم قيمته مباشرة بقيود بيعها بدل
  الإعداد العام؛ لو تُرِك فارغاً، يُستخدَم `Setting['default_sales_account_id']`
  كاحتياطي لتلك المادة فقط. أي فاتورة بعدة مواد قد تُنتج أكثر من سطر مبيعات
  بقيد واحد لو اختلفت حساباتها.
- الحسابات الثلاثة (لو مُحدَّدة) يجب أن تكون: موجودة، حساب حركة
  (`is_group=False`)، **نشطة** (`is_active=True`)، ومن النوع المحاسبي
  المناسب (مخزون→أصول، مبيعات→إيرادات، تكلفة مبيعات→مصروفات)
- مادة لها حركات مخزون مسجَّلة: **لا يجوز تغيير `cost_method` أو أي من
  الحسابات الثلاثة** — يُفسِد اتساق تكلفة المبيعات التاريخية (نفس مبدأ حماية
  حساب له حركات بـaccount_edit.py). **هذا قيد v1 مؤقت وليس تصميماً نهائياً**
  — لاحقاً يمكن السماح بتغيير الحسابات "اعتباراً من تاريخ" مع بقاء الحركات
  القديمة مرتبطة بالحسابات التاريخية؛ لا شيء بهذا الملف أو بالـschema يمنع
  إضافة تلك الآلية لاحقاً، فقط لم تُبنَ الآن
- إعادة التسمية والتصنيف والوحدة وحد إعادة الطلب تبقى قابلة للتعديل دوماً
  حتى لو للمادة حركات — القيد فقط على الحقول الحسّاسة أعلاه
- لا حذف بهذا الإصدار. تعطيل المادة لا يخفيها من الفواتير/الحركات/التقارير
  التاريخية — فقط من قوائم اختيار المادة بإدخال جديد (list_active_items)
"""

from __future__ import annotations
from decimal import Decimal
from sqlalchemy.orm import Session

from app.models import Item, Account, AccountType, CostMethod

ACCOUNT_TYPE_FOR_FIELD = {
    "inventory_account_id": AccountType.ASSET,
    "sales_account_id": AccountType.REVENUE,
    "cogs_account_id": AccountType.EXPENSE,
}
FIELD_LABEL_AR = {
    "inventory_account_id": "حساب المخزون",
    "sales_account_id": "حساب المبيعات",
    "cogs_account_id": "حساب تكلفة المبيعات",
}


class ItemEditError(Exception):
    pass


def _validate_linked_account(session: Session, field: str, account_id: int | None, required: bool) -> None:
    label = FIELD_LABEL_AR[field]
    if account_id is None:
        if required:
            raise ItemEditError(f"{label} مطلوب — يُستخدَم مباشرة عند ترحيل فواتير هذه المادة")
        return
    account = session.get(Account, account_id)
    if account is None:
        raise ItemEditError(f"{label} المحدَّد غير موجود")
    if account.is_group:
        raise ItemEditError(f"{label} يجب أن يكون حساب حركة (غير تجميعي)")
    if not account.is_active:
        raise ItemEditError(f"{label} غير نشط — اختر حساباً نشطاً")
    expected_type = ACCOUNT_TYPE_FOR_FIELD[field]
    if account.account_type != expected_type:
        raise ItemEditError(f"{label} يجب أن يكون من نوع {expected_type.value}")


def _validate_common(
    session: Session, *, sku: str, name_ar: str, unit: str, cost_method: CostMethod,
    reorder_point: Decimal, inventory_account_id: int | None, sales_account_id: int | None,
    cogs_account_id: int | None, existing: Item | None,
) -> tuple[str, str, str]:
    sku = (sku or "").strip()
    name_ar = (name_ar or "").strip()
    unit = (unit or "قطعة").strip()

    if not sku:
        raise ItemEditError("كود المادة مطلوب")
    if not name_ar:
        raise ItemEditError("اسم المادة مطلوب")
    if reorder_point < 0:
        raise ItemEditError("حد إعادة الطلب لا يجوز أن يكون سالباً")
    if cost_method != CostMethod.AVERAGE:
        raise ItemEditError("طريقة التكلفة FIFO غير مُنفَّذة بعد بمحرّك الترحيل — Average فقط متاحة حالياً")

    dup_query = session.query(Item).filter(Item.sku == sku)
    if existing is not None:
        dup_query = dup_query.filter(Item.id != existing.id)
    if dup_query.first() is not None:
        raise ItemEditError(f"كود المادة '{sku}' مستخدم مسبقاً بمادة أخرى")

    _validate_linked_account(session, "inventory_account_id", inventory_account_id, required=True)
    _validate_linked_account(session, "sales_account_id", sales_account_id, required=False)
    _validate_linked_account(session, "cogs_account_id", cogs_account_id, required=True)

    return sku, name_ar, unit


def create_item(
    session: Session, *, sku: str, name_ar: str, unit: str = "قطعة", category: str | None = None,
    cost_method: CostMethod = CostMethod.AVERAGE, reorder_point: Decimal = Decimal("0"),
    inventory_account_id: int | None = None, sales_account_id: int | None = None,
    cogs_account_id: int | None = None, is_active: bool = True,
) -> Item:
    sku, name_ar, unit = _validate_common(
        session, sku=sku, name_ar=name_ar, unit=unit, cost_method=cost_method,
        reorder_point=reorder_point, inventory_account_id=inventory_account_id,
        sales_account_id=sales_account_id, cogs_account_id=cogs_account_id, existing=None,
    )
    item = Item(
        sku=sku, name_ar=name_ar, unit=unit, category=(category or "").strip() or None,
        cost_method=cost_method, reorder_point=reorder_point, is_active=is_active,
        inventory_account_id=inventory_account_id, sales_account_id=sales_account_id,
        cogs_account_id=cogs_account_id,
    )
    session.add(item)
    session.flush()
    return item


def update_item(
    session: Session, item: Item, *, sku: str, name_ar: str, unit: str, category: str | None,
    cost_method: CostMethod, reorder_point: Decimal, inventory_account_id: int | None,
    sales_account_id: int | None, cogs_account_id: int | None, is_active: bool,
) -> Item:
    sku, name_ar, unit = _validate_common(
        session, sku=sku, name_ar=name_ar, unit=unit, cost_method=cost_method,
        reorder_point=reorder_point, inventory_account_id=inventory_account_id,
        sales_account_id=sales_account_id, cogs_account_id=cogs_account_id, existing=item,
    )

    has_movements = len(item.movements) > 0
    if has_movements:
        if cost_method != item.cost_method:
            raise ItemEditError(
                "لا يمكن تغيير طريقة التكلفة — للمادة حركات مخزون مسجَّلة مسبقاً بالطريقة الحالية"
            )
        sensitive_changed = (
            inventory_account_id != item.inventory_account_id
            or sales_account_id != item.sales_account_id
            or cogs_account_id != item.cogs_account_id
        )
        if sensitive_changed:
            raise ItemEditError(
                "لا يمكن تغيير الحسابات المرتبطة بالمادة — لها حركات مخزون مسجَّلة، "
                "وتغييرها يُفسِد اتساق تكلفة المبيعات التاريخية (v1 — راجع تعليق الملف لخطة مستقبلية)"
            )

    item.sku = sku
    item.name_ar = name_ar
    item.unit = unit
    item.category = (category or "").strip() or None
    item.cost_method = cost_method
    item.reorder_point = reorder_point
    item.inventory_account_id = inventory_account_id
    item.sales_account_id = sales_account_id
    item.cogs_account_id = cogs_account_id
    item.is_active = is_active
    session.flush()
    return item


def set_item_active(session: Session, item: Item, is_active: bool) -> Item:
    """تفعيل/تعطيل — لا يمسّ أي حركة أو فاتورة تاريخية، فقط يُخفي المادة من
    list_active_items (قوائم اختيار المادة بإدخال جديد)."""
    item.is_active = is_active
    session.flush()
    return item
