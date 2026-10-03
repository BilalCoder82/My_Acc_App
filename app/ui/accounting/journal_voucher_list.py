"""Journal Voucher List — قائمة سندات القيد اليدوية"""
from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QTableWidget,
    QTableWidgetItem, QPushButton, QHeaderView, QAbstractItemView, QLabel
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont
from sqlalchemy.orm import Session

from app.models import JournalEntry

STATUS_STYLE = {
    "draft": ("مسودة", "#F59E0B", "#FFFBEB"),
    "posted": ("مرحّلة", "#16A34A", "#F0FDF4"),
    "cancelled": ("ملغاة", "#DC2626", "#FEF2F2"),
}


class JournalVoucherListView(QWidget):
    entry_opened = Signal(object, str)

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setStyleSheet("background-color: #F5F7FA;")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("سندات القيد")
        f = QFont(); f.setPointSize(16); f.setBold(True)
        title.setFont(f)
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        toolbar = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍  بحث برقم القيد أو البيان...")
        self.search_box.setStyleSheet(
            "padding: 8px 12px; border: 1px solid #D1D5DB; border-radius: 6px; background: white;"
        )
        self.search_box.setFixedHeight(36)
        self.search_box.textChanged.connect(self._reload)
        new_btn = QPushButton("+  سند قيد جديد")
        new_btn.setStyleSheet(
            "background-color: #2563EB; color: white; font-weight: bold; "
            "padding: 8px 18px; border-radius: 6px;"
        )
        new_btn.setFixedHeight(36)
        new_btn.clicked.connect(lambda: self.entry_opened.emit(None, "سند قيد جديد"))
        toolbar.addWidget(self.search_box, stretch=1)
        toolbar.addWidget(new_btn)
        layout.addLayout(toolbar)

        self.columns = ["رقم القيد", "التاريخ", "البيان", "مدين", "دائن", "الحالة"]
        self.table = QTableWidget(0, len(self.columns))
        self.table.setHorizontalHeaderLabels(self.columns)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setFixedHeight(38)
        self.table.horizontalHeader().setStyleSheet(
            "QHeaderView::section { background: #EEF2FF; padding: 8px; border: none; font-weight: bold; }"
        )
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setStyleSheet(
            "QTableWidget { background: white; border: 1px solid #E5E7EB; border-radius: 6px; }"
        )
        self.table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.table)

        self._reload()

    def _reload(self) -> None:
        query = self.session.query(JournalEntry).filter(JournalEntry.source_type == "manual")
        search = self.search_box.text().strip()
        if search:
            query = query.filter(
                (JournalEntry.ref_no.ilike(f"%{search}%")) | (JournalEntry.description.ilike(f"%{search}%"))
            )
        entries = query.order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()).all()
        self.table.setRowCount(len(entries))
        self._row_ids = []
        for row, e in enumerate(entries):
            self._row_ids.append(e.id)
            total_debit = sum(float(l.debit) for l in e.lines)
            total_credit = sum(float(l.credit) for l in e.lines)
            self.table.setItem(row, 0, QTableWidgetItem(e.ref_no))
            self.table.setItem(row, 1, QTableWidgetItem(str(e.entry_date)))
            self.table.setItem(row, 2, QTableWidgetItem(e.description or ""))
            self.table.setItem(row, 3, QTableWidgetItem(f"{total_debit:,.2f}"))
            self.table.setItem(row, 4, QTableWidgetItem(f"{total_credit:,.2f}"))

            label, color, bg = STATUS_STYLE.get(e.status.value, (e.status.value, "#6B7280", "#F3F4F6"))
            badge = QLabel(label)
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(
                f"background-color: {bg}; color: {color}; padding: 3px 10px; "
                "border-radius: 10px; font-weight: bold; font-size: 11px;"
            )
            container = QWidget()
            l = QHBoxLayout(container)
            l.setContentsMargins(4, 2, 4, 2)
            l.addWidget(badge)
            self.table.setItem(row, 5, QTableWidgetItem(""))
            self.table.setCellWidget(row, 5, container)

    def _open_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        entry_id = self._row_ids[row]
        ref_no = self.table.item(row, 0).text()
        self.entry_opened.emit(entry_id, f"سند قيد {ref_no}")
