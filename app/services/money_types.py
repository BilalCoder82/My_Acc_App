"""
app/services/money_types.py
==============================
طبقة اختيارية (لم تُفرض على posting.py بعد عمداً — راجع القرار الموثّق
أدناه). تحل مشكلة مختلفة عن sanity_guard.py: sanity_guard يكتشف الخطأ
بعد وقوعه أثناء التشغيل (runtime)، بينما هذه الكائنات تمنعه بنيوياً وقت
كتابة الكود نفسه (خطأ AttributeError/TypeError فوري بدل رقم خاطئ صامت).

القرار الموثّق: لا نستبدل _jline/_jline_base الحاليتين الآن بهاتين
الدالتين البديلتين في كل الاستدعاءات (حوالي 15 موضعاً بين posting.py
وreturns.py) — هذا حجم refactor غير ضروري في هذه المرحلة تحديداً بعد
أن أثبت sanity_guard.py + fuzz+oracle فعاليتهما الفورية بتكلفة أقل.
تُترك هذه الوحدة جاهزة للاعتماد التدريجي لاحقاً (سطر باستدعاء واحد
بالمرة عند لمس كل دالة ترحيل من أي سبب آخر مستقبلاً)، لا كإعادة كتابة
دفعة واحدة.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TransactionAmount:
    """مبلغ بعملة المستند (فاتورة/قيد)، لم يُحوَّل بعد."""
    value: Decimal
    exchange_rate: Decimal

    def to_base(self) -> "BaseCurrencyAmount":
        return BaseCurrencyAmount(value=self.value * self.exchange_rate, source="converted")


@dataclass(frozen=True)
class BaseCurrencyAmount:
    """
    مبلغ بالعملة الأساسية فعلياً — محوَّل أو محسوب أصلاً بالأساسية
    (متوسط تكلفة). لا يملك to_base() — أي محاولة لتحويله ثانية تصبح
    AttributeError وقت التطوير، لا رقماً خاطئاً صامتاً وقت الترحيل.
    """
    value: Decimal
    source: str  # "converted" | "computed_base" — للتوثيق/التتبع فقط

    def as_decimal(self) -> Decimal:
        return self.value
