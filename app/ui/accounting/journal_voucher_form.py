"""
Journal Voucher Form — سند القيد اليدوي
============================================
نفس فلسفة فاتورة البيع (BaseDocumentFormView) لكن مُخصَّص للقيد المحاسبي:
عمودان (مدين/دائن) بدل عمود إجمالي واحد، بحث عن حساب بدل مادة، وفرق
لحظي (مدين - دائن) يتحول لونه أخضر/أحمر حسب التوازن — لكن هذا المؤشر
عرضي فقط، الحماية الفعلية دائماً من journal_edit.py وليس من هنا.
"""

from __future__ import annotations
from decimal import Decimal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QDateEdit,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QHeaderView,
    QMessageBox, QAbstractItemView, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QFont, QShortcut, QKeySequence
from sqlalchemy.orm import Session

from app.models import JournalEntry, JournalEntryStatus
from app.services.journal_edit import add_manual_line, post_manual_entry, JournalEditError
from app.services.account_queries import list_postable_accounts
from app.ui.common.numeric_delegate import NumericGridDelegate, format_currency

COLUMNS = ["رمز الحساب", "الحساب", "البيان", "مدين", "دائن"]
COL_CODE, COL_ACCOUNT, COL_DESC, COL_DEBIT, COL_CREDIT = range(5)

COLOR_PRIMARY = "#2563EB"
COLOR_BG = "#F5F7FA"
CARD_STYLE = "QFrame { background: white; border-radius: 6px; padding: 8px; border: 1px solid #E5E7EB; }"

STATUS_STYLE = {
    JournalEntryStatus.DRAFT: ("مسودة", "#F59E0B"),
    JournalEntryStatus.POSTED: ("مرحّلة", "#16A34A"),
    JournalEntryStatus.CANCELLED: ("ملغاة", "#DC2626"),
}


