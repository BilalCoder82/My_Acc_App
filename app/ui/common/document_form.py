"""
Base Document Form — الأساس المشترك لكل الفواتير والمرتجعات
==================================================================
فاتورة البيع هي المرجع البصري والسلوكي لكل مستند آخر (طلب المستخدم
صراحة) — هذا الملف هو ذلك المرجع مستخرَجاً كصنف أساسي قابل للتخصيص،
بدل تكراره حرفياً 4 مرات. الفروق بين أنواع المستندات (بيع/شراء/مرتجع
بيع/مرتجع شراء) محصورة بمعاملات __init__ فقط — لا فروع if/else مبعثرة
بمنطق الشبكة أو الحساب.

قاعدة صارمة كما بكل مكان: كل حساب عبر invoice_calc.py، كل حفظ/ترحيل
عبر الدالة الممرَّرة صراحة (posting function)، لا منطق محاسبي هنا.
"""

from __future__ import annotations
from decimal import Decimal
from typing import Callable
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QDateEdit,
    QComboBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QPushButton,
    QLabel, QHeaderView, QMessageBox, QAbstractItemView, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QFont, QShortcut, QKeySequence
from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, Item, Warehouse
from app.services.invoice_calc import compute_invoice_totals
from app.services.invoice_edit import add_line as service_add_line, EditNotAllowedError
from app.services.invoice_validation import InvoiceValidationError
from app.services.invoice_queries import list_items
from app.ui.common.numeric_delegate import NumericGridDelegate, format_currency

GRID_COLUMNS = ["كود", "المادة", "الكمية", "السعر", "الحسم %", "الضريبة %", "الإجمالي"]
COL_CODE, COL_ITEM, COL_QTY, COL_PRICE, COL_DISC, COL_TAX, COL_TOTAL = range(7)
NUMERIC_COLUMNS = [COL_QTY, COL_PRICE, COL_DISC, COL_TAX, COL_TOTAL]

COLOR_PRIMARY = "#2563EB"
COLOR_BG = "#F5F7FA"
COLOR_CARD_BORDER = "#E5E7EB"

STATUS_STYLE = {
    InvoiceStatus.DRAFT: ("مسودة", "#F59E0B"),
    InvoiceStatus.POSTED: ("مرحّلة", "#16A34A"),
    InvoiceStatus.CANCELLED: ("ملغاة", "#DC2626"),
}

FIELD_WIDTHS = {"invoice_no": 160, "date": 140, "currency": 120, "exchange_rate": 120, "is_cash": 140, "warehouse": 180}

CARD_STYLE = (
    "QFrame { background: white; border-radius: 6px; padding: 8px; "
    f"border: 1px solid {COLOR_CARD_BORDER}; }}"
)


