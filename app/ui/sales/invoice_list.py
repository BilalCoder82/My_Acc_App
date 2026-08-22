"""
Sales Invoice List — قائمة فواتير البيع
============================================
Table-first: بحث سريع + جدول + نقر مزدوج لفتح الفاتورة بتاب جديد.
لا منطق محاسبي هنا إطلاقاً — فقط عرض بيانات من app/services/.
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QTableWidget,
    QTableWidgetItem, QPushButton, QHeaderView, QAbstractItemView
)
from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from app.models import InvoiceKind
from app.services.invoice_queries import list_invoices

COLUMNS = ["رقم الفاتورة", "التاريخ", "العميل", "الحالة"]


class SalesInvoiceListView(QWidget):
    # (invoice_id أو None لفاتورة جديدة, عنوان التاب)
    invoice_opened = Signal(object, str)

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("بحث برقم الفاتورة أو اسم العميل...")
        self.search_box.textChanged.connect(self._reload)
        new_btn = QPushButton("+ فاتورة جديدة")
        new_btn.clicked.connect(self._open_new_invoice)
        toolbar.addWidget(self.search_box)
        toolbar.addStretch()
        toolbar.addWidget(new_btn)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.table)

        self._reload()

    def _reload(self) -> None:
        invoices = list_invoices(self.session, kind=InvoiceKind.SALES, search=self.search_box.text())
        self.table.setRowCount(len(invoices))
        self._row_invoice_ids = []
        status_labels = {"draft": "مسودة", "posted": "مرحّلة", "cancelled": "ملغاة"}
        for row, inv in enumerate(invoices):
            self._row_invoice_ids.append(inv.id)
            self.table.setItem(row, 0, QTableWidgetItem(inv.invoice_no))
            self.table.setItem(row, 1, QTableWidgetItem(str(inv.invoice_date)))
            self.table.setItem(row, 2, QTableWidgetItem(inv.party_name))
            self.table.setItem(row, 3, QTableWidgetItem(status_labels.get(inv.status.value, inv.status.value)))

    def _open_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        invoice_id = self._row_invoice_ids[row]
        invoice_no = self.table.item(row, 0).text()
        self.invoice_opened.emit(invoice_id, f"فاتورة {invoice_no}")

    def _open_new_invoice(self) -> None:
        self.invoice_opened.emit(None, "فاتورة بيع جديدة")
