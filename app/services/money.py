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


RATE_QUANT = Decimal("0.000001")


def rate(value) -> Decimal:
    """تُطبَّق على أي سعر صرف يُقرأ من قاعدة البيانات أو يُدخَل بالواجهة —
    نفس مبدأ money()، لكن بدقة 6 منازل (مناسبة لأسعار الصرف، لا 2 منزلة).
    ضرورية تحديداً بعد إعادة القراءة من SQLite: التخزين الفعلي REAL تقريبي،
    وقيمة مثل 1.0 ممكن ترجع 0.999999999... وتتضخّم لخطأ ملحوظ عند الضرب
    بمبالغ كبيرة (اكتُشف فعلياً: سعر صرف 1 تحوّل لفرق توازن 8×10⁻⁸ بعد
    إعادة تحميل قيد من القاعدة — كان بيظهر "8E-8" بدل "0" بالضبط)."""
    return D(value).quantize(RATE_QUANT, rounding=ROUND_HALF_UP)


def qty(value) -> Decimal:
    return D(value).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)