class BaseDocumentFormView(QWidget):
    """
    doc_title: العنوان المعروض ("فاتورة بيع", "فاتورة شراء", "مرتجع بيع"...)
    kind: InvoiceKind المطابق
    party_label: "العميل" أو "المورد"
    is_customer: True لجهة العميل (يؤثر على أي حساب فرعي يُنشأ)
    posting_fn: دالة الترحيل من app/services (توقيعها: (session, invoice, is_cash) -> JournalEntry)
    is_return: يُفعّل حقل "ربط بفاتورة أصلية" الإضافي
    """

    def __init__(
        self, session: Session, doc_title: str, kind: InvoiceKind, party_label: str,
        is_customer: bool, posting_fn: Callable, ref_prefix: str,
        invoice_id: int | None = None, is_return: bool = False, parent=None,
    ):
        super().__init__(parent)
        self.session = session
        self.doc_title = doc_title
        self.kind = kind
        self.party_label = party_label
        self.is_customer = is_customer
        self.posting_fn = posting_fn
        self.ref_prefix = ref_prefix
        self.is_return = is_return
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

    # -- بناء الواجهة -----------------------------------------------------
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

        title = QLabel(self.doc_title)
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

        self.party_edit = QLineEdit()
        self.party_edit.setPlaceholderText(f"ابحث عن {self.party_label} أو اكتب اسمه...")
        self.party_edit.setMinimumWidth(200)

        self.currency_combo = QComboBox()
        self.currency_combo.addItems(["SYP", "USD", "TRY", "EUR"])
        self.currency_combo.currentTextChanged.connect(self._recalculate_totals)

        self.exchange_rate_spin = QDoubleSpinBox()
        self.exchange_rate_spin.setDecimals(4)
        self.exchange_rate_spin.setMaximum(1_000_000)
        self.exchange_rate_spin.setValue(1)
        self.exchange_rate_spin.setButtonSymbols(QDoubleSpinBox.NoButtons)

        self.is_cash_combo = QComboBox()
        self.is_cash_combo.addItems(["نقدي", "آجل"])

        # المستودع (WORKFLOW.md §46-§47): خاصية على مستوى الفاتورة، إلزامي
        # اختياره صراحة — لا سقوط صامت لمستودع افتراضي غير ظاهر للمستخدم.
        # العنصر الأول عمداً بلا userData صالح (placeholder غير قابل للقبول).
        self.warehouse_combo = QComboBox()
        self.warehouse_combo.addItem("-- اختر المستودع --", None)
        for wh in self.session.query(Warehouse).filter_by(is_active=True).order_by(Warehouse.name_ar).all():
            self.warehouse_combo.addItem(wh.name_ar, wh.id)

        party_row = QHBoxLayout()
        party_row.setSpacing(4)
        party_row.setContentsMargins(0, 0, 0, 0)
        party_row.addWidget(self.party_edit, stretch=1)
        party_widget = QWidget()
        party_widget.setLayout(party_row)
        party_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        party_widget.setMinimumWidth(280)

        grid.addWidget(labeled("رقم المستند", self.invoice_no_edit, FIELD_WIDTHS["invoice_no"]), 0, 0)
        grid.addWidget(labeled("التاريخ", self.date_edit, FIELD_WIDTHS["date"]), 0, 1)
        grid.addWidget(labeled(self.party_label, party_widget), 0, 2, 1, 2)
        grid.addWidget(labeled("طريقة الدفع", self.is_cash_combo, FIELD_WIDTHS["is_cash"]), 0, 4)

        grid.addWidget(labeled("العملة", self.currency_combo, FIELD_WIDTHS["currency"]), 1, 0)
        grid.addWidget(labeled("سعر الصرف", self.exchange_rate_spin, FIELD_WIDTHS["exchange_rate"]), 1, 1)
        # المستودع بصف مستقل دائماً — لا يتصادم مع صف رابط المستند الأصلي
        # بالمرتجعات (صف 1، أعمدة 2-4)، ويبقى واضحاً بارزاً كما اشتُرط
        # صراحة ("ليس مخفياً في إعدادات أو قيمة افتراضية غير ظاهرة").
        grid.addWidget(labeled("المستودع *", self.warehouse_combo, FIELD_WIDTHS["warehouse"]), 2, 0)

        if self.is_return:
            self.original_ref_edit = QLineEdit()
            self.original_ref_edit.setPlaceholderText("رقم المستند الأصلي (اختياري)")
            load_btn = QPushButton("تحميل بنود المستند الأصلي")
            load_btn.setStyleSheet(
                "padding: 4px 12px; border: 1px solid #D1D5DB; border-radius: 4px; "
                "background: white; font-size: 11px;"
            )
            load_btn.clicked.connect(self._load_from_original)
            link_row = QHBoxLayout()
            link_row.setSpacing(6)
            link_row.setContentsMargins(0, 0, 0, 0)
            link_row.addWidget(self.original_ref_edit, stretch=1)
            link_row.addWidget(load_btn)
            link_widget = QWidget()
            link_widget.setLayout(link_row)
            grid.addWidget(
                labeled("مربوط بفاتورة أصلية (اختياري)", link_widget), 1, 2, 1, 3
            )

        grid.setColumnStretch(5, 1)
        return card

    def _build_lines_grid(self) -> QWidget:
        self.grid = QTableWidget(0, len(GRID_COLUMNS))
        self.grid.setHorizontalHeaderLabels(GRID_COLUMNS)

        self.grid.horizontalHeader().setSectionResizeMode(COL_ITEM, QHeaderView.Stretch)
        for col in [COL_CODE, COL_QTY, COL_PRICE, COL_DISC, COL_TAX, COL_TOTAL]:
            self.grid.horizontalHeader().setSectionResizeMode(col, QHeaderView.Fixed)
        self.grid.setColumnWidth(COL_CODE, 80)
        self.grid.setColumnWidth(COL_QTY, 80)
        self.grid.setColumnWidth(COL_PRICE, 110)
        self.grid.setColumnWidth(COL_DISC, 80)
        self.grid.setColumnWidth(COL_TAX, 80)
        self.grid.setColumnWidth(COL_TOTAL, 130)

        self.grid.horizontalHeader().setFixedHeight(36)
        self.grid.verticalHeader().setDefaultSectionSize(34)
        self.grid.verticalHeader().hide()
        self.grid.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.grid.setLayoutDirection(Qt.RightToLeft)

        # تنسيق الأرقام (فاصلة آلاف) ومؤشر يبدأ من اليمين — على كل الأعمدة الرقمية
        for col in NUMERIC_COLUMNS:
            editable = col != COL_TOTAL  # الإجمالي محسوب دائماً، غير قابل للتحرير
            self.grid.setItemDelegateForColumn(col, NumericGridDelegate(2, editable=editable, parent=self.grid))

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

    # -- ربط المرتجع بمستند أصلي (اختياري) -------------------------------
    def _load_from_original(self) -> None:
        from app.services.returns import get_returnable_lines
        ref = self.original_ref_edit.text().strip()
        if not ref:
            QMessageBox.warning(self, "تنبيه", "أدخل رقم المستند الأصلي أولاً")
            return
        original = self.session.query(Invoice).filter_by(invoice_no=ref).first()
        if original is None:
            QMessageBox.warning(self, "غير موجود", f"لا يوجد مستند برقم {ref}")
            return
        if original.status != InvoiceStatus.POSTED:
            QMessageBox.warning(self, "تنبيه", "المستند الأصلي غير مرحّل — لا يمكن الربط به")
            return

        self._original_invoice_id = original.id
        self.party_edit.setText(original.party_name)
        self.currency_combo.setCurrentText(original.currency_code)
        self.exchange_rate_spin.setValue(float(original.exchange_rate))
        # المرتجع المرتبط يرث مستودع الفاتورة الأصلية إلزامياً ويُقفَل
        # (WORKFLOW.md §47.3): يجب أن تعود الكمية للمستودع الذي خرجت منه
        # فعلياً، لا مستودع آخر يختاره المستخدم عن طريق الخطأ.
        self._set_warehouse(original.warehouse_id, locked=True)

        lines = get_returnable_lines(self.session, original)
        self.grid.blockSignals(True)
        self.grid.setRowCount(0)
        for l in lines:
            row = self.grid.rowCount()
            self.grid.insertRow(row)
            self.grid.setItem(row, COL_CODE, QTableWidgetItem(l["sku"]))
            self.grid.setItem(row, COL_ITEM, QTableWidgetItem(l["name_ar"]))
            self.grid.setItem(row, COL_QTY, QTableWidgetItem(str(l["quantity"])))
            self.grid.setItem(row, COL_PRICE, QTableWidgetItem(str(l["unit_price"])))
            self.grid.setItem(row, COL_DISC, QTableWidgetItem(str(l["discount_percent"])))
            self.grid.setItem(row, COL_TAX, QTableWidgetItem(str(l["tax_rate"])))
            total_item = QTableWidgetItem("")
            total_item.setFlags(total_item.flags() & ~Qt.ItemIsEditable)
            self.grid.setItem(row, COL_TOTAL, total_item)
        self._add_empty_row()
        self.grid.blockSignals(False)
        self._recalculate_totals()
        QMessageBox.information(self, "تم", f"تم تحميل {len(lines)} بند من المستند الأصلي — عدّل الكميات المُرجعة فعلياً")

    # -- تحميل مستند موجود -------------------------------------------------
    def _load_invoice(self) -> None:
        inv = self.invoice
        self.invoice_no_label.setText(inv.invoice_no)
        self.invoice_no_edit.setText(inv.invoice_no)
        self.date_edit.setDate(QDate(inv.invoice_date.year, inv.invoice_date.month, inv.invoice_date.day))
        self.party_edit.setText(inv.party_name)
        self.currency_combo.setCurrentText(inv.currency_code)
        self.exchange_rate_spin.setValue(float(inv.exchange_rate))
        is_linked_return = self.is_return and bool(inv.original_invoice_id)
        self._set_warehouse(inv.warehouse_id, locked=is_linked_return)
        if self.is_return and inv.original_invoice_id:
            original = self.session.get(Invoice, inv.original_invoice_id)
            if original:
                self.original_ref_edit.setText(original.invoice_no)

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
        widgets = [self.date_edit, self.party_edit, self.currency_combo, self.exchange_rate_spin]
        for widget in widgets:
            widget.setEnabled(not is_posted)
        # المستودع: مقفل أيضاً لو مرتجع مرتبط بمستند أصلي (بصرف النظر عن
        # حالة الترحيل — الوراثة إلزامية من لحظة الربط، لا من الترحيل فقط)
        is_linked_return = self.is_return and self.invoice is not None and bool(self.invoice.original_invoice_id)
        self.warehouse_combo.setEnabled(not is_posted and not is_linked_return)
        self.grid.setEditTriggers(
            QAbstractItemView.NoEditTriggers if is_posted else QAbstractItemView.AllEditTriggers
        )

    def _close_current_editor(self) -> None:
        current_item = self.grid.currentItem()
        if current_item:
            self.grid.closePersistentEditor(current_item)

    # -- الشبكة --------------------------------------------------------------
    def _add_empty_row(self) -> None:
        self._close_current_editor()
        row = self.grid.rowCount()
        self.grid.insertRow(row)
        for col in range(len(GRID_COLUMNS)):
            self.grid.setItem(row, col, QTableWidgetItem(""))

    def _add_row_from_line(self, line: InvoiceLine) -> None:
        row = self.grid.rowCount()
        self.grid.insertRow(row)
        item = line.item
        self.grid.setItem(row, COL_CODE, QTableWidgetItem(item.sku))
        self.grid.setItem(row, COL_ITEM, QTableWidgetItem(item.name_ar))
        self.grid.setItem(row, COL_QTY, QTableWidgetItem(str(line.quantity)))
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

    # -- الحساب الحي -----------------------------------------------------
    def _build_transient_invoice(self) -> Invoice | None:
        temp = Invoice(
            invoice_no="TEMP", kind=self.kind,
            currency_code=self.currency_combo.currentText(),
            exchange_rate=Decimal(str(self.exchange_rate_spin.value())),
        )
        lines = []
        for row in range(self.grid.rowCount()):
            code_item = self.grid.item(row, COL_CODE)
            if not code_item or not code_item.text().strip():
                continue
            match = next((it for it in self._items_cache if it.sku == code_item.text().strip()), None)
            if not match:
                continue
            try:
                qty = Decimal((self.grid.item(row, COL_QTY).text() or "0").replace(",", ""))
                price = Decimal((self.grid.item(row, COL_PRICE).text() or "0").replace(",", ""))
                disc = Decimal((self.grid.item(row, COL_DISC).text() or "0").replace(",", ""))
                tax = Decimal((self.grid.item(row, COL_TAX).text() or "0").replace(",", ""))
            except Exception:
                continue
            lines.append(InvoiceLine(
                item_id=match.id, quantity=qty, unit_price=price,
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
        currency = self.currency_combo.currentText()
        if temp is None:
            self.subtotal_label.setText(format_currency(0, currency))
            self.discount_label.setText(format_currency(0, currency))
            self.tax_label.setText(format_currency(0, currency))
            self.grand_total_label.setText(format_currency(0, currency))
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

        self.subtotal_label.setText(format_currency(totals.subtotal, currency))
        self.discount_label.setText(format_currency(totals.total_discount, currency))
        self.tax_label.setText(format_currency(totals.total_tax, currency))
        self.grand_total_label.setText(format_currency(totals.grand_total, currency))

    # -- حفظ وترحيل --------------------------------------------------------
    def _set_warehouse(self, warehouse_id: int | None, *, locked: bool = False) -> None:
        """يضبط اختيار المستودع بالـComboBox، ويقفله (تعطيل) إن طُلب —
        للمرتجع المرتبط تحديداً: لا يجوز اختيار مستودع مختلف عن الذي
        خرجت منه البضاعة فعلياً (WORKFLOW.md §47.3)."""
        if warehouse_id is not None:
            idx = self.warehouse_combo.findData(warehouse_id)
            if idx >= 0:
                self.warehouse_combo.setCurrentIndex(idx)
        self.warehouse_combo.setEnabled(not locked)

    def _selected_warehouse_id(self) -> int | None:
        return self.warehouse_combo.currentData()

    def _validate_warehouse_selected(self) -> bool:
        """لا سقوط صامت لمستودع افتراضي — رفض واضح إن لم يُختَر (WORKFLOW.md §47)."""
        if self._selected_warehouse_id() is None:
            QMessageBox.warning(self, "المستودع مطلوب", "يجب اختيار المستودع قبل الحفظ أو الترحيل.")
            return False
        return True

    def _save_draft(self) -> None:
        if not self._validate_warehouse_selected():
            return
        temp = self._build_transient_invoice()
        if temp is None:
            QMessageBox.warning(self, "تنبيه", "لا يوجد بنود صالحة بالمستند")
            return

        if self.invoice is None:
            self.invoice = Invoice(
                invoice_no=f"DRAFT-{id(self)}", kind=self.kind,
                invoice_date=self.date_edit.date().toPython(),
                party_name=self.party_edit.text().strip(),
                currency_code=self.currency_combo.currentText(),
                exchange_rate=Decimal(str(self.exchange_rate_spin.value())),
                original_invoice_id=getattr(self, "_original_invoice_id", None),
                warehouse_id=self._selected_warehouse_id(),
            )
            self.session.add(self.invoice)
            self.session.flush()
        else:
            # مسودة موجودة يُعاد حفظها — نُزامن المستودع من الـComboBox
            # دائماً (قد يكون المستخدم عدّله قبل إعادة الحفظ)
            self.invoice.warehouse_id = self._selected_warehouse_id()

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
        if not self._validate_warehouse_selected():
            return
        # مزامنة أخيرة قبل الترحيل مباشرة — يحمي من تعديل الـComboBox
        # بعد آخر حفظ مسودة دون إعادة حفظها
        self.invoice.warehouse_id = self._selected_warehouse_id()

        confirm = QMessageBox.question(
            self, "تأكيد الترحيل",
            f"هل تريد ترحيل {self.doc_title} رقم {self.invoice.invoice_no}؟\n"
            "لا يمكن التراجع عن هذا الإجراء إلا بعكسه لاحقاً.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            is_cash = self.is_cash_combo.currentText() == "نقدي"
            self.posting_fn(self.session, self.invoice, is_cash=is_cash)
            self.session.commit()
        except (EditNotAllowedError, InvoiceValidationError, Exception) as e:
            self.session.rollback()
            QMessageBox.critical(self, "تعذّر الترحيل", str(e))
            return
        QMessageBox.information(self, "تم", f"تم ترحيل {self.doc_title} بنجاح")
        self._load_invoice()
