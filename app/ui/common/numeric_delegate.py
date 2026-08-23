"""
Numeric Grid Delegate — تنسيق موحّد للخلايا الرقمية بكل شبكات الفواتير
==========================================================================
فاصلة الآلاف: تُطبَّق على "نص العرض" فقط (displayText) — القيمة الخام
المُخزَّنة بالخلية تبقى رقماً نظيفاً بدون فواصل، لأن كودنا الحسابي
(_build_transient_invoice) يحلّل هذا النص مباشرة كـDecimal. لو حاولنا
نص التنسيق أثناء الكتابة حرفاً بحرف، القيمة الخام تتلوث بفواصل ويصير
تحليلها عرضة للكسر. لذلك التنسيق يظهر فور الانتهاء من تحرير الخلية
(الخروج منها)، لا أثناء كل ضغطة مفتاح — هذا حل عملي وشائع بأنظمة مشابهة،
وليس قصوراً تقنياً.

المؤشر يبدأ من يمين الخلية عند التحرير (AlignRight)، بما يتوافق مع RTL.

فاصلة العشرية القابلة للتخصيص من الإعدادات — لم تُبنَ بعد، القيمة
الحالية ثابتة (نقطة عشرية قياسية)، موثّق كفجوة معروفة بـWORKFLOW.md.
"""

from __future__ import annotations
from decimal import Decimal, InvalidOperation
from PySide6.QtWidgets import QStyledItemDelegate, QLineEdit
from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator


CURRENCY_SYMBOLS = {"SYP": "ل.س", "USD": "$", "TRY": "₺", "EUR": "€"}


def format_number(value, decimals: int = 2) -> str:
    """فاصلة آلاف + عدد منازل عشرية ثابت — يُستخدم بكل مكان نعرض فيه رقماً."""
    try:
        d = Decimal(str(value)) if value not in (None, "") else Decimal("0")
    except (InvalidOperation, ValueError):
        return str(value)
    return f"{d:,.{decimals}f}"


def format_currency(value, currency_code: str = "SYP", decimals: int = 2) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency_code, currency_code)
    return f"{format_number(value, decimals)} {symbol}"


class NumericGridDelegate(QStyledItemDelegate):
    """يُستخدم على أعمدة الكمية/السعر/الحسم/الضريبة/الإجمالي بأي جدول فاتورة."""

    def __init__(self, decimals: int = 2, editable: bool = True, parent=None):
        super().__init__(parent)
        self.decimals = decimals
        self.editable = editable

    def createEditor(self, parent, option, index):
        if not self.editable:
            return None
        editor = QLineEdit(parent)
        editor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)  # المؤشر يبدأ من اليمين
        validator = QDoubleValidator(0, 10 ** 12, self.decimals, editor)
        validator.setNotation(QDoubleValidator.StandardNotation)
        editor.setValidator(validator)
        return editor

    def setEditorData(self, editor, index):
        # نعرض الرقم الخام بدون فواصل أثناء التحرير — أسهل للتعديل والحذف
        raw = index.model().data(index, Qt.EditRole) or ""
        editor.setText(str(raw).replace(",", ""))
        editor.selectAll()

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text(), Qt.EditRole)

    def displayText(self, value, locale) -> str:
        if value in (None, ""):
            return ""
        return format_number(value, self.decimals)
