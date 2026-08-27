"""
دليل المواد — الشاشة الرئيسية. جدول لا شجرة (لا تسلسل هرمي حقيقي بالمواد —
راجع WORKFLOW.md §25 لسبب القرار). نفس تفاعلات دليل الحسابات: زر "+ مادة"،
Double-click/Enter لفتح البطاقة (itemActivated تغطي كليهما تلقائياً)، بحث فوري.
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QLineEdit, QComboBox, QLabel, QHeaderView, QPushButton, QAbstractItemView
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from sqlalchemy.orm import Session

from app.models import Item
from app.services.item_queries import list_all_items, get_item_stock_summary
from app.ui.inventory.item_card_dialog import ItemCardDialog

COLOR_BG = "#F5F7FA"


class ItemListView(QWidget):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet(f"background-color: {COLOR_BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("دليل المواد")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setStyleSheet("color: #111827;")
        title.setFont(title_font)
        header.addWidget(title)
        header.addStretch()
        add_btn = QPushButton("+ مادة")
        add_btn.setStyleSheet(
            "background: #2563EB; color: white; padding: 8px 18px; border-radius: 6px; font-weight: bold;"
        )
        add_btn.clicked.connect(self._add_item)
        header.addWidget(add_btn)
        layout.addLayout(header)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("بحث بالكود أو الاسم...")
        self.search_edit.setStyleSheet(
            "padding: 7px 10px; border: 1px solid #D1D5DB; border-radius: 6px; background: white;"
        )
        self.search_edit.textChanged.connect(self._apply_filters)
        filters.addWidget(self.search_edit, stretch=3)

        self.category_combo = QComboBox()
        self.category_combo.setLayoutDirection(Qt.RightToLeft)
        self.category_combo.setStyleSheet(
            "padding: 7px 10px; border: 1px solid #D1D5DB; border-radius: 6px; background: white;"
        )
        self.category_combo.currentTextChanged.connect(self._apply_filters)
        filters.addWidget(self.category_combo, stretch=1)
        layout.addLayout(filters)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["الكود", "الاسم", "الوحدة", "الرصيد الحالي", "نشطة؟"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setStyleSheet(
            "QTableWidget { background: white; border: 1px solid #E5E7EB; }"
            "QHeaderView::section { background: #EEF2FF; padding: 6px; border: none; font-weight: bold; }"
        )
        self.table.itemActivated.connect(self._open_item_card)
        layout.addWidget(self.table)

        self._reload()

    # -- تحميل البيانات ---------------------------------------------------------
    def _reload(self) -> None:
        self._items = list_all_items(self.session)

        current_category = self.category_combo.currentText()
        categories = sorted({i.category for i in self._items if i.category})
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("كل التصنيفات")
        self.category_combo.addItems(categories)
        idx = self.category_combo.findText(current_category)
        self.category_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.category_combo.blockSignals(False)

        self._apply_filters()

    def _apply_filters(self) -> None:
        search = self.search_edit.text().strip().lower()
        category = self.category_combo.currentText()

        rows = [
            i for i in self._items
            if (not search or search in i.sku.lower() or search in i.name_ar.lower())
            and (category in ("", "كل التصنيفات") or i.category == category)
        ]

        self.table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            summary = get_item_stock_summary(self.session, item.id)
            code_item = QTableWidgetItem(item.sku)
            code_item.setData(Qt.UserRole, item.id)
            self.table.setItem(row, 0, code_item)
            self.table.setItem(row, 1, QTableWidgetItem(item.name_ar))
            self.table.setItem(row, 2, QTableWidgetItem(item.unit))
            qty_item = QTableWidgetItem(f"{summary.quantity:,.3f}")
            qty_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 3, qty_item)
            self.table.setItem(row, 4, QTableWidgetItem("✓" if item.is_active else "✗"))
            if not item.is_active:
                for col in range(5):
                    self.table.item(row, col).setForeground(Qt.GlobalColor.gray)

    # -- بطاقة المادة ----------------------------------------------------------
    def _item_from_row(self, row: int) -> Item | None:
        item_id = self.table.item(row, 0).data(Qt.UserRole)
        return self.session.get(Item, item_id) if item_id else None

    def _open_item_card(self, table_item: QTableWidgetItem, _column: int = 0) -> None:
        item = self._item_from_row(table_item.row())
        if item is None:
            return
        dlg = ItemCardDialog(self.session, item=item, parent=self)
        if dlg.exec():
            self._reload()

    def _add_item(self) -> None:
        dlg = ItemCardDialog(self.session, item=None, parent=self)
        if dlg.exec():
            self._reload()
