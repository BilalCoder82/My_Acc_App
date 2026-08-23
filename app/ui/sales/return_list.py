"""
Sales Return List — قائمة مرتجعات البيع
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QTableWidget,
    QTableWidgetItem, QPushButton, QHeaderView, QAbstractItemView, QLabel
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont
from sqlalchemy.orm import Session

from app.models import InvoiceKind
from app.services.invoice_queries import list_invoices

COLUMNS = ["رقم المرتجع", "التاريخ", "العميل", "الحالة"]

STATUS_STYLE = {
    "draft": ("مسودة", "#F59E0B", "#FFFBEB"),
    "posted": ("مرحّلة", "#16A34A", "#F0FDF4"),
    "cancelled": ("ملغاة", "#DC2626", "#FEF2F2"),
}


class SalesReturnListView(QWidget):
    invoice_opened = Signal(object, str)

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setStyleSheet("background-color: #F5F7FA;")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("مرتجعات البيع")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #111827;")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍  بحث برقم المرتجع أو اسم العميل...")
        self.search_box.setStyleSheet(
            "padding: 8px 12px; border: 1px solid #D1D5DB; border-radius: 6px; "
            "background: white; font-size: 12px;"
        )
        self.search_box.setFixedHeight(36)
        self.search_box.textChanged.connect(self._reload)

        new_btn = QPushButton("+  مرتجع جديد")
        new_btn.setStyleSheet(
            "background-color: #2563EB; color: white; font-weight: bold; "
            "padding: 8px 18px; border-radius: 6px; font-size: 12px;"
        )
        new_btn.setFixedHeight(36)
        new_btn.clicked.connect(self._open_new_invoice)

        toolbar.addWidget(self.search_box, stretch=1)
        toolbar.addWidget(new_btn)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(3, 100)
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
        invoices = list_invoices(self.session, kind=InvoiceKind.SALES_RETURN, search=self.search_box.text())
        self.table.setRowCount(len(invoices))
        self._row_invoice_ids = []
        for row, inv in enumerate(invoices):
            self._row_invoice_ids.append(inv.id)
            self.table.setItem(row, 0, QTableWidgetItem(inv.invoice_no))
            self.table.setItem(row, 1, QTableWidgetItem(str(inv.invoice_date)))
            self.table.setItem(row, 2, QTableWidgetItem(inv.party_name))

            status_key = inv.status.value
            label, color, bg = STATUS_STYLE.get(status_key, (status_key, "#6B7280", "#F3F4F6"))
            status_item = QTableWidgetItem(label)
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setText(
                f'<span style="background-color:{bg}; color:{color}; '
                f'padding:3px 10px; border-radius:10px; font-weight:bold; '
                f'font-size:11px;">{label}</span>'
            )
            self.table.setItem(row, 3, status_item)
        self.table.resizeRowsToContents()

    def _open_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        invoice_id = self._row_invoice_ids[row]
        invoice_no = self.table.item(row, 0).text()
        self.invoice_opened.emit(invoice_id, f"مرتجع {invoice_no}")

    def _open_new_invoice(self) -> None:
        self.invoice_opened.emit(None, "مرتجع بيع جديد")