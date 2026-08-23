"""
Returns Service — مرتجعات البيع والشراء
============================================
يدعم مسارين حسب طلب المستخدم صراحة:
    1. مرتجع مربوط بفاتورة أصلية: يُدخل رقم الفاتورة، فيُسحب اسم الطرف
       وبنودها تلقائياً (بكمياتها الأصلية القابلة للتعديل نزولاً فقط).
       كلفة الوحدة تُقرأ من حركة المخزون الأصلية نفسها (دقة تامة).
    2. مرتجع حر (بدون ربط): الطرف والبنود تُدخل يدوياً بالكامل.
       كلفة الوحدة = المتوسط المرجّح الحالي وقت المرتجع (تقريب معقول،
       موثّق كفرق مقصود عن الحالة الأولى — لا يوجد "الفاتورة الأصلية"
       لنقرأ منها كلفة دقيقة).

هذا تصميم جديد يستبدل post_return() القديم (عكس القيد الأصلي بالكامل) —
القديم كان يفرض إرجاع الفاتورة كاملة فقط، بينما هذا يدعم إرجاع جزئي
لبنود محددة بكميات محددة، وهو ما طلبه المستخدم صراحة.
"""

from __future__ import annotations
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import (
    Invoice, InvoiceKind, InvoiceStatus, InventoryMovement, MovementDirection,
    JournalEntry, JournalEntryStatus, Item, Setting,
)
from app.services.parties import get_or_create_party_account
from app.services.invoice_calc import compute_invoice_totals
from app.services.invoice_validation import validate_invoice_for_posting, InvoiceValidationError
from app.services.money import D, money
from app.services.posting import (
    _get_setting, _next_ref_no, _jline, _average_cost, _invoice_warehouse_id, PostingError,
)


def get_already_returned_quantity(session: Session, original_invoice_id: int, item_id: int) -> Decimal:
    """مجموع الكمية المُرجعة سابقاً (بمرتجعات مرحّلة فقط) لهذا الصنف من هذه
    الفاتورة الأصلية تحديداً — يشمل كل المرتجعات، مو آخر واحد فقط."""
    returns = session.query(Invoice).filter_by(
        original_invoice_id=original_invoice_id, status=InvoiceStatus.POSTED,
    ).all()
    total = Decimal("0")
    for r in returns:
        for line in r.lines:
            if line.item_id == item_id:
                total += D(line.quantity)
    return total


def validate_return_against_original(session: Session, return_invoice: Invoice) -> None:
    """يمنع إرجاع كمية أكبر من (الكمية الأصلية - كل ما أُرجع سابقاً) —
    ضابط محاسبي مفروض هنا بالخدمة، لا يعتمد على الواجهة إطلاقاً."""
    if not return_invoice.original_invoice_id:
        return  # مرتجع حر بدون ربط — لا قيد على الكمية

    original = session.get(Invoice, return_invoice.original_invoice_id)
    if original is None:
        raise InvoiceValidationError("المستند الأصلي المربوط به المرتجع غير موجود")

    errors: list[str] = []
    for line in return_invoice.lines:
        original_line = next((l for l in original.lines if l.item_id == line.item_id), None)
        if original_line is None:
            errors.append(f"الصنف بمعرّف {line.item_id} غير موجود أصلاً بالمستند {original.invoice_no}")
            continue
        already_returned = get_already_returned_quantity(session, original.id, line.item_id)
        remaining = D(original_line.quantity) - already_returned
        if D(line.quantity) > remaining:
            item = session.get(Item, line.item_id)
            errors.append(
                f"{item.name_ar}: الكمية المتاحة للإرجاع {remaining} فقط "
                f"(الأصل {original_line.quantity}، مُرجَع سابقاً {already_returned})، "
                f"وطُلب إرجاع {line.quantity}"
            )
    if errors:
        raise InvoiceValidationError(" — ".join(errors))


