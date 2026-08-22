"""
Invoice Calculation Contract
==============================
هذا الملف هو المصدر الوحيد لصيغة حساب الفاتورة. أي شاشة أو تقرير أو محرك
ترحيل يحتاج "كم صافي هالفاتورة" يستدعي compute_invoice_totals() — لا يُعاد
كتابة هذا الحساب بمكان آخر إطلاقاً.

كل الحسابات هنا بـDecimal حصراً (راجع app/services/money.py) — لا float
إطلاقاً بأي خطوة، لتفادي أخطاء التقريب الثنائي المعروفة بالقيم المالية.

ترتيب الحساب (ثابت، موثّق، مُختبر):
1. line_net = (quantity × unit_price) − line_discount
2. invoice_subtotal = Σ line_net
3. invoice_discount يُوزَّع نسبياً على كل بند حسب وزنه من subtotal
4. الضريبة تُحسب على صافي كل بند بعد الحسمين، بنسبة ضريبة ذلك البند
"""

from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from app.models import Invoice, InvoiceLine
from app.services.money import D, money


def _safe(value, fallback="0") -> Decimal:
    """يحوّل None لصفر بأمان — الحقول الاختيارية (حسم/ضريبة) قد تبقى None
    على كائنات مؤقتة (transient) لم تُحفَظ بعد بقاعدة البيانات، لأن القيمة
    الافتراضية بـSQLAlchemy (default=0) لا تُطبَّق إلا عند flush() الفعلي."""
    return D(fallback) if value is None else D(value)


@dataclass
class LineTotal:
    line: InvoiceLine
    net_before_invoice_discount: Decimal
    prorated_invoice_discount: Decimal
    net_after_all_discounts: Decimal
    tax_amount: Decimal
    line_grand_total: Decimal


@dataclass
class InvoiceTotals:
    lines: list[LineTotal]
    subtotal: Decimal
    total_discount: Decimal
    total_tax: Decimal
    grand_total: Decimal


def _line_discount(line: InvoiceLine) -> Decimal:
    gross = _safe(line.quantity) * _safe(line.unit_price)
    percent_part = gross * _safe(line.discount_percent) / D(100)
    return percent_part + _safe(line.discount_amount)


def compute_invoice_totals(invoice: Invoice) -> InvoiceTotals:
    if not invoice.lines:
        raise ValueError("الفاتورة بدون بنود — لا يمكن حساب الإجمالي")

    raw_lines = []
    subtotal = Decimal("0")
    line_discount_sum = Decimal("0")
    for line in invoice.lines:
        gross = _safe(line.quantity) * _safe(line.unit_price)
        ld = _line_discount(line)
        net = max(gross - ld, Decimal("0"))
        raw_lines.append((line, net))
        subtotal += net
        line_discount_sum += ld

    invoice_discount_total = (
        subtotal * _safe(invoice.discount_percent) / D(100) + _safe(invoice.discount_amount)
    )
    invoice_discount_total = min(invoice_discount_total, subtotal)

    results: list[LineTotal] = []
    total_tax = Decimal("0")
    for line, net in raw_lines:
        weight = (net / subtotal) if subtotal > 0 else Decimal("0")
        prorated = invoice_discount_total * weight
        net_final = max(net - prorated, Decimal("0"))
        tax = net_final * _safe(line.tax_rate) / D(100)
        total_tax += tax
        results.append(LineTotal(
            line=line,
            net_before_invoice_discount=money(net),
            prorated_invoice_discount=money(prorated),
            net_after_all_discounts=money(net_final),
            tax_amount=money(tax),
            line_grand_total=money(net_final + tax),
        ))

    grand_total = (subtotal - invoice_discount_total) + total_tax

    return InvoiceTotals(
        lines=results,
        subtotal=money(subtotal),
        total_discount=money(line_discount_sum + invoice_discount_total),
        total_tax=money(total_tax),
        grand_total=money(grand_total),
    )
