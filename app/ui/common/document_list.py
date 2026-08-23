"""
Base Document List — الأساس المشترك لكل قوائم الفواتير والمرتجعات
=====================================================================
نفس نمط قائمة فواتير البيع (toolbar, search, table, badge حالة) —
مُستخرَج هنا لتفادي تكراره 4 مرات، بنفس منطق BaseDocumentFormView.
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QTableWidget,
    QTableWidgetItem, QPushButton, QHeaderView, QAbstractItemView, QLabel
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont
from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceKind
from app.services.invoice_queries import list_invoices

STATUS_STYLE = {
    "draft": ("مسودة", "#F59E0B", "#FFFBEB"),
    "posted": ("مرحّلة", "#16A34A", "#F0FDF4"),
    "cancelled": ("ملغاة", "#DC2626", "#FEF2F2"),
}


class BaseDocumentListView(QWidget):
    invoice_opened = Signal(object, str)

    def __init__(
        self, session: Session, kind: InvoiceKind, title: str, party_label: str,
        new_doc_title: str, show_original_ref: bool = False, parent=None,
    ):
        super().__init__(parent)
        self.session = session
        self.kind = kind
        self.new_doc_title = new_doc_title
        self.show_original_ref = show_original_ref
        self.setStyleSheet("background-color: #F5F7FA;")

        self.columns = ["رقم المستند", "التاريخ", party_label]
        if show_original_ref:
            self.columns.append("المستند الأصلي")
        self.columns.append("الحالة")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title_label = QLabel(title)
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #111827;")
        header.addWidget(title_label)
        header.addStretch()
        layout.addLayout(header)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(f"🔍  بحث برقم المستند أو اسم {party_label}...")
        self.search_box.setStyleSheet(
            "padding: 8px 12px; border: 1px solid #D1D5DB; border-radius: 6px; "
            "background: white; font-size: 12px;"
        )
        self.search_box.setFixedHeight(36)
        self.search_box.textChanged.connect(self._reload)

        new_btn = QPushButton(f"+  {new_doc_title}")
        new_btn.setStyleSheet(
            "background-color: #2563EB; color: white; font-weight: bold; "
            "padding: 8px 18px; border-radius: 6px; font-size: 12px;"
        )
        new_btn.setFixedHeight(36)
        new_btn.clicked.connect(self._open_new)

        toolbar.addWidget(self.search_box, stretch=1)
        toolbar.addWidget(new_btn)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(self.columns))
        self.table.setHorizontalHeaderLabels(self.columns)
        last_col = len(self.columns) - 1
        for col in range(len(self.columns)):
            mode = QHeaderView.Stretch if col == 2 else QHeaderView.Fixed
            self.table.horizontalHeader().setSectionResizeMode(col, mode)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 110)
        if show_original_ref:
            self.table.setColumnWidth(3, 140)
        self.table.setColumnWidth(last_col, 100)
        self.table.horizontalHeader().setFixedHeight(38)
        self.table.horizontalHeader().setStyleSheet(
            "QHeaderView::section { background: #EEF2FF; padding: 8px; "
            "border: none; font-weight: bold; font-size: 12px; color: #374151; }"
        )
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setStyleSheet(
            "QTableWidget { background: white; border: 1px solid #E5E7EB; "
            "border-radius: 6px; gridline-color: #F3F4F6; }"
            "QTableWidget::item { padding: 6px; border-bottom: 1px solid #F3F4F6; }"
            "QTableWidget::item:selected { background: #DBEAFE; color: #1E40AF; }"
        )
        self.table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.table)

        self._reload()

    def _reload(self) -> None:
        invoices = list_invoices(self.session, kind=self.kind, search=self.search_box.text())
        self.table.setRowCount(len(invoices))
        self._row_invoice_ids = []
        status_col = len(self.columns) - 1
        for row, inv in enumerate(invoices):
            self._row_invoice_ids.append(inv.id)
            self.table.setItem(row, 0, QTableWidgetItem(inv.invoice_no))
            self.table.setItem(row, 1, QTableWidgetItem(str(inv.invoice_date)))
            self.table.setItem(row, 2, QTableWidgetItem(inv.party_name))
            if self.show_original_ref:
                original = self.session.get(Invoice, inv.original_invoice_id) if inv.original_invoice_id else None
                self.table.setItem(row, 3, QTableWidgetItem(original.invoice_no if original else "— مرتجع حر —"))

            status_key = inv.status.value
            label, color, bg = STATUS_STYLE.get(status_key, (status_key, "#6B7280", "#F3F4F6"))
            badge = QLabel(label)
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(
                f"background-color: {bg}; color: {color}; padding: 3px 10px; "
                "border-radius: 10px; font-weight: bold; font-size: 11px;"
            )
            badge_container = QWidget()
            badge_layout = QHBoxLayout(badge_container)
            badge_layout.setContentsMargins(4, 2, 4, 2)
            badge_layout.addWidget(badge)
            self.table.setItem(row, status_col, QTableWidgetItem(""))
            self.table.setCellWidget(row, status_col, badge_container)

        self.table.resizeRowsToContents()

    def _open_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        invoice_id = self._row_invoice_ids[row]
        invoice_no = self.table.item(row, 0).text()
        self.invoice_opened.emit(invoice_id, f"{self.new_doc_title.replace('جديدة', '').replace('جديد', '').strip()} {invoice_no}")

    def _open_new(self) -> None:
        self.invoice_opened.emit(None, self.new_doc_title)