def get_returnable_lines(session: Session, original_invoice: Invoice) -> list[dict]:
    """يرجّع بنود الفاتورة الأصلية جاهزة كنقطة بداية لملء شبكة المرتجع.
    لا تتحقق حالياً من كميات أُرجعت سابقاً بمرتجعات أخرى لنفس الفاتورة —
    فجوة معروفة موثّقة بـWORKFLOW.md، القيمة المُرجعة هي الكمية الأصلية
    كاملة، والمحاسب يعدّلها يدوياً حسب الكمية الفعلية المُرجعة."""
    result = []
    for line in original_invoice.lines:
        item = session.get(Item, line.item_id)
        # كلفة الوحدة الدقيقة من حركة المخزون الأصلية لنفس المادة والمصدر
        movement = session.execute(
            select(InventoryMovement).where(
                InventoryMovement.source_type.in_(["sales_invoice", "purchase_invoice"]),
                InventoryMovement.source_id == original_invoice.id,
                InventoryMovement.item_id == line.item_id,
            )
        ).scalars().first()
        unit_cost = movement.unit_cost if movement else None
        result.append({
            "item_id": item.id, "sku": item.sku, "name_ar": item.name_ar,
            "quantity": line.quantity, "unit_price": line.unit_price,
            "discount_percent": line.discount_percent, "tax_rate": line.tax_rate,
            "unit_cost": unit_cost,
        })
    return result


def _return_unit_cost(session: Session, item_id: int, original_invoice: Invoice | None) -> Decimal:
    if original_invoice is not None:
        movement = session.execute(
            select(InventoryMovement).where(
                InventoryMovement.source_type.in_(["sales_invoice", "purchase_invoice"]),
                InventoryMovement.source_id == original_invoice.id,
                InventoryMovement.item_id == item_id,
            )
        ).scalars().first()
        if movement is not None:
            return D(movement.unit_cost)
    # مرتجع حر بدون ربط، أو لم نجد حركة أصلية مطابقة: المتوسط المرجّح الحالي
    return _average_cost(session, item_id)


def post_sales_return(session: Session, return_invoice: Invoice, is_cash: bool = True) -> JournalEntry:
    if return_invoice.status == InvoiceStatus.POSTED:
        raise PostingError("المرتجع مرحّل أصلاً — لا يمكن ترحيله مرتين")
    try:
        validate_invoice_for_posting(return_invoice)
        validate_return_against_original(session, return_invoice)
    except InvoiceValidationError as e:
        raise PostingError(str(e))

    original_invoice = (
        session.get(Invoice, return_invoice.original_invoice_id)
        if return_invoice.original_invoice_id else None
    )

    if is_cash:
        cash_or_ar = _get_setting(session, "default_cash_account_id")
    else:
        party_account = get_or_create_party_account(session, return_invoice.party_name, is_customer=True)
        cash_or_ar = party_account.id
    sales_acc = _get_setting(session, "default_sales_account_id")
    tax_acc = _get_setting(session, "default_sales_tax_account_id")
    warehouse_id = _invoice_warehouse_id(session, return_invoice)

    entry = JournalEntry(
        entry_date=return_invoice.invoice_date,
        ref_no=_next_ref_no(session, "JE-SRET"),
        description=f"مرتجع بيع رقم {return_invoice.invoice_no} — {return_invoice.party_name}"
                    + (f" (مرتبط بفاتورة {original_invoice.invoice_no})" if original_invoice else " (مرتجع حر)"),
        source_type="sales_return", source_id=return_invoice.id,
        currency_code=return_invoice.currency_code, exchange_rate=return_invoice.exchange_rate,
        status=JournalEntryStatus.POSTED,
    )

    totals = compute_invoice_totals(return_invoice)
    total_sales, total_tax, total_cost = Decimal("0"), Decimal("0"), Decimal("0")

    for line_total in totals.lines:
        item = session.get(Item, line_total.line.item_id)
        total_sales += line_total.net_after_all_discounts
        total_tax += line_total.tax_amount

        unit_cost = _return_unit_cost(session, item.id, original_invoice)
        total_cost += money(unit_cost * D(line_total.line.quantity))

        # البضاعة ترجع للمخزون (IN)، عكس اتجاه البيع تماماً
        session.add(InventoryMovement(
            item_id=item.id, warehouse_id=warehouse_id, direction=MovementDirection.IN,
            quantity=line_total.line.quantity, unit_cost=unit_cost,
            movement_date=return_invoice.invoice_date, source_type="sales_return",
            source_id=return_invoice.id,
        ))

    # مدين: مبيعات + ضريبة (تخفيض الإيراد والضريبة) | دائن: الصندوق/العميل (استرداد)
    entry.lines = [
        _jline(sales_acc, total_sales, Decimal("0"), return_invoice.exchange_rate),
        _jline(cash_or_ar, Decimal("0"), total_sales + total_tax, return_invoice.exchange_rate),
    ]
    if total_tax:
        entry.lines.append(_jline(tax_acc, total_tax, Decimal("0"), return_invoice.exchange_rate))
    if total_cost:
        item0 = session.get(Item, return_invoice.lines[0].item_id)
        entry.lines.append(_jline(item0.inventory_account_id, total_cost, Decimal("0"), return_invoice.exchange_rate))
        entry.lines.append(_jline(item0.cogs_account_id, Decimal("0"), total_cost, return_invoice.exchange_rate))

    if not entry.is_balanced():
        raise PostingError("خطأ داخلي: قيد المرتجع غير متوازن — لا يُرحّل")

    return_invoice.status = InvoiceStatus.POSTED
    session.add(entry)
    session.flush()
    return_invoice.journal_entry_id = entry.id
    return entry