class JournalVoucherFormView(QWidget):
    def __init__(self, session: Session, entry_id: int | None = None, parent=None):
        super().__init__(parent)
        self.session = session
        self.entry: JournalEntry | None = session.get(JournalEntry, entry_id) if entry_id else None
        self._accounts_cache = list_postable_accounts(session)

        self.setStyleSheet(f"background-color: {COLOR_BG};")
        self._build_ui()
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save_draft)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._post)

        if self.entry:
            self._load_entry()
        else:
            for _ in range(8):
                self._add_empty_row()

    # -- بناء الواجهة -----------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_info())
        layout.addWidget(self._build_grid(), stretch=1)
        layout.addLayout(self._build_totals_and_actions())
        self._refresh_editability()

    def _build_header(self) -> QWidget:
        card = QFrame()
        card.setStyleSheet(CARD_STYLE)
        row = QHBoxLayout(card)
        title = QLabel("سند قيد محاسبي")
        f = QFont(); f.setPointSize(16); f.setBold(True)
        title.setFont(f)
        title.setStyleSheet("color: #111827;")
        self.ref_label = QLabel("جديد (غير محفوظ)")
        self.ref_label.setStyleSheet("color: #6B7280; font-size: 12px;")
        self.status_badge = QLabel()
        self.status_badge.setStyleSheet(
            "border-radius: 10px; padding: 3px 12px; color: white; font-weight: bold; font-size: 12px;"
        )
        row.addWidget(title)
        row.addWidget(self.ref_label)
        row.addWidget(self.status_badge)
        row.addStretch()
        return card

    def _build_info(self) -> QWidget:
        card = QFrame()
        card.setStyleSheet(CARD_STYLE)
        grid = QGridLayout(card)
        grid.setHorizontalSpacing(16)

        def labeled(text, widget, width=None):
            if width:
                widget.setFixedWidth(width)
            box = QVBoxLayout()
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #6B7280; font-size: 11px;")
            box.addWidget(lbl)
            box.addWidget(widget)
            container = QWidget()
            container.setLayout(box)
            return container

        self.ref_edit = QLineEdit()
        self.ref_edit.setReadOnly(True)
        self.ref_edit.setPlaceholderText("يُولَّد عند الترحيل")
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText("بيان القيد العام...")
        self.description_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        grid.addWidget(labeled("رقم القيد", self.ref_edit, 160), 0, 0)
        grid.addWidget(labeled("التاريخ", self.date_edit, 140), 0, 1)
        grid.addWidget(labeled("البيان", self.description_edit), 0, 2)
        return card

    def _build_grid(self) -> QWidget:
        self.grid = QTableWidget(0, len(COLUMNS))
        self.grid.setHorizontalHeaderLabels(COLUMNS)
        self.grid.horizontalHeader().setSectionResizeMode(COL_ACCOUNT, QHeaderView.Stretch)
        for col in [COL_CODE, COL_DESC, COL_DEBIT, COL_CREDIT]:
            self.grid.horizontalHeader().setSectionResizeMode(col, QHeaderView.Fixed)
        self.grid.setColumnWidth(COL_CODE, 90)
        self.grid.setColumnWidth(COL_DESC, 220)
        self.grid.setColumnWidth(COL_DEBIT, 130)
        self.grid.setColumnWidth(COL_CREDIT, 130)
        self.grid.horizontalHeader().setFixedHeight(36)
        self.grid.verticalHeader().setDefaultSectionSize(34)
        self.grid.verticalHeader().hide()
        self.grid.setLayoutDirection(Qt.RightToLeft)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectItems)

        for col in [COL_DEBIT, COL_CREDIT]:
            self.grid.setItemDelegateForColumn(col, NumericGridDelegate(2, editable=True, parent=self.grid))

        self.grid.setStyleSheet(
            "QTableWidget { background: white; border: 1px solid #E5E7EB; }"
            "QHeaderView::section { background: #EEF2FF; padding: 6px; border: none; font-weight: bold; font-size: 12px; }"
            "QTableWidget::item { border-bottom: 1px solid #F3F4F6; padding: 4px; }"
        )
        self.grid.itemChanged.connect(self._on_cell_changed)
        self.grid.installEventFilter(self)
        return self.grid

    def _build_totals_and_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)

        actions = QVBoxLayout()
        post_btn = QPushButton("ترحيل")
        post_btn.setStyleSheet(
            f"background-color: {COLOR_PRIMARY}; color: white; font-weight: bold; "
            "padding: 10px 24px; border-radius: 4px; font-size: 13px;"
        )
        post_btn.clicked.connect(self._post)
        save_btn = QPushButton("حفظ مسودة")
        save_btn.setStyleSheet("padding: 8px 20px; border: 1px solid #D1D5DB; border-radius: 4px; background: white;")
        save_btn.clicked.connect(self._save_draft)
        actions.addWidget(post_btn)
        actions.addWidget(save_btn)
        actions.addStretch()

        totals_card = QFrame()
        totals_card.setStyleSheet(CARD_STYLE)
        totals_card.setFixedWidth(300)
        t = QVBoxLayout(totals_card)

        self.debit_total_label = self._totals_row(t, "إجمالي المدين", "0.00")
        self.credit_total_label = self._totals_row(t, "إجمالي الدائن", "0.00")

        line = QFrame(); line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background: #E5E7EB;"); line.setFixedHeight(1)
        t.addWidget(line)

        diff_row = QHBoxLayout()
        diff_title = QLabel("الفرق")
        diff_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.diff_label = QLabel("0.00 ✓")
        self.diff_label.setStyleSheet(
            "font-weight: bold; font-size: 16px; color: #16A34A; "
            "background-color: #F0FDF4; padding: 6px 14px; border-radius: 6px;"
        )
        diff_row.addWidget(diff_title)
        diff_row.addStretch()
        diff_row.addWidget(self.diff_label)
        t.addLayout(diff_row)

        row.addLayout(actions)
        row.addStretch()
        row.addWidget(totals_card)
        return row

    @staticmethod
    def _totals_row(layout, title, value) -> QLabel:
        r = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setStyleSheet("color: #4B5563; font-size: 12px;")
        r.addWidget(lbl)
        r.addStretch()
        v = QLabel(value)
        v.setStyleSheet("font-size: 13px; font-weight: 500;")
        r.addWidget(v)
        layout.addLayout(r)
        return v

    # -- تحميل قيد موجود -----------------------------------------------------
    def _load_entry(self) -> None:
        e = self.entry
        self.ref_label.setText(e.ref_no)
        self.ref_edit.setText(e.ref_no)
        self.date_edit.setDate(QDate(e.entry_date.year, e.entry_date.month, e.entry_date.day))
        self.description_edit.setText(e.description or "")

        self.grid.blockSignals(True)
        self._close_current_editor()
        self.grid.setRowCount(0)
        for line in e.lines:
            row = self.grid.rowCount()
            self.grid.insertRow(row)
            self.grid.setItem(row, COL_CODE, QTableWidgetItem(line.account.code))
            self.grid.setItem(row, COL_ACCOUNT, QTableWidgetItem(line.account.name_ar))
            self.grid.setItem(row, COL_DESC, QTableWidgetItem("") )
            self.grid.setItem(row, COL_DEBIT, QTableWidgetItem(str(line.debit) if line.debit else ""))
            self.grid.setItem(row, COL_CREDIT, QTableWidgetItem(str(line.credit) if line.credit else ""))
        for _ in range(max(1, 8 - len(e.lines))):
            self._add_empty_row()
        self.grid.blockSignals(False)

        self._recalculate_totals()
        self._refresh_editability()

    def _refresh_editability(self) -> None:
        status = self.entry.status if self.entry else JournalEntryStatus.DRAFT
        text, color = STATUS_STYLE.get(status, ("مسودة", "#F59E0B"))
        if self.entry is None:
            text = "جديد"
        self.status_badge.setText(text)
        self.status_badge.setStyleSheet(
            f"background-color: {color}; border-radius: 10px; padding: 3px 12px; "
            "color: white; font-weight: bold; font-size: 12px;"
        )
        is_posted = self.entry is not None and self.entry.status == JournalEntryStatus.POSTED
        self.date_edit.setEnabled(not is_posted)
        self.description_edit.setEnabled(not is_posted)
        self.grid.setEditTriggers(
            QAbstractItemView.NoEditTriggers if is_posted else QAbstractItemView.AllEditTriggers
        )

    def _close_current_editor(self) -> None:
        current = self.grid.currentItem()
        if current:
            self.grid.closePersistentEditor(current)

    # -- الشبكة --------------------------------------------------------------
    def _add_empty_row(self) -> None:
        self._close_current_editor()
        row = self.grid.rowCount()
        self.grid.insertRow(row)
        for col in range(len(COLUMNS)):
            self.grid.setItem(row, col, QTableWidgetItem(""))

    def _row_has_data(self, row: int) -> bool:
        code_item = self.grid.item(row, COL_CODE)
        return bool(code_item and code_item.text().strip())

    def _on_cell_changed(self, changed_item: QTableWidgetItem) -> None:
        row = changed_item.row()
        if changed_item.column() == COL_CODE:
            code = changed_item.text().strip()
            match = next((a for a in self._accounts_cache if a.code == code), None)
            if match:
                self.grid.blockSignals(True)
                self.grid.setItem(row, COL_ACCOUNT, QTableWidgetItem(match.name_ar))
                self.grid.blockSignals(False)
        elif changed_item.column() == COL_DEBIT and changed_item.text().strip():
            self.grid.blockSignals(True)
            self.grid.setItem(row, COL_CREDIT, QTableWidgetItem(""))
            self.grid.blockSignals(False)
        elif changed_item.column() == COL_CREDIT and changed_item.text().strip():
            self.grid.blockSignals(True)
            self.grid.setItem(row, COL_DEBIT, QTableWidgetItem(""))
            self.grid.blockSignals(False)

        if row == self.grid.rowCount() - 1 and self._row_has_data(row):
            self._close_current_editor()
            self._add_empty_row()

        self._recalculate_totals()

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        if obj is self.grid and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Insert:
                self._add_empty_row(); return True
            if key == Qt.Key_Delete:
                self._remove_current_row(); return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._move_to_next_cell(); return True
        return super().eventFilter(obj, event)

    def _move_to_next_cell(self) -> None:
        row, col = self.grid.currentRow(), self.grid.currentColumn()
        skip = {COL_ACCOUNT}
        next_col = col + 1
        while next_col in skip and next_col < len(COLUMNS):
            next_col += 1
        if next_col < len(COLUMNS):
            self.grid.setCurrentCell(row, next_col)
        else:
            next_row = row + 1
            if next_row >= self.grid.rowCount():
                self._add_empty_row()
            self.grid.setCurrentCell(next_row, COL_CODE)

    def _remove_current_row(self) -> None:
        if self.entry and self.entry.status == JournalEntryStatus.POSTED:
            return
        self._close_current_editor()
        row = self.grid.currentRow()
        if row >= 0 and self.grid.rowCount() > 1:
            self.grid.removeRow(row)
            self._recalculate_totals()

    # -- الحساب الحي -----------------------------------------------------
    def _recalculate_totals(self) -> None:
        total_debit, total_credit = Decimal("0"), Decimal("0")
        for row in range(self.grid.rowCount()):
            if not self._row_has_data(row):
                continue
            try:
                d = Decimal((self.grid.item(row, COL_DEBIT).text() or "0").replace(",", ""))
                c = Decimal((self.grid.item(row, COL_CREDIT).text() or "0").replace(",", ""))
            except Exception:
                continue
            total_debit += d
            total_credit += c

        self.debit_total_label.setText(format_currency(total_debit, "SYP"))
        self.credit_total_label.setText(format_currency(total_credit, "SYP"))
        diff = total_debit - total_credit
        if diff == 0:
            self.diff_label.setText(f"{diff} ✓ متوازن")
            self.diff_label.setStyleSheet(
                "font-weight: bold; font-size: 16px; color: #16A34A; "
                "background-color: #F0FDF4; padding: 6px 14px; border-radius: 6px;"
            )
        else:
            self.diff_label.setText(f"{diff} ⚠ غير متوازن")
            self.diff_label.setStyleSheet(
                "font-weight: bold; font-size: 16px; color: #DC2626; "
                "background-color: #FEF2F2; padding: 6px 14px; border-radius: 6px;"
            )

    # -- حفظ وترحيل --------------------------------------------------------
    def _next_ref_no(self) -> str:
        count = self.session.query(JournalEntry).filter(
            JournalEntry.ref_no.like("JV-%")
        ).count()
        return f"JV-{count + 1:06d}"

    def _save_draft(self) -> None:
        if self.entry is None:
            self.entry = JournalEntry(
                entry_date=self.date_edit.date().toPython(),
                ref_no=self._next_ref_no(),
                description=self.description_edit.text().strip(),
                source_type="manual", currency_code="SYP", exchange_rate=1,
                status=JournalEntryStatus.DRAFT,
            )
            self.session.add(self.entry)
            self.session.flush()
        else:
            self.entry.description = self.description_edit.text().strip()
            for line in list(self.entry.lines):
                self.session.delete(line)
            self.session.flush()

        errors = []
        for row in range(self.grid.rowCount()):
            if not self._row_has_data(row):
                continue
            code = self.grid.item(row, COL_CODE).text().strip()
            match = next((a for a in self._accounts_cache if a.code == code), None)
            if not match:
                errors.append(f"السطر {row+1}: الحساب '{code}' غير موجود")
                continue
            debit_txt = (self.grid.item(row, COL_DEBIT).text() or "0").replace(",", "")
            credit_txt = (self.grid.item(row, COL_CREDIT).text() or "0").replace(",", "")
            try:
                add_manual_line(
                    self.session, self.entry, account_id=match.id,
                    debit=debit_txt or 0, credit=credit_txt or 0,
                )
            except JournalEditError as e:
                errors.append(f"السطر {row+1}: {e}")

        if errors:
            QMessageBox.warning(self, "تنبيه", "\n".join(errors))

        self.session.commit()
        self.ref_edit.setText(self.entry.ref_no)
        self.ref_label.setText(self.entry.ref_no)
        QMessageBox.information(self, "تم", "تم حفظ المسودة")
        self._refresh_editability()

    def _post(self) -> None:
        if self.entry is None or not self.entry.lines:
            self._save_draft()
        if self.entry is None:
            return

        confirm = QMessageBox.question(
            self, "تأكيد الترحيل",
            f"هل تريد ترحيل سند القيد رقم {self.entry.ref_no}؟\n"
            "لا يمكن التراجع عن هذا الإجراء إلا بعكسه لاحقاً.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            post_manual_entry(self.session, self.entry)
            self.session.commit()
        except JournalEditError as e:
            self.session.rollback()
            QMessageBox.critical(self, "تعذّر الترحيل", str(e))
            return
        QMessageBox.information(self, "تم", "تم ترحيل سند القيد بنجاح")
        self._load_entry()
