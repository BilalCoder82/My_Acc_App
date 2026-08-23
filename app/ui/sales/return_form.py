"""
Sales Return Invoice Form — مرتجع فاتورة البيع
"""

from __future__ import annotations
from decimal import Decimal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QDateEdit,
    QComboBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QPushButton,
    QLabel, QHeaderView, QMessageBox, QAbstractItemView, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QFont, QShortcut, QKeySequence
from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, Item
from app.services.invoice_calc import compute_invoice_totals
from app.services.invoice_edit import add_line as service_add_line, EditNotAllowedError
from app.services.posting import post_return, PostingError
from app.services.invoice_queries import list_items, get_invoice_by_no

GRID_COLUMNS = ["كود", "المادة", "الكمية", "السعر", "الحسم %", "الضريبة %", "الإجمالي"]
COL_CODE, COL_ITEM, COL_QTY, COL_PRICE, COL_DISC, COL_TAX, COL_TOTAL = range(7)

COLOR_PRIMARY = "#2563EB"
COLOR_BG = "#F5F7FA"
COLOR_CARD_BORDER = "#E5E7EB"
COLOR_HEADER_BG = "#EEF2FF"

STATUS_STYLE = {
    InvoiceStatus.DRAFT: ("مسودة", "#F59E0B"),
    InvoiceStatus.POSTED: ("مرحّلة", "#16A34A"),
    InvoiceStatus.CANCELLED: ("ملغاة", "#DC2626"),
}

FIELD_WIDTHS = {
    "invoice_no": 160, "date": 140, "currency": 120,
    "exchange_rate": 120, "original_invoice": 200,
}

CARD_STYLE = (
    "QFrame {"
    "  background: white;"
    "  border-radius: 6px;"
    "  padding: 8px;"
    "  border: 1px solid " + COLOR_CARD_BORDER + ";"
    "}"
)


