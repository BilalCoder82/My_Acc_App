"""
Invoice Edit Rules
=====================
قاعدة صارمة واحدة: فاتورة بحالة POSTED لا تُعدَّل ولا تُحذف مباشرة —
لا على مستوى البند ولا على مستوى الفاتورة كاملة. الإجراء الصحيح الوحيد:
عكسها عبر post_return()، ثم إنشاء فاتورة تصحيحية جديدة إذا لزم.

هذا يخالف عمداً سلوك QuickBooks Desktop (الذي يسمح بتعديل مستند مرحّل
مباشرة) لأن ذلك السلوك يكسر أثر التدقيق — راجع WORKFLOW.md قسم 6.

كل دالة هنا تُستدعى من الواجهة لاحقاً؛ لا يوجد أي منطق تعديل مباشر
بالـUI بدون المرور من هنا أولاً.
"""

from __future__ import annotations
from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceLine, InvoiceStatus


class EditNotAllowedError(Exception):
    pass


def ensure_editable(invoice: Invoice) -> None:
    if invoice.status == InvoiceStatus.POSTED:
        raise EditNotAllowedError(
            f"الفاتورة {invoice.invoice_no} مرحّلة — لا يمكن تعديلها أو حذف "
            "بنودها مباشرة. استخدم post_return() لعكسها ثم أنشئ فاتورة تصحيحية."
        )
    if invoice.status == InvoiceStatus.CANCELLED:
        raise EditNotAllowedError(f"الفاتورة {invoice.invoice_no} ملغاة — لا يمكن تعديلها.")


def add_line(session: Session, invoice: Invoice, item_id: int, quantity: float,
             unit_price: float, discount_percent: float = 0, discount_amount: float = 0,
             tax_rate: float = 0) -> InvoiceLine:
    ensure_editable(invoice)
    line = InvoiceLine(
        invoice_id=invoice.id, item_id=item_id, quantity=quantity, unit_price=unit_price,
        discount_percent=discount_percent, discount_amount=discount_amount, tax_rate=tax_rate,
    )
    session.add(line)
    session.flush()
    return line


def update_line(session: Session, line: InvoiceLine, **fields) -> InvoiceLine:
    ensure_editable(line.invoice)
    allowed = {"quantity", "unit_price", "discount_percent", "discount_amount", "tax_rate"}
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"حقل غير مسموح بالتعديل: {key}")
        setattr(line, key, value)
    session.flush()
    return line


def remove_line(session: Session, line: InvoiceLine) -> None:
    ensure_editable(line.invoice)
    if len(line.invoice.lines) <= 1:
        raise EditNotAllowedError("لا يمكن حذف آخر بند بالفاتورة — احذف الفاتورة كاملة بدلاً من ذلك")
    session.delete(line)
    session.flush()


def delete_invoice(session: Session, invoice: Invoice) -> None:
    """حذف فعلي مسموح فقط للمسودات (DRAFT). أي فاتورة مرحّلة تُعكس، لا تُحذف."""
    ensure_editable(invoice)
    session.delete(invoice)
    session.flush()
