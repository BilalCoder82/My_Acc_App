"""
app/services/sanity_guard.py
==============================
حارس **اتساق**، وليس حارس **حجم** — درس مُستفاد من مراجعة سابقة: حارس
مبني على "المبلغ كبير جداً" سيرفض معاملات مشروعة (مثال: 100,000 USD
بسعر صرف مرتفع). هذا الحارس لا يعرف عن حجم المبلغ إطلاقاً؛ يتحقق فقط
أن القيمة المخزَّنة (debit_base/credit_base) تطابق ما تنتجه معادلة
التحويل المتوقعة (raw × exchange_rate)، بصرف النظر عن كِبر الرقم.

مُصمَّم للاستدعاء من داخل _jline()/_jline_base() (app/services/posting.py)
مباشرة على كل سطر يُنشأ — تكلفته زهيدة (عملية قسمة ومقارنة واحدة)،
ويحوّل الخطأ الذي وقع فعلياً (WORKFLOW.md §30) من رقم خاطئ صامت يمر عبر
is_balanced() إلى استثناء فوري وقت الترحيل.
"""

from decimal import Decimal


class AccountingSanityError(Exception):
    """يُرفع عند اكتشاف قيمة قيد لا تطابق معادلة التحويل المتوقعة."""


def assert_reasonable_conversion(
    *,
    raw_amount: Decimal,
    stored_base_amount: Decimal,
    exchange_rate: Decimal,
    context: str,
    tolerance: Decimal = Decimal("0.02"),
) -> None:
    """
    يتحقق أن stored_base_amount قريب فعلياً من raw_amount × exchange_rate.
    tolerance بالعملة الأساسية مباشرة (وليس نسبة) — الافتراضي 0.02 يطابق
    ضعف MONEY_QUANT (0.01) في app/services/money.py، لاستيعاب تراكم
    تقريب خطوتين كحد أقصى دون تمرير خطأ حقيقي.

    context: نص وصفي لتسريع تتبع مصدر أي مشكلة (مثال: "post_purchase_invoice inv=P-00042").
    """
    expected = raw_amount * exchange_rate

    if abs(stored_base_amount - expected) <= tolerance:
        return

    # قيمة صفرية أو شبه صفرية: نتحقق بالفرق المطلق فقط أعلاه، لا داعي لنسبة هنا.
    if expected == 0:
        return

    ratio = stored_base_amount / expected
    if abs(ratio - exchange_rate) < tolerance * max(exchange_rate, Decimal("1")):
        hint = "يبدو أن القيمة حُوّلت مرتين (double conversion) — راجع اختيار _jline مقابل _jline_base"
    elif exchange_rate != 0 and abs(ratio - (Decimal("1") / exchange_rate)) < tolerance:
        hint = "يبدو أن القيمة لم تُحوَّل إطلاقاً رغم أنها بعملة أجنبية"
    else:
        hint = "انحراف غير مفسَّر عن القيمة المتوقعة"

    raise AccountingSanityError(
        f"[sanity_guard] {context}: raw={raw_amount} rate={exchange_rate} "
        f"expected≈{expected} stored={stored_base_amount} — {hint}"
    )
