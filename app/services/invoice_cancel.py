"""
app/services/invoice_cancel.py
=================================
Cancel/Reverse للفواتير — راجع WORKFLOW.md §44 للقاعدة الكاملة قبل تعديل
أي شيء هنا. Cancel ≠ Return: عكس حرفي بالقيم التاريخية نفسها، لا حدث
تجاري جديد ولا إعادة حساب.
"""
from __future__ import annotations
from datetime import date

from sqlalchemy.orm import Session

from app.models import (
    Invoice, InvoiceStatus, InventoryMovement, MovementDirection,
    JournalEntry, JournalEntryStatus, JournalLine, SettlementAllocation,
)


class CancelNotAllowedError(Exception):
    pass


def cancel_invoice(session: Session, invoice: Invoice, cancel_date: date) -> JournalEntry:
    """
    يُلغي فاتورة POSTED بالكامل: قيد عكسي حرفي + عكس كل حركات المخزون
    المرتبطة بنفس تكلفتها الأصلية بالضبط. المستند الأصلي وقيده وحركاته
    لا تُحذف ولا تُعدَّل — فقط status → CANCELLED، وأثر عكسي منفصل
    وقابل للتتبع (WORKFLOW.md §44.2).
    """
    if invoice.status == InvoiceStatus.CANCELLED:
        raise CancelNotAllowedError(f"الفاتورة {invoice.invoice_no} ملغاة أصلاً — لا يجوز إلغاؤها مرتين")
    if invoice.status != InvoiceStatus.POSTED:
        raise CancelNotAllowedError(f"الفاتورة {invoice.invoice_no} غير مرحّلة — لا يوجد أثر لعكسه")

    # Phase 3B-3: Settlement لم يعد يحمل invoice_id مباشرة — انتقل بالكامل
    # لـSettlementAllocation (PHASE3B3_DESIGN_SPEC.md §1.10/§9). تصحيح
    # ميكانيكي محتّم بقرار §1.10 نفسه، لا تغييراً معمارياً جديداً.
    existing_settlements = session.query(SettlementAllocation).filter_by(invoice_id=invoice.id).count()
    if existing_settlements > 0:
        raise CancelNotAllowedError(
            f"الفاتورة {invoice.invoice_no} لها {existing_settlements} تسوية (قبض/دفع) مرتبطة — "
            "لا يجوز إلغاؤها مباشرة (WORKFLOW.md §44.3). عالج التسويات أولاً."
        )

    original_entry: JournalEntry = session.get(JournalEntry, invoice.journal_entry_id)
    if original_entry is None:
        raise CancelNotAllowedError(f"الفاتورة {invoice.invoice_no} بلا قيد مرحّل — حالة غير متسقة")
    if original_entry.is_reversal_of is not None:
        raise CancelNotAllowedError("لا يجوز إلغاء فاتورة قيدها هو نفسه قيد عكسي أصلاً")
    already_reversed = session.query(JournalEntry).filter_by(is_reversal_of=original_entry.id).first()
    if already_reversed is not None:
        raise CancelNotAllowedError(
            f"الفاتورة {invoice.invoice_no} أُلغيت أصلاً بالقيد {already_reversed.ref_no}"
        )

    # --- عكس القيد حرفياً: مدين↔دائن، بنفس debit_base/credit_base تماماً
    # (نفس نمط reverse_manual_entry الموجود فعلياً — journal_edit.py) ---
    count = session.query(JournalEntry).filter(JournalEntry.ref_no.like("INV-CXL-%")).count()
    reversal_entry = JournalEntry(
        entry_date=cancel_date,
        ref_no=f"INV-CXL-{count + 1:06d}",
        description=f"إلغاء الفاتورة {invoice.invoice_no}",
        source_type="invoice_cancel", source_id=invoice.id, is_reversal_of=original_entry.id,
        currency_code=original_entry.currency_code, exchange_rate=original_entry.exchange_rate,
        status=JournalEntryStatus.POSTED,
    )
    reversal_entry.lines = [
        JournalLine(
            account_id=l.account_id, debit=l.credit, credit=l.debit,
            debit_base=l.credit_base, credit_base=l.debit_base,
            line_currency_code=l.line_currency_code, line_exchange_rate=l.line_exchange_rate,
            cost_center=l.cost_center,
        )
        for l in original_entry.lines
    ]
    if not reversal_entry.is_balanced():
        raise CancelNotAllowedError("خطأ داخلي: قيد الإلغاء غير متوازن — لا يُرحّل")

    # --- عكس حركات المخزون حرفياً: نفس الكمية ونفس unit_cost الأصلي،
    # اتجاه معاكس فقط — لا إعادة حساب بالمتوسط الحالي (WORKFLOW.md §44.4) ---
    original_movements = session.query(InventoryMovement).filter(
        InventoryMovement.source_type.in_(("sales_invoice", "purchase_invoice")),
        InventoryMovement.source_id == invoice.id,
    ).all()
    reversal_movements = [
        InventoryMovement(
            item_id=m.item_id, warehouse_id=m.warehouse_id,
            direction=MovementDirection.OUT if m.direction == MovementDirection.IN else MovementDirection.IN,
            quantity=m.quantity, unit_cost=m.unit_cost,  # نفس القيمة الأصلية بالضبط
            movement_date=cancel_date, source_type="invoice_cancel", source_id=invoice.id,
            note=f"عكس إلغاء الفاتورة {invoice.invoice_no}",
        )
        for m in original_movements
    ]

    session.add(reversal_entry)
    session.add_all(reversal_movements)
    invoice.status = InvoiceStatus.CANCELLED
    session.flush()
    return reversal_entry
