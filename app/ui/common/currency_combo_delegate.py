"""ComboBox حقيقي لاختيار عملة السطر — بدل كتابة نص حر عرضة للأخطاء الإملائية.
فارغ = يرث عملة القيد الافتراضية (لا نفرض عملة على المستخدم إذا ما احتاجها)."""

from __future__ import annotations
from PySide6.QtWidgets import QStyledItemDelegate, QComboBox
from PySide6.QtCore import Qt

CURRENCY_CHOICES = ["", "SYP", "USD", "TRY", "EUR"]


class CurrencyComboDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(CURRENCY_CHOICES)
        combo.setEditable(False)
        combo.setLayoutDirection(Qt.RightToLeft)
        return combo

    def setEditorData(self, editor, index):
        current = index.model().data(index, Qt.EditRole) or ""
        i = editor.findText(current)
        editor.setCurrentIndex(i if i >= 0 else 0)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)
