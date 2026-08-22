"""
Money Handling — سياسة موحّدة للقيم المالية
================================================
قاعدة صارمة: أي رقم مالي بالنظام يُحوَّل لـDecimal فور دخوله، ولا يُحوَّل
لـfloat أبداً في أي حساب. SQLAlchemy Numeric يرجّع Decimal تلقائياً من
قاعدة البيانات (asdecimal=True هو الافتراضي) — المشكلة كانت أننا نكسر
هذا بتحويل يدوي لـfloat، وهذا ما توقف عنه من الآن.

سياسة التقريب: ROUND_HALF_UP على منزلتين عشريتين للمبالغ المالية،
وثلاث منازل للكميات — موحّدة بكل النظام لمنع تراكم فروقات تقريب.

ملاحظة SQLite: التخزين الفعلي بـSQLite ليس Decimal حقيقياً (REAL/TEXT
حسب القيمة). الالتزام بالتقريب لمنزلتين عند كل خطوة حساب (لا فقط عند
العرض النهائي) هو ما يمنع تراكم الخطأ عبر عمليات متتالية.
"""

from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP

MONEY_QUANT = Decimal("0.01")
QTY_QUANT = Decimal("0.001")


def D(value) -> Decimal:
    """يحوّل أي قيمة (float, int, str, Decimal) لـDecimal بأمان — عبر str()
    دائماً لو كانت float، لتفادي أخطاء تمثيل float الثنائي الشهيرة
    (مثال: Decimal(0.1) != Decimal('0.1'))."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money(value) -> Decimal:
    return D(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def qty(value) -> Decimal:
    return D(value).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)
