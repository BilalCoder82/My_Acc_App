"""
كشف حساب — نافذة منبثقة بسيطة فوق get_account_statement (app/reports/ledger.py).
لا منطق محاسبي هنا إطلاقاً؛ فقط عرض. حساب تجميعي لا حركات مباشرة له
(get_account_statement يرفض بوضوح)، فنعرض رسالة بدل جدول فارغ مضلِّل.
"""

from __future__ import annotations
from datetime import date
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QDateEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QFont
from sqlalchemy.orm import Session

from app.models import Account
from app.reports.ledger import get_account_statement

COLOR_BG = "#F5F7FA"


class AccountStatementDialog(QDialog):
    def __init__(self, session: Session, account: Account, parent=None):
        super().__init__(parent)
        self.session = session
        self.account = account

        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle(f"كشف حساب: {account.name_ar}")
        self.resize(720, 520)
        self.setStyleSheet(f"background-color: {COLOR_BG};")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 16, 18, 16)

        title = QLabel(f"كشف حساب: {account.code} — {account.name_ar}")
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("من تاريخ"))
        self.from_date = QDateEdit(calendarPopup=True)
        self.from_date.setDate(QDate.currentDate().addMonths(-1))
        self.from_date.setStyleSheet("padding: 5px; border: 1px solid #D1D5DB; border-radius: 5px; background: white;")
        filters.addWidget(self.from_date)
        filters.addWidget(QLabel("إلى تاريخ"))
        self.to_date = QDateEdit(calendarPopup=True)
        self.to_date.setDate(QDate.currentDate())
        self.to_date.setStyleSheet("padding: 5px; border: 1px solid #D1D5DB; border-radius: 5px; background: white;")
        filters.addWidget(self.to_date)
        refresh_btn = QPushButton("تحديث")
        refresh_btn.setStyleSheet("padding: 6px 16px; border-radius: 5px; background: #2563EB; color: white;")
        refresh_btn.clicked.connect(self._reload)
        filters.addWidget(refresh_btn)
        filters.addStretch()
        layout.addLayout(filters)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: #374151;")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["التاريخ", "رقم القيد", "البيان", "مدين", "دائن", "الرصيد"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setStyleSheet(
            "QTableWidget { background: white; border: 1px solid #E5E7EB; }"
            "QHeaderView::section { background: #EEF2FF; padding: 6px; border: none; font-weight: bold; }"
        )
        layout.addWidget(self.table)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("إغلاق")
        close_btn.setStyleSheet("padding: 8px 20px; border-radius: 5px; border: 1px solid #D1D5DB; background: white;")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self._reload()

    def _reload(self) -> None:
        date_from: date = self.from_date.date().toPython()
        date_to: date = self.to_date.date().toPython()

        if self.account.is_group:
            self.table.setRowCount(0)
            self.summary_label.setText(
                "هذا حساب تجميعي — لا حركات مباشرة عليه. رصيده هو مجموع أرصدة حساباته الفرعية."
            )
            return

        try:
            statement = get_account_statement(self.session, self.account.id, date_from, date_to)
        except ValueError as e:
            QMessageBox.warning(self, "تعذّر عرض الكشف", str(e))
            return

        self.summary_label.setText(
            f"الرصيد الافتتاحي: {statement.opening_balance:,.2f}   |   "
            f"الرصيد الختامي: {statement.closing_balance:,.2f} {self.account.currency_code}"
        )

        self.table.setRowCount(len(statement.rows))
        for i, row in enumerate(statement.rows):
            self.table.setItem(i, 0, QTableWidgetItem(row.entry_date.strftime("%Y-%m-%d")))
            self.table.setItem(i, 1, QTableWidgetItem(row.ref_no or ""))
            self.table.setItem(i, 2, QTableWidgetItem(row.description or ""))
            self.table.setItem(i, 3, QTableWidgetItem(f"{row.debit:,.2f}" if row.debit else ""))
            self.table.setItem(i, 4, QTableWidgetItem(f"{row.credit:,.2f}" if row.credit else ""))
            self.table.setItem(i, 5, QTableWidgetItem(f"{row.running_balance:,.2f}"))
