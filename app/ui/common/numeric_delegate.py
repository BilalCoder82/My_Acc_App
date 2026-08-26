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

-- مسار Enter الموحّد (on_return) -----------------------------------------
كل خلية قابلة للتحرير بشبكة سند القيد تمرّ Enter بها عبر نفس المسار:
`_ReturnKeyEditingMixin.eventFilter`. السبب أن QAbstractItemDelegate
الافتراضي عنده معالجة Enter/Return خاصة به (يستدعي commitData/closeEditor
بنفسه بـhint = EditNextItem، وهذا يحرّك المؤشر حسب ترتيب tab القياسي
لكيوت — يتجاهل تماماً منطقنا الخاص بتخطي "الحساب" غير القابل للتحرير
وإضافة سطر جديد تلقائياً). لو تركنا هذا السلوك الافتراضي، ستكون تجربة
Enter مختلفة حسب كون الخلية "قيد التحرير الفعلي" (يمرّ عبر مسار Qt
الافتراضي) أو "محددة فقط بدون تحرير" (يمرّ عبر eventFilter المثبّت على
الشبكة بـjournal_voucher_form.py) — وهذا بالضبط سبب تحذير الطرفية
`QAbstractItemView::commitData called with an editor that does not
belong to this view`: مسارا Enter يتنافسان أحياناً على نفس المحرّر.

الحل: نعترض Enter/Return بمستوى المحرّر نفسه (قبل أن يصله المعالج
الافتراضي لـQAbstractItemDelegate)، ونُنفّذ نحن commitData ثم closeEditor
بأنفسنا (بنفس الترتيب الذي يتّبعه كيوت داخلياً)، ثم نستدعي `on_return`
— دالة التنقل الموحّدة بالنموذج (`_move_to_next_cell`) — ونُرجع True
لمنع أي معالجة إضافية مزدوجة لنفس الضغطة.
"""

from __future__ import annotations
from decimal import Decimal, InvalidOperation
from PySide6.QtWidgets import QStyledItemDelegate, QLineEdit, QAbstractItemDelegate
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QDoubleValidator


class _ReturnKeyEditingMixin:
    """يُخلَط مع أي QStyledItemDelegate يحتاج مسار Enter موحّداً — راجع شرح
    "مسار Enter الموحّد" بأعلى الملف. الصنف المضيف يجب أن يضبط `self.on_return`
    (دالة بدون معاملات، أو None لتعطيل الاعتراض والعودة للسلوك الافتراضي)."""

    def _install_return_filter(self, editor) -> None:
        if getattr(self, "on_return", None) is not None:
            editor.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (اسم Qt القياسي)
        if (
            getattr(self, "on_return", None) is not None
            and event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Return, Qt.Key_Enter)
        ):
            self.commitData.emit(obj)
            self.closeEditor.emit(obj, QAbstractItemDelegate.NoHint)
            self.on_return()
            return True
        return super().eventFilter(obj, event)


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


class NumericGridDelegate(_ReturnKeyEditingMixin, QStyledItemDelegate):
    """يُستخدم على أعمدة الكمية/السعر/الحسم/الضريبة/الإجمالي بأي جدول فاتورة.

    on_return: دالة تنقل اختيارية (بدون معاملات) تُستدعى عند Enter/Return
    أثناء التحرير الفعلي — راجع `_ReturnKeyEditingMixin` بأعلى الملف."""

    def __init__(self, decimals: int = 2, editable: bool = True, parent=None, on_return=None):
        super().__init__(parent)
        self.decimals = decimals
        self.editable = editable
        self.on_return = on_return

    def createEditor(self, parent, option, index):
        if not self.editable:
            return None
        editor = QLineEdit(parent)
        editor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)  # المؤشر يبدأ من اليمين
        validator = QDoubleValidator(0, 10 ** 12, self.decimals, editor)
        validator.setNotation(QDoubleValidator.StandardNotation)
        editor.setValidator(validator)
        self._install_return_filter(editor)
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


class PlainTextGridDelegate(_ReturnKeyEditingMixin, QStyledItemDelegate):
    """محرّر نصّي بسيط (رمز الحساب / البيان) — نفس الغرض من NumericGridDelegate
    لكن بدون validator رقمي أو تنسيق فاصلة آلاف. يُستخدم فقط ليمرّ Enter عبر
    نفس مسار التنقل الموحّد (on_return) بدل المسار الافتراضي لـQt، بما أن
    هذين العمودين كانا سابقاً بدون delegate مخصَّص إطلاقاً."""

    def __init__(self, parent=None, on_return=None):
        super().__init__(parent)
        self.on_return = on_return

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        self._install_return_filter(editor)
        return editor
