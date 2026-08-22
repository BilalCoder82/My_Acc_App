"""
Invoice Posting Engine
=======================
يحوّل الفاتورة (Invoice) إلى قيد محاسبي متوازن (JournalEntry) + حركات مخزون.
المرتجعات لا تولّد قيداً جديداً مستقلاً — بل تعكس القيد الأصلي بالضبط.

كل الحسابات المالية هنا بـDecimal حصراً (app/services/money.py) — لا float.

الإعدادات المطلوبة مسبقاً بجدول Settings (Setting.key):
    default_cash_account_id      -> حساب الصندوق/الذمم الافتراضي
    default_sales_account_id     -> حساب المبيعات
    default_purchases_tax_account_id / default_sales_tax_account_id
"""

from __future__ import annotations
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import (
    Invoice, InvoiceKind, InvoiceStatus,
    JournalEntry, JournalLine, JournalEntryStatus,
    InventoryMovement, MovementDirection,
    Item, Setting, Warehouse,
)
from app.services.parties import get_or_create_party_account
from app.services.invoice_calc import compute_invoice_totals
from app.services.invoice_validation import validate_invoice_for_posting, InvoiceValidationError
from app.services.money import D, money


class PostingError(Exception):
    pass


def _get_setting(session: Session, key: str) -> int:
    row = session.get(Setting, key)
    if row is None:
        raise PostingError(f"إعداد مفقود بالنظام: {key} — راجع شاشة الإعدادات")
    return int(row.value)


def _next_ref_no(session: Session, prefix: str) -> str:
    count = session.query(JournalEntry).filter(
        JournalEntry.ref_no.like(f"{prefix}-%")
    ).count()
    return f"{prefix}-{count + 1:05d}"


def get_default_warehouse(session: Session) -> Warehouse:
    """يرجّع المستودع الرئيسي، وينشئه تلقائياً إن لم يكن موجوداً."""
    wh = session.query(Warehouse).filter_by(name_ar="المستودع الرئيسي").first()
    if wh is None:
        wh = Warehouse(name_ar="المستودع الرئيسي", is_active=True)
        session.add(wh)
        session.flush()
    return wh


def _invoice_warehouse_id(session: Session, invoice: Invoice) -> int:
    return invoice.warehouse_id or get_default_warehouse(session).id


def _jline(account_id: int, debit: Decimal, credit: Decimal, exchange_rate) -> JournalLine:
    """ينشئ سطر قيد مع حساب القيمة بالعملة الأساسية تلقائياً."""
    d, c, rate = money(debit), money(credit), D(exchange_rate)
    return JournalLine(
        account_id=account_id, debit=d, credit=c,
        debit_base=money(d * rate), credit_base=money(c * rate),
    )


def _average_cost(session: Session, item_id: int) -> Decimal:
    """كلفة الوحدة الحالية = آخر متوسط مرجّح محفوظ من حركات الدخول (كل المستودعات)."""
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
        return Decimal("0")
    return total_cost / total_qty


def post_sales_invoice(session: Session, invoice: Invoice, is_cash: bool = True) -> JournalEntry:
    if invoice.status == InvoiceStatus.POSTED:
        raise PostingError("الفاتورة مرحّلة أصلاً — لا يمكن ترحيلها مرتين")
    try:
        validate_invoice_for_posting(invoice)
    except InvoiceValidationError as e:
        raise PostingError(str(e))

    if is_cash:
        cash_or_ar = _get_setting(session, "default_cash_account_id")
    else:
        party_account = get_or_create_party_account(session, invoice.party_name, is_customer=True)
        cash_or_ar = party_account.id
    sales_acc = _get_setting(session, "default_sales_account_id")
    tax_acc = _get_setting(session, "default_sales_tax_account_id")
    warehouse_id = _invoice_warehouse_id(session, invoice)

    entry = JournalEntry(
        entry_date=invoice.invoice_date,
        ref_no=_next_ref_no(session, "JE-SAL"),
        description=f"فاتورة بيع رقم {invoice.invoice_no} — {invoice.party_name}",
        source_type="sales_invoice", source_id=invoice.id,
        currency_code=invoice.currency_code, exchange_rate=invoice.exchange_rate,
        status=JournalEntryStatus.POSTED,
    )

    total_sales, total_tax, total_cogs = Decimal("0"), Decimal("0"), Decimal("0")
    totals = compute_invoice_totals(invoice)

    for line_total in totals.lines:
        item = session.get(Item, line_total.line.item_id)
        total_sales += line_total.net_after_all_discounts
        total_tax += line_total.tax_amount

        unit_cost = _average_cost(session, item.id)
        total_cogs += money(unit_cost * D(line_total.line.quantity))

        session.add(InventoryMovement(
            item_id=item.id, warehouse_id=warehouse_id, direction=MovementDirection.OUT,
            quantity=line_total.line.quantity, unit_cost=unit_cost,
            movement_date=invoice.invoice_date, source_type="sales_invoice", source_id=invoice.id,
        ))

    entry.lines = [
        _jline(cash_or_ar, total_sales + total_tax, Decimal("0"), invoice.exchange_rate),
        _jline(sales_acc, Decimal("0"), total_sales, invoice.exchange_rate),
    ]
    if total_tax:
        entry.lines.append(_jline(tax_acc, Decimal("0"), total_tax, invoice.exchange_rate))
    if total_cogs:
        item0 = session.get(Item, invoice.lines[0].item_id)
        entry.lines.append(_jline(item0.cogs_account_id, total_cogs, Decimal("0"), invoice.exchange_rate))
        entry.lines.append(_jline(item0.inventory_account_id, Decimal("0"), total_cogs, invoice.exchange_rate))

    if not entry.is_balanced():
        raise PostingError("خطأ داخلي: القيد غير متوازن — لا يُرحّل")

    invoice.status = InvoiceStatus.POSTED
    session.add(entry)
    session.flush()
    invoice.journal_entry_id = entry.id
    return entry