class SalesReturnInvoiceFormView(QWidget):
    def __init__(self, session: Session, invoice_id: int | None = None, parent=None):
        super().__init__(parent)
        self.session = session
        self.invoice: Invoice | None = session.get(Invoice, invoice_id) if invoice_id else None
        self._items_cache = list_items(session)

        self.setStyleSheet(f"background-color: {COLOR_BG};")
        self._build_ui()
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save_draft)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._post)
        if self.invoice:
            self._load_invoice()
        else:
            for _ in range(8):
                self._add_empty_row()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(self._build_document_header())
        layout.addWidget(self._build_document_info())
        layout.addWidget(self._build_lines_grid(), stretch=1)
        layout.addLayout(self._build_totals_and_actions())
        self._refresh_editability()

    def _build_document_header(self) -> QWidget:
        card = QFrame()
        card.setStyleSheet(CARD_STYLE)
        row = QHBoxLayout(card)
        row.setSpacing(8)

        title = QLabel("مرتجع فاتورة بيع")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #111827;")

        self.invoice_no_label = QLabel("جديدة (غير محفوظة)")
        self.invoice_no_label.setStyleSheet("color: #6B7280; font-size: 12px;")

        self.status_badge = QLabel()
        self.status_badge.setStyleSheet(
            "border-radius: 10px; padding: 3px 12px; color: white; font-weight: bold; font-size: 12px;"
        )

        row.addWidget(title)
        row.addWidget(self.invoice_no_label)
        row.addWidget(self.status_badge)
        row.addStretch()
        return card

    def _build_document_info(self) -> QWidget:
        card = QFrame()
        card.setStyleSheet(CARD_STYLE)
        grid = QGridLayout(card)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)

        def labeled(text: str, widget: QWidget, width: int | None = None) -> QWidget:
            if width:
                widget.setFixedWidth(width)
            container = QWidget()
            container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            if width:
                container.setFixedWidth(width)
            box = QVBoxLayout(container)
            box.setSpacing(2)
            box.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #6B7280; font-size: 11px;")
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            box.addWidget(lbl)
            box.addWidget(widget)
            return container

        self.invoice_no_edit = QLineEdit()
        self.invoice_no_edit.setReadOnly(True)
        self.invoice_no_edit.setPlaceholderText("يُولَّد عند الترحيل")

        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)

        self.original_invoice_edit = QLineEdit()
        self.original_invoice_edit.setPlaceholderText("رقم فاتورة البيع الأصلية...")
        self.original_invoice_edit.setMinimumWidth(200)
        self.original_invoice_edit.textChanged.connect(self._on_original_invoice_changed)

        self.party_edit = QLineEdit()
        self.party_edit.setPlaceholderText("اسم العميل (يُملأ تلقائياً من الفاتورة الأصلية)")
        self.party_edit.setReadOnly(True)
        self.party_edit.setMinimumWidth(200)

        self.currency_combo = QComboBox()
        self.currency_combo.addItems(["SYP", "USD", "TRY", "EUR"])

        self.exchange_rate_spin = QDoubleSpinBox()
        self.exchange_rate_spin.setDecimals(4)
        self.exchange_rate_spin.setMaximum(1_000_000)
        self.exchange_rate_spin.setValue(1)
        self.exchange_rate_spin.setButtonSymbols(QDoubleSpinBox.NoButtons)

        grid.addWidget(labeled("رقم المرتجع", self.invoice_no_edit, FIELD_WIDTHS["invoice_no"]), 0, 0)
        grid.addWidget(labeled("التاريخ", self.date_edit, FIELD_WIDTHS["date"]), 0, 1)
        grid.addWidget(labeled("الفاتورة الأصلية", self.original_invoice_edit, FIELD_WIDTHS["original_invoice"]), 0, 2)
        grid.addWidget(labeled("العميل", self.party_edit), 0, 3)

        grid.addWidget(labeled("العملة", self.currency_combo, FIELD_WIDTHS["currency"]), 1, 0)
        grid.addWidget(labeled("سعر الصرف", self.exchange_rate_spin, FIELD_WIDTHS["exchange_rate"]), 1, 1)
        grid.setColumnStretch(5, 1)
        return card

    def _on_original_invoice_changed(self, text: str) -> None:
        text = text.strip()
        if not text:
            self.party_edit.clear()
            return
        original = get_invoice_by_no(self.session, text)
        if original and original.kind == InvoiceKind.SALES and original.status == InvoiceStatus.POSTED:
            self.party_edit.setText(original.party_name)
            self.currency_combo.setCurrentText(original.currency_code)
            self.exchange_rate_spin.setValue(float(original.exchange_rate))
            self._load_original_lines(original)
        else:
            self.party_edit.clear()

    def _load_original_lines(self, original: Invoice) -> None:
        self.grid.blockSignals(True)
        self._close_current_editor()
        self.grid.setRowCount(0)
        for line in original.lines:
            self._add_row_from_line(line, is_return=True)
        for _ in range(max(1, 8 - len(original.lines))):
            self._add_empty_row()
        self.grid.blockSignals(False)
        self._recalculate_totals()

    def _build_lines_grid(self) -> QWidget:
        self.grid = QTableWidget(0, len(GRID_COLUMNS))
        self.grid.setHorizontalHeaderLabels(GRID_COLUMNS)

        self.grid.horizontalHeader().setSectionResizeMode(COL_ITEM, QHeaderView.Stretch)
        for col in [COL_CODE, COL_QTY, COL_PRICE, COL_DISC, COL_TAX, COL_TOTAL]:
            self.grid.horizontalHeader().setSectionResizeMode(col, QHeaderView.Fixed)

        self.grid.setColumnWidth(COL_CODE, 80)
        self.grid.setColumnWidth(COL_QTY, 80)
        self.grid.setColumnWidth(COL_PRICE, 100)
        self.grid.setColumnWidth(COL_DISC, 80)
        self.grid.setColumnWidth(COL_TAX, 80)
        self.grid.setColumnWidth(COL_TOTAL, 120)

        self.grid.horizontalHeader().setFixedHeight(36)
        self.grid.verticalHeader().setDefaultSectionSize(34)
        self.grid.verticalHeader().hide()
        self.grid.setSelectionBehavior(QAbstractItemView.SelectItems)

        self.grid.setStyleSheet(
            "QTableWidget { background: white; border: 1px solid #E5E7EB; }"
            "QHeaderView::section { background: #EEF2FF; padding: 6px; "
            "border: none; font-weight: bold; font-size: 12px; }"
            "QTableWidget::item { border-bottom: 1px solid #F3F4F6; padding: 4px; }"
        )
        self.grid.itemChanged.connect(self._on_cell_changed)
        self.grid.installEventFilter(self)
        return self.grid

    def _build_totals_and_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)

        actions = QVBoxLayout()
        actions.setSpacing(8)

        post_btn = QPushButton("ترحيل")
        post_btn.setStyleSheet(
            f"background-color: {COLOR_PRIMARY}; color: white; font-weight: bold; "
            "padding: 10px 24px; border-radius: 4px; font-size: 13px;"
        )
        post_btn.clicked.connect(self._post)

        save_btn = QPushButton("حفظ مسودة")
        save_btn.setStyleSheet(
            "padding: 8px 20px; border: 1px solid #D1D5DB; border-radius: 4px; "
            "background: white; font-size: 12px;"
        )
        save_btn.clicked.connect(self._save_draft)

        print_btn = QPushButton("طباعة")
        print_btn.setStyleSheet(
            "padding: 8px 20px; border: 1px solid #D1D5DB; border-radius: 4px; "
            "background: white; font-size: 12px;"
        )

        actions.addWidget(post_btn)
        actions.addWidget(save_btn)
        actions.addWidget(print_btn)
        actions.addStretch()

        totals_card = QFrame()
        totals_card.setStyleSheet(CARD_STYLE)
        totals_card.setFixedWidth(300)
        t_layout = QVBoxLayout(totals_card)
        t_layout.setSpacing(8)

        self.subtotal_label = self._totals_row(t_layout, "الإجمالي قبل الضريبة", "0.00")
        self.discount_label = self._totals_row(t_layout, "الخصم", "0.00")
        self.tax_label = self._totals_row(t_layout, "الضريبة", "0.00")

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #E5E7EB; background: #E5E7EB;")
        line.setFixedHeight(1)
        t_layout.addWidget(line)

        grand_row = QHBoxLayout()
        grand_row.setSpacing(8)
        grand_title = QLabel("الصافي")
        grand_title.setStyleSheet("font-weight: bold; font-size: 15px; color: #111827;")
        self.grand_total_label = QLabel("0.00")
        self.grand_total_label.setStyleSheet(
            f"font-weight: bold; font-size: 20px; color: {COLOR_PRIMARY}; "
            "background-color: #EFF6FF; padding: 6px 14px; border-radius: 6px;"
        )
        grand_row.addWidget(grand_title)
        grand_row.addStretch()
        grand_row.addWidget(self.grand_total_label)
        t_layout.addLayout(grand_row)

        row.addLayout(actions)
        row.addStretch()
        row.addWidget(totals_card)
        return row

    @staticmethod
    def _totals_row(layout: QVBoxLayout, title: str, value: str) -> QLabel:
        r = QHBoxLayout()
        r.setSpacing(8)
        lbl = QLabel(title)
        lbl.setStyleSheet("color: #4B5563; font-size: 12px;")
        r.addWidget(lbl)
        r.addStretch()
        value_label = QLabel(value)
        value_label.setStyleSheet("color: #111827; font-size: 13px; font-weight: 500;")
        r.addWidget(value_label)
        layout.addLayout(r)
        return value_label

    def _load_invoice(self) -> None:
        inv = self.invoice
        self.invoice_no_label.setText(inv.invoice_no)
        self.invoice_no_edit.setText(inv.invoice_no)
        self.date_edit.setDate(QDate(inv.invoice_date.year, inv.invoice_date.month, inv.invoice_date.day))
        self.party_edit.setText(inv.party_name)
        self.currency_combo.setCurrentText(inv.currency_code)
        self.exchange_rate_spin.setValue(float(inv.exchange_rate))
        if inv.original_invoice_id:
            from app.models import Invoice
            orig = self.session.get(Invoice, inv.original_invoice_id)
            if orig:
                self.original_invoice_edit.setText(orig.invoice_no)

        self.grid.blockSignals(True)
        self._close_current_editor()
        self.grid.setRowCount(0)
        for line in inv.lines:
            self._add_row_from_line(line)
        for _ in range(max(1, 8 - len(inv.lines))):
            self._add_empty_row()
        self.grid.blockSignals(False)

        self._recalculate_totals()
        self._refresh_editability()

    def _refresh_editability(self) -> None:
        status = self.invoice.status if self.invoice else InvoiceStatus.DRAFT
        text, color = STATUS_STYLE.get(status, ("مسودة", "#F59E0B"))
        if self.invoice is None:
            text = "جديدة"
        self.status_badge.setText(text)
        self.status_badge.setStyleSheet(
            f"background-color: {color}; border-radius: 10px; padding: 3px 12px; "
            "color: white; font-weight: bold; font-size: 12px;"
        )

        is_posted = self.invoice is not None and self.invoice.status == InvoiceStatus.POSTED
        for widget in (self.date_edit, self.party_edit, self.currency_combo,
                       self.exchange_rate_spin, self.original_invoice_edit):
            widget.setEnabled(not is_posted)
        self.grid.setEditTriggers(
            QAbstractItemView.NoEditTriggers if is_posted else QAbstractItemView.AllEditTriggers
        )

    def _close_current_editor(self) -> None:
        current_item = self.grid.currentItem()
        if current_item:
            self.grid.closePersistentEditor(current_item)

    def _add_empty_row(self) -> None:
        self._close_current_editor()
        row = self.grid.rowCount()
        self.grid.insertRow(row)
        for col in range(len(GRID_COLUMNS)):
            self.grid.setItem(row, col, QTableWidgetItem(""))

    def _add_row_from_line(self, line: InvoiceLine, is_return: bool = False) -> None:
        row = self.grid.rowCount()
        self.grid.insertRow(row)
        item = line.item
        self.grid.setItem(row, COL_CODE, QTableWidgetItem(item.sku))
        self.grid.setItem(row, COL_ITEM, QTableWidgetItem(item.name_ar))
        qty = line.quantity
        if is_return:
            qty = abs(qty)
        self.grid.setItem(row, COL_QTY, QTableWidgetItem(str(qty)))
        self.grid.setItem(row, COL_PRICE, QTableWidgetItem(str(line.unit_price)))
        self.grid.setItem(row, COL_DISC, QTableWidgetItem(str(line.discount_percent)))
        self.grid.setItem(row, COL_TAX, QTableWidgetItem(str(line.tax_rate)))
        total_item = QTableWidgetItem("")
        total_item.setFlags(total_item.flags() & ~Qt.ItemIsEditable)
        self.grid.setItem(row, COL_TOTAL, total_item)

    def _on_cell_changed(self, changed_item: QTableWidgetItem) -> None:
        row = changed_item.row()
        if changed_item.column() == COL_CODE:
            code = changed_item.text().strip()
            match = next((it for it in self._items_cache if it.sku == code), None)
            if match:
                self.grid.blockSignals(True)
                self._close_current_editor()
                self.grid.setItem(row, COL_ITEM, QTableWidgetItem(match.name_ar))
                self.grid.blockSignals(False)

        if row == self.grid.rowCount() - 1 and self._row_has_data(row):
            self._close_current_editor()
            self._add_empty_row()

        self._recalculate_totals()

    def _row_has_data(self, row: int) -> bool:
        code_item = self.grid.item(row, COL_CODE)
        return bool(code_item and code_item.text().strip())

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        if obj is self.grid and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Insert:
                self._add_empty_row()
                return True
            if key == Qt.Key_Delete:
                self._remove_current_row()
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._move_to_next_cell()
                return True
        return super().eventFilter(obj, event)

    def _move_to_next_cell(self) -> None:
        row, col = self.grid.currentRow(), self.grid.currentColumn()
        skip = {COL_ITEM, COL_TOTAL}
        next_col = col + 1
        while next_col in skip and next_col < len(GRID_COLUMNS):
            next_col += 1
        if next_col < len(GRID_COLUMNS):
            self.grid.setCurrentCell(row, next_col)
        else:
            next_row = row + 1
            if next_row >= self.grid.rowCount():
                self._add_empty_row()
            self.grid.setCurrentCell(next_row, COL_CODE)

    def _remove_current_row(self) -> None:
        if self.invoice and self.invoice.status == InvoiceStatus.POSTED:
            return
        self._close_current_editor()
        row = self.grid.currentRow()
        if row >= 0 and self.grid.rowCount() > 1:
            self.grid.removeRow(row)
            self._recalculate_totals()

    def _get_original_invoice(self) -> Invoice | None:
        no = self.original_invoice_edit.text().strip()
        if not no:
            return None
        return get_invoice_by_no(self.session, no)

    def _build_transient_invoice(self) -> Invoice | None:
        temp = Invoice(
            invoice_no="TEMP",
            kind=InvoiceKind.SALES_RETURN,
            currency_code=self.currency_combo.currentText(),
            exchange_rate=Decimal(str(self.exchange_rate_spin.value())),
        )
        original = self._get_original_invoice()
        if original:
            temp.original_invoice_id = original.id

        lines = []
        for row in range(self.grid.rowCount()):
            code_item = self.grid.item(row, COL_CODE)
            if not code_item or not code_item.text().strip():
                continue
            match = next((it for it in self._items_cache if it.sku == code_item.text().strip()), None)
            if not match:
                continue
            try:
                qty = Decimal(self.grid.item(row, COL_QTY).text() or "0")
                price = Decimal(self.grid.item(row, COL_PRICE).text() or "0")
                disc = Decimal(self.grid.item(row, COL_DISC).text() or "0")
                tax = Decimal(self.grid.item(row, COL_TAX).text() or "0")
            except Exception:
                continue
            lines.append(InvoiceLine(
                item_id=match.id, quantity=-abs(qty), unit_price=price,
                discount_percent=disc, discount_amount=Decimal("0"), tax_rate=tax,
            ))
        if not lines:
            return None
        temp.lines = lines
        temp.discount_percent = Decimal("0")
        temp.discount_amount = Decimal("0")
        return temp

    def _recalculate_totals(self) -> None:
        temp = self._build_transient_invoice()
        if temp is None:
            self.subtotal_label.setText("0.00")
            self.discount_label.setText("0.00")
            self.tax_label.setText("0.00")
            self.grand_total_label.setText("0.00")
            return
        try:
            totals = compute_invoice_totals(temp)
        except Exception:
            return

        for row, line_total in zip(
            (r for r in range(self.grid.rowCount()) if self._row_has_data(r)), totals.lines
        ):
            total_cell = self.grid.item(row, COL_TOTAL)
            if total_cell is None:
                continue
            self.grid.blockSignals(True)
            total_cell.setText(str(line_total.line_grand_total))
            self.grid.blockSignals(False)

        self.subtotal_label.setText(str(totals.subtotal))
        self.discount_label.setText(str(totals.total_discount))
        self.tax_label.setText(str(totals.total_tax))
        self.grand_total_label.setText(str(totals.grand_total))

    def _save_draft(self) -> None:
        temp = self._build_transient_invoice()
        if temp is None:
            QMessageBox.warning(self, "تنبيه", "لا يوجد بنود صالحة بالمرتجع")
            return

        original = self._get_original_invoice()
        if original is None:
            QMessageBox.warning(self, "تنبيه", "يجب تحديد الفاتورة الأصلية")
            return

        if self.invoice is None:
            self.invoice = Invoice(
                invoice_no=f"RET-{id(self)}",
                kind=InvoiceKind.SALES_RETURN,
                invoice_date=self.date_edit.date().toPython(),
                party_name=self.party_edit.text().strip(),
                currency_code=self.currency_combo.currentText(),
                exchange_rate=Decimal(str(self.exchange_rate_spin.value())),
                original_invoice_id=original.id,
            )
            self.session.add(self.invoice)
            self.session.flush()

        for line in list(self.invoice.lines):
            self.session.delete(line)
        self.session.flush()
        for line in temp.lines:
            service_add_line(
                self.session, self.invoice, item_id=line.item_id, quantity=line.quantity,
                unit_price=line.unit_price, discount_percent=line.discount_percent,
                tax_rate=line.tax_rate,
            )
        self.session.commit()
        self.invoice_no_edit.setText(self.invoice.invoice_no)
        self.invoice_no_label.setText(self.invoice.invoice_no)
        QMessageBox.information(self, "تم", "تم حفظ المسودة")
        self._refresh_editability()

    def _post(self) -> None:
        if self.invoice is None:
            self._save_draft()
        if self.invoice is None:
            return

        original = self._get_original_invoice()
        if original is None:
            QMessageBox.warning(self, "تنبيه", "يجب تحديد الفاتورة الأصلية المرحّلة")
            return
        if original.status != InvoiceStatus.POSTED:
            QMessageBox.warning(self, "تنبيه", "الفاتورة الأصلية غير مرحّلة — لا يمكن عكسها")
            return

        confirm = QMessageBox.question(
            self, "تأكيد الترحيل",
            f"هل تريد ترحيل مرتجع {self.invoice.invoice_no} مقابل الفاتورة {original.invoice_no}؟\n"
            "سيتم عكس القيد المحاسبي الأصلي بالضبط.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            post_return(self.session, original, self.invoice)
            self.session.commit()
        except (PostingError, EditNotAllowedError) as e:
            self.session.rollback()
            QMessageBox.critical(self, "تعذّر الترحيل", str(e))
            return
        QMessageBox.information(self, "تم", "تم ترحيل المرتجع بنجاح")
        self._load_invoice()