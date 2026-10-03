"""ComboBox حقيقي لاختيار عملة السطر — بدل كتابة نص حر عرضة للأخطاء الإملائية.
فارغ = يرث عملة القيد الافتراضية (لا نفرض عملة على المستخدم إذا ما احتاجها).

منذ إضافة تعبئة العملة الافتراضية تلقائياً بكل سطر جديد
(journal_voucher_form._add_empty_row)، الخيار الفارغ نادراً ما يظهر عملياً
— لكنه يبقى متاحاً لو أراد المحاسب صراحة إفراغ عملة سطر بعينه."""

from __future__ import annotations
from PySide6.QtWidgets import QStyledItemDelegate, QComboBox
from PySide6.QtCore import Qt

from app.ui.common.numeric_delegate import _ReturnKeyEditingMixin

CURRENCY_CHOICES = ["", "SYP", "USD", "TRY", "EUR"]


class CurrencyComboDelegate(_ReturnKeyEditingMixin, QStyledItemDelegate):
    """on_return: راجع نفس الشرح بـnumeric_delegate.py — يوحّد مسار Enter
    بين كل خلايا الشبكة القابلة للتحرير بما فيها هذا الـComboBox."""

    def __init__(self, parent=None, on_return=None):
        super().__init__(parent)
        self.on_return = on_return

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(CURRENCY_CHOICES)
        combo.setEditable(False)
        combo.setLayoutDirection(Qt.RightToLeft)
        self._install_return_filter(combo)
        return combo

    def setEditorData(self, editor, index):
        current = index.model().data(index, Qt.EditRole) or ""
        i = editor.findText(current)
        editor.setCurrentIndex(i if i >= 0 else 0)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)