def post_purchase_invoice(session: Session, invoice: Invoice, is_cash: bool = True) -> JournalEntry:
    """الشراء لا يولّد قيد COGS — البضاعة تدخل المخزون فقط."""
    if invoice.status == InvoiceStatus.POSTED:
        raise PostingError("الفاتورة مرحّلة أصلاً — لا يمكن ترحيلها مرتين")
    try:
        validate_invoice_for_posting(invoice)
    except InvoiceValidationError as e:
        raise PostingError(str(e))

    if is_cash:
        cash_or_ap = _get_setting(session, "default_cash_account_id")
    else:
        party_account = get_or_create_party_account(session, invoice.party_name, is_customer=False)
        cash_or_ap = party_account.id
    tax_acc = _get_setting(session, "default_purchases_tax_account_id")
    warehouse_id = _invoice_warehouse_id(session, invoice)

    entry = JournalEntry(
        entry_date=invoice.invoice_date,
        ref_no=_next_ref_no(session, "JE-PUR"),
        description=f"فاتورة شراء رقم {invoice.invoice_no} — {invoice.party_name}",
        source_type="purchase_invoice", source_id=invoice.id,
        currency_code=invoice.currency_code, exchange_rate=invoice.exchange_rate,
        status=JournalEntryStatus.POSTED,
    )

    total_purchase, total_tax = Decimal("0"), Decimal("0")
    inventory_debits: dict[int, Decimal] = {}
    totals = compute_invoice_totals(invoice)

    for line_total in totals.lines:
        item = session.get(Item, line_total.line.item_id)
        total_purchase += line_total.net_after_all_discounts
        total_tax += line_total.tax_amount

        inventory_debits[item.inventory_account_id] = (
            inventory_debits.get(item.inventory_account_id, Decimal("0")) + line_total.net_after_all_discounts
        )

        q = D(line_total.line.quantity)
        unit_cost_after_discount = (line_total.net_after_all_discounts / q) if q else Decimal("0")
        session.add(InventoryMovement(
            item_id=item.id, warehouse_id=warehouse_id, direction=MovementDirection.IN,
            quantity=line_total.line.quantity, unit_cost=unit_cost_after_discount,
            movement_date=invoice.invoice_date, source_type="purchase_invoice", source_id=invoice.id,
        ))

    entry.lines = [_jline(cash_or_ap, Decimal("0"), total_purchase + total_tax, invoice.exchange_rate)]
    for inv_acc_id, amount in inventory_debits.items():
        entry.lines.append(_jline(inv_acc_id, amount, Decimal("0"), invoice.exchange_rate))
    if total_tax:
        entry.lines.append(_jline(tax_acc, total_tax, Decimal("0"), invoice.exchange_rate))

    if not entry.is_balanced():
        raise PostingError("خطأ داخلي: القيد غير متوازن — لا يُرحّل")

    invoice.status = InvoiceStatus.POSTED
    session.add(entry)
    session.flush()
    invoice.journal_entry_id = entry.id
    return entry


def post_return(session: Session, original_invoice: Invoice, return_invoice: Invoice) -> JournalEntry:
    """يعكس القيد الأصلي بالضبط بدل توليد قيد مستقل، لضمان الدقة."""
    if original_invoice.journal_entry_id is None:
        raise PostingError("الفاتورة الأصلية غير مرحّلة — لا يمكن عكسها")

    original_entry = session.get(JournalEntry, original_invoice.journal_entry_id)
    reversal = JournalEntry(
        entry_date=return_invoice.invoice_date,
        ref_no=_next_ref_no(session, "JE-RET"),
        description=f"عكس قيد مرتجع — أصل الفاتورة {original_invoice.invoice_no}",
        source_type="return_invoice", source_id=return_invoice.id,
        is_reversal_of=original_entry.id,
        currency_code=original_entry.currency_code, exchange_rate=original_entry.exchange_rate,
        status=JournalEntryStatus.POSTED,
    )
    reversal.lines = [
        _jline(l.account_id, D(l.credit), D(l.debit), original_entry.exchange_rate)
        for l in original_entry.lines
    ]
    if not reversal.is_balanced():
        raise PostingError("خطأ داخلي: قيد العكس غير متوازن")

    return_invoice.status = InvoiceStatus.POSTED
    session.add(reversal)
    session.flush()
    return_invoice.journal_entry_id = reversal.id
    return reversal
