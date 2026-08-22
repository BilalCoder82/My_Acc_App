"""
Invoice Validation — ضوابط قبل الترحيل
============================================
هذا التحقق يُفرض من posting.py نفسه (لا يعتمد على الواجهة لاستدعائه) —
لأن هذه ضوابط محاسبية داخلية، ليست مجرد تجربة استخدام. أي طريق آخر
لترحيل فاتورة (سكربت، اختبار، واجهة مستقبلية أخرى) يمر من هنا حتماً.
"""

from __future__ import annotations
from decimal import Decimal
from app.models import Invoice


class InvoiceValidationError(Exception):
    """أخطاء تحقق متعددة مجمّعة برسالة واحدة واضحة للمستخدم."""


def validate_invoice_for_posting(invoice: Invoice) -> None:
    errors: list[str] = []

    if not invoice.lines:
        errors.append("الفاتورة بدون بنود — أضف صنفاً واحداً على الأقل")

    if not (invoice.party_name or "").strip():
        errors.append("اسم العميل/المورد مطلوب")

    for i, line in enumerate(invoice.lines, start=1):
        try:
            qty = Decimal(str(line.quantity))
        except Exception:
            qty = Decimal("0")
        if qty <= 0:
            errors.append(f"البند {i}: الكمية يجب أن تكون أكبر من صفر")

        try:
            price = Decimal(str(line.unit_price))
        except Exception:
            price = Decimal("-1")
        if price < 0:
            errors.append(f"البند {i}: السعر غير صالح")

    if errors:
        raise InvoiceValidationError(" — ".join(errors))
