"""
app/services/invoice_cancel.py
=================================
Cancel/Reverse للفواتير — راجع WORKFLOW.md §44 للقاعدة الكاملة قبل تعديل
أي شيء هنا. Cancel ≠ Return: عكس حرفي بالقيم التاريخية نفسها، لا حدث
تجاري جديد ولا إعادة حساب.

PHASE3B4/Group 3-C: القيد العكسي يُبنى الآن عبر journal_edit.py::reverse()
(Accounting Posting Boundary) بدل بناء يدوي داخلي — namespace الترقيم
تبع لذلك من "INV-CXL" الحصري إلى "JV-REV" المشترك مع كل عكوس المحاسبة
العامة (نفس namespace الذي يستخدمه reverse_opening_party_entry والعكس
اليدوي العام). هذا تغيير مقصود في بنية الترقيم، وافق عليه Gate Review.

**"INV-CXL-NNNNNN" أصبح namespace تاريخياً (legacy) متروكاً بالكامل —
لا يُستخدَم لأي قيد جديد بعد الآن، ولا توجد أي جهة بالكود تولّد أرقاماً
بهذه الصيغة بعد اكتمال هذه الهجرة.** يبقى ظاهراً فقط على القيود القديمة
الموجودة فعلاً بقواعد بيانات العملاء (قبل هذه الهجرة) — لا Migration ولا
Backfill مطلوب له تحديداً (خلافاً لـJV/JV-REV/JE-SAL/JE-PUR/JV-OPEN/
JV-OPNPTY)، لأنه لن يُستكمَل أو يُقارَن به أي رقم جديد مطلقاً.
"""
from __future__ import annotations
from datetime import date

from sqlalchemy.orm import Session

from app.models import (
    Invoice, InvoiceStatus, InventoryMovement, MovementDirection,
    JournalEntry, SettlementAllocation,
)
from app.services.journal_edit import reverse, JournalEditError


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

    # --- عكس القيد محاسبياً عبر Boundary (Group 3-C) — بعد نجاح الفحوص
    # الستة أعلاه فقط، لا قبلها. reverse() تُعيد فحص POSTED/is_reversal_of/
    # عدم التكرار داخلياً أيضاً (دفاع مزدوج غير ضار، لا حاجة لحذفه) لكنها
    # لا تعرف شيئاً عن SettlementAllocation — ذاك يبقى هنا حصراً. ---
    try:
        reversal_entry = reverse(
            session, original_entry, reversal_date=cancel_date,
            description=f"إلغاء الفاتورة {invoice.invoice_no}",
            source_type="invoice_cancel", source_id=invoice.id,
        )
    except JournalEditError as e:
        session.rollback()
        raise CancelNotAllowedError(str(e))

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

    try:
        session.add_all(reversal_movements)
        invoice.status = InvoiceStatus.CANCELLED
        session.flush()
    except Exception:
        session.rollback()
        raise
    return reversal_entry