def post_purchase_return(session: Session, return_invoice: Invoice, is_cash: bool = True) -> JournalEntry:
    if return_invoice.status == InvoiceStatus.POSTED:
        raise PostingError("المرتجع مرحّل أصلاً — لا يمكن ترحيله مرتين")
    try:
        validate_invoice_for_posting(return_invoice)
        validate_return_against_original(session, return_invoice)
    except InvoiceValidationError as e:
        raise PostingError(str(e))

    original_invoice = (
        session.get(Invoice, return_invoice.original_invoice_id)
        if return_invoice.original_invoice_id else None
    )

    if is_cash:
        cash_or_ap = _get_setting(session, "default_cash_account_id")
    else:
        party_account = get_or_create_party_account(session, return_invoice.party_name, is_customer=False)
        cash_or_ap = party_account.id
    tax_acc = _get_setting(session, "default_purchases_tax_account_id")
    warehouse_id = _invoice_warehouse_id(session, return_invoice)

    entry = JournalEntry(
        entry_date=return_invoice.invoice_date,
        ref_no=_next_ref_no(session, "JE-PRET"),
        description=f"مرتجع شراء رقم {return_invoice.invoice_no} — {return_invoice.party_name}"
                    + (f" (مرتبط بفاتورة {original_invoice.invoice_no})" if original_invoice else " (مرتجع حر)"),
        source_type="purchase_return", source_id=return_invoice.id,
        currency_code=return_invoice.currency_code, exchange_rate=return_invoice.exchange_rate,
        status=JournalEntryStatus.POSTED,
    )

    totals = compute_invoice_totals(return_invoice)
    total_purchase, total_tax = Decimal("0"), Decimal("0")
    inventory_credits: dict[int, Decimal] = {}

    for line_total in totals.lines:
        item = session.get(Item, line_total.line.item_id)
        total_purchase += line_total.net_after_all_discounts
        total_tax += line_total.tax_amount
        inventory_credits[item.inventory_account_id] = (
            inventory_credits.get(item.inventory_account_id, Decimal("0")) + line_total.net_after_all_discounts
        )
        unit_cost = _return_unit_cost(session, item.id, original_invoice)

        # البضاعة تخرج من المخزون (OUT) — ترجع للمورد
        session.add(InventoryMovement(
            item_id=item.id, warehouse_id=warehouse_id, direction=MovementDirection.OUT,
            quantity=line_total.line.quantity, unit_cost=unit_cost,
            movement_date=return_invoice.invoice_date, source_type="purchase_return",
            source_id=return_invoice.id,
        ))

    # مدين: المورد/الصندوق (تخفيض الذمم أو استرداد نقدي) | دائن: المخزون + الضريبة
    entry.lines = [_jline(cash_or_ap, total_purchase + total_tax, Decimal("0"), return_invoice.exchange_rate)]
    for inv_acc_id, amount in inventory_credits.items():
        entry.lines.append(_jline(inv_acc_id, Decimal("0"), amount, return_invoice.exchange_rate))
    if total_tax:
        entry.lines.append(_jline(tax_acc, Decimal("0"), total_tax, return_invoice.exchange_rate))

    if not entry.is_balanced():
        raise PostingError("خطأ داخلي: قيد المرتجع غير متوازن — لا يُرحّل")

    return_invoice.status = InvoiceStatus.POSTED
    session.add(entry)
    session.flush()
    return_invoice.journal_entry_id = entry.id
    return entry
