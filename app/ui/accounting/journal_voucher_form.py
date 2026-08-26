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
    QComboBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QPushButton, QLabel, QHeaderView,
    QMessageBox, QAbstractItemView, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QDate, QTimer
from PySide6.QtGui import QFont, QShortcut, QKeySequence
from sqlalchemy.orm import Session

from app.models import JournalEntry, JournalEntryStatus
from app.services.journal_edit import add_manual_line, post_manual_entry, JournalEditError
from app.services.account_queries import list_postable_accounts
from app.ui.common.numeric_delegate import NumericGridDelegate, PlainTextGridDelegate, format_currency
from app.ui.common.currency_combo_delegate import CurrencyComboDelegate
from app.services.money import rate as rate_, money as money_

# البيان اختياري لكل سطر. العملة والمعادل الأساسي وسعر الصرف كلها أعمدة
# قابلة للتحرير يدوياً بالكامل (راجع _derive_base_from_amount_and_rate /
# _derive_rate_from_amount_and_base) — "مدين/دائن بالأساسية" ليسا للعرض
# فقط، بل يمكن تعديلهما مباشرة والنظام يستنتج سعر الصرف المطابق تلقائياً.
#
# العملة وسعر الصرف الافتراضيان بالرأس (default_currency_combo /
# default_exchange_rate_spin) يُطبَّقان تلقائياً على أي سطر جديد فور
# إنشائه (_add_empty_row) — المحاسب غير مضطر لفتح ComboBox العملة يدوياً
# بكل سطر. لو غيّر المحاسب عملة/سعر سطر بعينه يدوياً، يُسجَّل هذا السطر
# بمجموعة "معدَّل يدوياً" (_manual_currency_rows / _manual_rate_rows)
# فلا يُعاد الكتابة فوقه لو غيّر المحاسب لاحقاً القيمة الافتراضية بالرأس.
COLUMNS = [
    "رمز الحساب", "الحساب", "البيان", "العملة", "سعر الصرف",
    "مدين", "دائن", "مدين بالأساسية", "دائن بالأساسية",
]
(COL_CODE, COL_ACCOUNT, COL_DESC, COL_CURRENCY, COL_RATE,
 COL_DEBIT, COL_CREDIT, COL_DEBIT_BASE, COL_CREDIT_BASE) = range(9)

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
        # صفوف غيّر فيها المحاسب العملة/سعر الصرف يدوياً — لا تتبع تغيّر
        # القيمة الافتراضية بالرأس بعد ذلك. راجع _on_default_currency_changed
        # و_on_default_rate_changed.
        self._manual_currency_rows: set[int] = set()
        self._manual_rate_rows: set[int] = set()

        self.setStyleSheet(f"background-color: {COLOR_BG};")
        self._build_ui()
        # يُوصَل بعد بناء الرأس والشبكة معاً — لو وُصِل داخل بناء الرأس
        # نفسه، فإن addItems/setValue الأوليين يُطلقان الإشارة قبل وجود
        # self.grid أصلاً.
        self.default_currency_combo.currentTextChanged.connect(self._on_default_currency_changed)
        self.default_exchange_rate_spin.valueChanged.connect(self._on_default_rate_changed)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save_draft)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._post)

        if self.entry:
            self._load_entry()
        else:
            for _ in range(8):
                self._add_empty_row()
            self._recalculate_totals()

    # -- بناء الواجهة -----------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(self._build_header_and_info())
        layout.addWidget(self._build_grid(), stretch=1)
        layout.addWidget(self._build_summary_bar())
        self._refresh_editability()

    def _build_header_and_info(self) -> QWidget:
        """بطاقة واحدة مضغوطة تجمع العنوان ومعلومات القيد — بدل بطاقتين منفصلتين
        بمساحة بيضاء زايدة بينهما."""
        card = QFrame()
        card.setStyleSheet(CARD_STYLE)
        outer = QVBoxLayout(card)
        outer.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel("سند قيد محاسبي")
        f = QFont(); f.setPointSize(15); f.setBold(True)
        title.setFont(f)
        title.setStyleSheet("color: #111827;")
        self.ref_label = QLabel("جديد (غير محفوظ)")
        self.ref_label.setStyleSheet("color: #6B7280; font-size: 12px;")
        self.status_badge = QLabel()
        self.status_badge.setStyleSheet(
            "border-radius: 10px; padding: 3px 12px; color: white; font-weight: bold; font-size: 12px;"
        )
        title_row.addWidget(title)
        title_row.addWidget(self.status_badge)
        title_row.addWidget(self.ref_label)
        title_row.addStretch()
        outer.addLayout(title_row)

        info_row = QHBoxLayout()
        info_row.setSpacing(16)

        def labeled(text, widget, width=None):
            if width:
                widget.setFixedWidth(width)
            box = QVBoxLayout()
            box.setSpacing(2)
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

        info_row.addWidget(labeled("رقم القيد", self.ref_edit, 140))
        info_row.addWidget(labeled("التاريخ", self.date_edit, 130))
        info_row.addWidget(labeled("البيان", self.description_edit), stretch=1)
        outer.addLayout(info_row)

        # عملة القيد الافتراضية — كل سطر يرثها ما لم يُخصَّص له عملة مختلفة
        # بعمود "العملة" بالجدول (خلط عملات — راجع توثيق journal_edit.py)
        currency_row = QHBoxLayout()
        currency_row.setSpacing(16)
        self.default_currency_combo = QComboBox()
        self.default_currency_combo.addItems(["SYP", "USD", "TRY", "EUR"])
        self.default_exchange_rate_spin = QDoubleSpinBox()
        self.default_exchange_rate_spin.setDecimals(4)
        self.default_exchange_rate_spin.setMaximum(1_000_000)
        self.default_exchange_rate_spin.setValue(1)
        currency_row.addWidget(labeled("العملة الافتراضية", self.default_currency_combo, 120))
        currency_row.addWidget(labeled("سعر الصرف الافتراضي", self.default_exchange_rate_spin, 150))
        currency_row.addStretch()
        outer.addLayout(currency_row)
        return card

    def _build_grid(self) -> QWidget:
        self.grid = QTableWidget(0, len(COLUMNS))
        self.grid.setHorizontalHeaderLabels(COLUMNS)
        self.grid.horizontalHeader().setSectionResizeMode(COL_ACCOUNT, QHeaderView.Stretch)
        fixed_cols = [COL_CODE, COL_DESC, COL_CURRENCY, COL_RATE, COL_DEBIT, COL_DEBIT_BASE, COL_CREDIT, COL_CREDIT_BASE]
        for col in fixed_cols:
            self.grid.horizontalHeader().setSectionResizeMode(col, QHeaderView.Fixed)
        self.grid.setColumnWidth(COL_CODE, 90)
        self.grid.setColumnWidth(COL_DESC, 150)
        self.grid.setColumnWidth(COL_CURRENCY, 80)
        self.grid.setColumnWidth(COL_RATE, 95)
        self.grid.setColumnWidth(COL_DEBIT, 105)
        self.grid.setColumnWidth(COL_CREDIT, 105)
        self.grid.setColumnWidth(COL_DEBIT_BASE, 120)
        self.grid.setColumnWidth(COL_CREDIT_BASE, 120)
        self.grid.horizontalHeader().setFixedHeight(36)
        self.grid.verticalHeader().setDefaultSectionSize(34)
        self.grid.verticalHeader().hide()
        self.grid.setLayoutDirection(Qt.RightToLeft)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectItems)

        # كل خلية قابلة للتحرير بالشبكة — بما فيها رمز الحساب والبيان —
        # تأخذ delegate يمرّر Enter عبر مسار التنقل الموحّد self._move_to_next_cell
        # (راجع شرح "مسار Enter الموحّد" أعلى numeric_delegate.py). قبل هذا
        # كان لعمودي الرمز/البيان معالجة Enter افتراضية من كيوت مختلفة تماماً
        # عن بقية الأعمدة، وهذا التعارض بين مسارين مختلفين لنفس الضغطة هو
        # سبب تحذير الطرفية 'commitData called with an editor that does not
        # belong to this view'.
        self.grid.setItemDelegateForColumn(
            COL_CODE, PlainTextGridDelegate(parent=self.grid, on_return=self._move_to_next_cell)
        )
        self.grid.setItemDelegateForColumn(
            COL_DESC, PlainTextGridDelegate(parent=self.grid, on_return=self._move_to_next_cell)
        )
        self.grid.setItemDelegateForColumn(
            COL_CURRENCY, CurrencyComboDelegate(self.grid, on_return=self._move_to_next_cell)
        )
        # كل الأعمدة الرقمية قابلة للتحرير الآن — بما فيها المعادل الأساسي.
        # العلاقة ديناميكية بالاتجاهين (راجع _derive_base_from_amount_and_rate
        # و_derive_rate_from_amount_and_base): آخر حقل عدّله المستخدم هو
        # مصدر الحقيقة لهذا التغيير، والحقل الآخر (سعر الصرف أو المعادل) يُحسب
        # منه تلقائياً — بدون أي إصلاح تلقائي لفرق حقيقي بين الطرفين.
        for col in [COL_RATE, COL_DEBIT, COL_CREDIT, COL_DEBIT_BASE, COL_CREDIT_BASE]:
            self.grid.setItemDelegateForColumn(
                col,
                NumericGridDelegate(
                    4 if col == COL_RATE else 2, editable=True, parent=self.grid,
                    on_return=self._move_to_next_cell,
                ),
            )

        self.grid.setStyleSheet(
            "QTableWidget { background: white; border: 1px solid #E5E7EB; }"
            "QHeaderView::section { background: #EEF2FF; padding: 6px; border: none; font-weight: bold; font-size: 12px; }"
            "QTableWidget::item { border-bottom: 1px solid #F3F4F6; padding: 4px; }"
        )
        self.grid.itemChanged.connect(self._on_cell_changed)
        self.grid.installEventFilter(self)
        return self.grid

    def _build_summary_bar(self) -> QWidget:
        """شريط ملخص سفلي موحّد — بدل تقسيم الإجماليات والأزرار لصندوقين منفصلين."""
        card = QFrame()
        card.setStyleSheet(CARD_STYLE)
        row = QHBoxLayout(card)
        row.setSpacing(24)

        def summary_item(title: str) -> QLabel:
            box = QVBoxLayout()
            box.setSpacing(2)
            lbl = QLabel(title)
            lbl.setStyleSheet("color: #6B7280; font-size: 11px;")
            value = QLabel("0.00")
            value.setMinimumWidth(140)
            value.setStyleSheet("font-size: 14px; font-weight: bold; color: #111827;")
            box.addWidget(lbl)
            box.addWidget(value)
            container = QWidget()
            container.setLayout(box)
            row.addWidget(container)
            return value

        self.debit_total_label = summary_item("إجمالي المدين")
        self.credit_total_label = summary_item("إجمالي الدائن")

        diff_box = QVBoxLayout()
        diff_box.setSpacing(2)
        diff_title = QLabel("الفرق")
        diff_title.setStyleSheet("color: #6B7280; font-size: 11px;")
        self.diff_label = QLabel("0.00 ✓ متوازن")
        self.diff_label.setStyleSheet(
            "font-weight: bold; font-size: 14px; color: #16A34A; "
            "background-color: #F0FDF4; padding: 4px 12px; border-radius: 6px;"
        )
        diff_box.addWidget(diff_title)
        diff_box.addWidget(self.diff_label)
        diff_container = QWidget()
        diff_container.setLayout(diff_box)
        row.addWidget(diff_container)

        row.addStretch()

        self.save_btn = QPushButton("حفظ مسودة")
        self.save_btn.setStyleSheet(
            "padding: 8px 20px; border: 1px solid #D1D5DB; border-radius: 4px; background: white;"
        )
        self.save_btn.clicked.connect(self._save_draft)

        self.post_btn = QPushButton("ترحيل")
        self.post_btn.clicked.connect(self._post)

        row.addWidget(self.save_btn)
        row.addWidget(self.post_btn)
        return card

    def _apply_post_button_state(self, is_balanced: bool) -> None:
        """تعطيل بصري لزر الترحيل لما القيد غير متوازن — تجربة أفضل من
        السماح بالضغط ثم إظهار رسالة خطأ. الحماية الفعلية تبقى بالـservice
        دائماً بغض النظر عن حالة هذا الزر."""
        if is_balanced:
            self.post_btn.setEnabled(True)
            self.post_btn.setStyleSheet(
                f"background-color: {COLOR_PRIMARY}; color: white; font-weight: bold; "
                "padding: 10px 24px; border-radius: 4px; font-size: 13px;"
            )
        else:
            self.post_btn.setEnabled(False)
            self.post_btn.setStyleSheet(
                "background-color: #D1D5DB; color: #6B7280; font-weight: bold; "
                "padding: 10px 24px; border-radius: 4px; font-size: 13px;"
            )

    # -- تحميل قيد موجود -----------------------------------------------------
    def _load_entry(self) -> None:
        e = self.entry
        self.ref_label.setText(e.ref_no)
        self.ref_edit.setText(e.ref_no)
        self.date_edit.setDate(QDate(e.entry_date.year, e.entry_date.month, e.entry_date.day))
        self.description_edit.setText(e.description or "")
        self.default_currency_combo.setCurrentText(e.currency_code)
        # rate_() ينظّف أي شائبة float متبقية من إعادة القراءة عبر SQLite
        # (راجع توثيق money.py) قبل ما تدخل بأي حساب لاحق بالواجهة
        self.default_exchange_rate_spin.setValue(float(rate_(e.exchange_rate)))

        # صفوف هذا القيد المُعاد تحميله تبدأ بدون أي تعديل يدوي مسجَّل — تُعاد
        # علامة "معدَّل يدوياً" أدناه فقط للأسطر التي فعلاً لها عملة/سعر صرف
        # خاصان محفوظان (line_currency_code/line_exchange_rate)، تمييزاً عن
        # الأسطر التي ورثت عملة القيد الافتراضية وقت الحفظ.
        self._manual_currency_rows = set()
        self._manual_rate_rows = set()

        self.grid.blockSignals(True)
        self._close_current_editor()
        self.grid.setRowCount(0)
        for line in e.lines:
            row = self.grid.rowCount()
            self.grid.insertRow(row)
            self.grid.setItem(row, COL_CODE, QTableWidgetItem(line.account.code))
            self.grid.setItem(row, COL_ACCOUNT, self._readonly_item(line.account.name_ar))
            self.grid.setItem(row, COL_DESC, QTableWidgetItem(""))
            # عملة/سعر خاصان بهذا السطر لو محفوظان صراحة، وإلا نعرض عملة/سعر
            # القيد الافتراضيَين الحاليَين بالرأس (نفس منطق سطر جديد تماماً).
            if line.line_currency_code:
                self.grid.setItem(row, COL_CURRENCY, QTableWidgetItem(line.line_currency_code))
                self._manual_currency_rows.add(row)
            else:
                self.grid.setItem(row, COL_CURRENCY, QTableWidgetItem(self.default_currency_combo.currentText()))
            if line.line_exchange_rate:
                self.grid.setItem(row, COL_RATE, QTableWidgetItem(str(rate_(line.line_exchange_rate))))
                self._manual_rate_rows.add(row)
            else:
                self.grid.setItem(row, COL_RATE, QTableWidgetItem(str(self._default_rate())))
            self.grid.setItem(row, COL_DEBIT, QTableWidgetItem(str(line.debit) if line.debit else ""))
            self.grid.setItem(row, COL_CREDIT, QTableWidgetItem(str(line.credit) if line.credit else ""))
            self.grid.setItem(row, COL_DEBIT_BASE, QTableWidgetItem(str(line.debit_base) if line.debit else ""))
            self.grid.setItem(row, COL_CREDIT_BASE, QTableWidgetItem(str(line.credit_base) if line.credit else ""))
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

    @staticmethod
    def _readonly_item(value) -> QTableWidgetItem:
        item = QTableWidgetItem(str(value) if value else "")
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        return item

    # -- الشبكة --------------------------------------------------------------
    def _add_empty_row(self) -> None:
        """يبني صفاً فارغاً جديداً، ويعبّئ عمودي العملة/سعر الصرف بالقيمة
        الافتراضية بالرأس مباشرة (بدل تركهما فارغين) — حتى لا يُضطر المحاسب
        لفتح ComboBox العملة يدوياً بكل سطر. التعبئة تتم بـblockSignals حتى
        لا تُسجَّل كـ"تعديل يدوي" (راجع _manual_currency_rows/_manual_rate_rows)."""
        self._close_current_editor()
        row = self.grid.rowCount()
        self.grid.insertRow(row)
        # راجع تعليق _set_cell_silently عن سبب استعادة الحالة السابقة بدل
        # تثبيت False — هذه الدالة تُستدعى أحياناً من داخل _load_entry
        # وهو مُغلَق الإشارات أصلاً بحلقة كاملة.
        was_blocked = self.grid.blockSignals(True)
        for col in range(len(COLUMNS)):
            self.grid.setItem(row, col, QTableWidgetItem(""))
        self.grid.setItem(row, COL_ACCOUNT, self._readonly_item(""))
        self.grid.setItem(row, COL_CURRENCY, QTableWidgetItem(self.default_currency_combo.currentText()))
        self.grid.setItem(row, COL_RATE, QTableWidgetItem(str(self._default_rate())))
        self.grid.blockSignals(was_blocked)

    def _row_has_data(self, row: int) -> bool:
        code_item = self.grid.item(row, COL_CODE)
        return bool(code_item and code_item.text().strip())

    def _cell_text(self, row: int, col: int) -> str:
        item = self.grid.item(row, col)
        return (item.text() if item else "").replace(",", "").strip()

    def _set_cell_silently(self, row: int, col: int, text: str) -> None:
        # blockSignals() يُرجع الحالة السابقة — نستعيدها بدل تثبيت False
        # دائماً، لأن هذه الدالة قد تُستدعى من داخل سياق آخر مُغلَق الإشارات
        # أصلاً (مثل _load_entry أو _add_empty_row)، وQObject.blockSignals
        # ليست عدّاداً قابلاً للتداخل (nesting) — تثبيت False بلا شرط كان
        # سيُعيد فتح الإشارات قبل الأوان وسط تلك السياقات.
        was_blocked = self.grid.blockSignals(True)
        item = self._readonly_item(text) if col == COL_ACCOUNT else QTableWidgetItem(text)
        self.grid.setItem(row, col, item)
        self.grid.blockSignals(was_blocked)

    def _default_rate(self) -> Decimal:
        return rate_(Decimal(str(self.default_exchange_rate_spin.value())))

    def _on_default_currency_changed(self, text: str) -> None:
        """تغيّر عملة الرأس الافتراضية → تُطبَّق فوراً على كل الأسطر التي لم
        يخصّها المحاسب بعملة مختلفة يدوياً. لا تُطبَّق هذه القيمة كـ"عملة
        القيد الوحيدة"، فقط كافتراضي — بالضبط كما نوقش: تغيير العملة بالرأس
        لا يعني أن السند كله أصبح بهذه العملة."""
        if not hasattr(self, "grid"):
            return
        for row in range(self.grid.rowCount()):
            if row in self._manual_currency_rows:
                continue
            self._set_cell_silently(row, COL_CURRENCY, text)

    def _on_default_rate_changed(self, _value: float) -> None:
        """نفس مبدأ _on_default_currency_changed لسعر الصرف: Default قابل
        للتغيير الحر من المحاسب بأي سطر (راجع نقاش سعر الصرف — لاحقاً سيأتي
        هذا الافتراضي من إعدادات أسعار الصرف حسب العملة والتاريخ، وليس الآن
        من هذا الحقل فقط، لكن يبقى قابلاً للتعديل اليدوي دوماً بنفس المبدأ)."""
        if not hasattr(self, "grid"):
            return
        rate_text = str(self._default_rate())
        for row in range(self.grid.rowCount()):
            if row in self._manual_rate_rows:
                continue
            self._set_cell_silently(row, COL_RATE, rate_text)
            if self._cell_text(row, COL_DEBIT):
                self._derive_base_from_amount_and_rate(row, COL_DEBIT, COL_DEBIT_BASE)
            if self._cell_text(row, COL_CREDIT):
                self._derive_base_from_amount_and_rate(row, COL_CREDIT, COL_CREDIT_BASE)
        self._recalculate_totals()

    def _derive_base_from_amount_and_rate(self, row: int, amount_col: int, base_col: int) -> None:
        """المبلغ أو سعر الصرف تغيّر → نعيد حساب المعادل الأساسي منهما."""
        amount_text = self._cell_text(row, amount_col)
        if not amount_text:
            self._set_cell_silently(row, base_col, "")
            return
        try:
            amount = Decimal(amount_text)
        except Exception:
            return
        rate_text = self._cell_text(row, COL_RATE)
        effective_rate = rate_(rate_text) if rate_text else self._default_rate()
        self._set_cell_silently(row, base_col, str(money_(amount * effective_rate)))

    def _derive_rate_from_amount_and_base(self, row: int, amount_col: int, base_col: int) -> None:
        """المستخدم عدّل المعادل الأساسي مباشرة → نستنتج سعر الصرف المطابق،
        بدل ما نجبره يحسب القسمة يدوياً (طلب صريح من صديق المستخدم)."""
        amount_text = self._cell_text(row, amount_col)
        base_text = self._cell_text(row, base_col)
        if not amount_text or not base_text:
            return
        try:
            amount = Decimal(amount_text)
            base_value = Decimal(base_text)
            if amount == 0:
                return
            new_rate = rate_(base_value / amount)
        except Exception:
            return
        self._set_cell_silently(row, COL_RATE, str(new_rate))

    def _on_cell_changed(self, changed_item: QTableWidgetItem) -> None:
        row, col = changed_item.row(), changed_item.column()

        # هذه الدالة لا تُستدعى إطلاقاً للتغييرات المُجراة عبر
        # _set_cell_silently (لأنها تُغلِق الإشارات دائماً) — أي وصول
        # لهذه النقطة بعمود العملة/السعر/المعادل يعني المحاسب هو من غيّره
        # مباشرة، فنسجّله كـ"تعديل يدوي" لا يُكتَب فوقه لاحقاً عند تغيّر
        # الافتراضي بالرأس (راجع _on_default_currency_changed/_on_default_rate_changed).
        if col == COL_CURRENCY:
            self._manual_currency_rows.add(row)
        elif col == COL_RATE:
            self._manual_rate_rows.add(row)
        elif col in (COL_DEBIT_BASE, COL_CREDIT_BASE) and changed_item.text().strip():
            # تعديل المعادل الأساسي يدوياً يُنتج سعر صرف مُستنتَج (أدناه) —
            # وهذا بحد ذاته اختيار يدوي لسعر الصرف، فيُسجَّل بنفس المجموعة.
            self._manual_rate_rows.add(row)

        if col == COL_CODE:
            code = changed_item.text().strip()
            match = next((a for a in self._accounts_cache if a.code == code), None)
            if match:
                self._set_cell_silently(row, COL_ACCOUNT, match.name_ar)

        elif col == COL_DEBIT and changed_item.text().strip():
            self._set_cell_silently(row, COL_CREDIT, "")
            self._set_cell_silently(row, COL_CREDIT_BASE, "")
            self._derive_base_from_amount_and_rate(row, COL_DEBIT, COL_DEBIT_BASE)

        elif col == COL_CREDIT and changed_item.text().strip():
            self._set_cell_silently(row, COL_DEBIT, "")
            self._set_cell_silently(row, COL_DEBIT_BASE, "")
            self._derive_base_from_amount_and_rate(row, COL_CREDIT, COL_CREDIT_BASE)

        elif col == COL_RATE:
            # سعر الصرف يخص أي طرف مُدخل بهذا السطر (مدين أو دائن، واحد فقط
            # منهما فعلياً بحكم قاعدة عدم الجمع بنفس السطر)
            self._derive_base_from_amount_and_rate(row, COL_DEBIT, COL_DEBIT_BASE)
            self._derive_base_from_amount_and_rate(row, COL_CREDIT, COL_CREDIT_BASE)

        elif col == COL_DEBIT_BASE and changed_item.text().strip():
            self._derive_rate_from_amount_and_base(row, COL_DEBIT, COL_DEBIT_BASE)

        elif col == COL_CREDIT_BASE and changed_item.text().strip():
            self._derive_rate_from_amount_and_base(row, COL_CREDIT, COL_CREDIT_BASE)

        if row == self.grid.rowCount() - 1 and self._row_has_data(row):
            # مؤجَّل عمداً لتشغيل بعد اكتمال دورة الحدث الحالية بالكامل —
            # إضافة صف (insertRow) بشكل متزامن هنا، أثناء كوننا فعلياً داخل
            # معالجة commitData لمحرِّر خلية لا يزال قيد الإغلاق (خصوصاً عبر
            # مسار Enter الموحّد)، تزيح فهرسة كل الصفوف التالية قبل أن يُنهي
            # كيوت ربط المحرِّر بخليته الأصلية — وهذا هو سبب تحذير الطرفية
            # 'commitData called with an editor that does not belong to this
            # view'. التأجيل بـQTimer.singleShot(0, ...) نمط قياسي بكيوت
            # لتفادي هذا التعارض: يشغّل الدالة بعد أن يحرِّر Qt المحرِّر
            # القديم بالكامل من طابور الأحداث، لا في منتصف معالجته.
            QTimer.singleShot(0, self._add_empty_row)

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
            # فهرسة الصفوف تحت الصف المحذوف تنزاح للأعلى بمقدار 1 — نطابق
            # نفس الإزاحة بمجموعتَي "التعديل اليدوي" حتى لا تبقى مرتبطة
            # بصفوف خاطئة بعد الحذف (راجع _on_default_currency_changed).
            self._manual_currency_rows = {
                r if r < row else r - 1 for r in self._manual_currency_rows if r != row
            }
            self._manual_rate_rows = {
                r if r < row else r - 1 for r in self._manual_rate_rows if r != row
            }
            self._recalculate_totals()

    # -- الحساب الحي -----------------------------------------------------
    def _recalculate_totals(self) -> None:
        """يقرأ فقط — لا يكتب فوق عمودي المعادل، لأنهما صارا قابلين للتحرير
        اليدوي المباشر (`_derive_base_from_amount_and_rate` و
        `_derive_rate_from_amount_and_base` هما المسؤولان الوحيدان عن حساب
        قيمة المعادل أو السعر آلياً، ولا يكتبان فوق تعديل يدوي أحدث).
        الفرق اللحظي دائماً بالعملة الأساسية — لا جمع لعملات مختلفة مباشرة."""
        total_debit_base, total_credit_base = Decimal("0"), Decimal("0")
        for row in range(self.grid.rowCount()):
            if not self._row_has_data(row):
                continue
            try:
                db = Decimal(self._cell_text(row, COL_DEBIT_BASE) or "0")
                cb = Decimal(self._cell_text(row, COL_CREDIT_BASE) or "0")
            except Exception:
                continue
            total_debit_base += db
            total_credit_base += cb

        base_currency = "SYP"  # عملة الشركة الأساسية — التوازن دائماً بها
        self.debit_total_label.setText(format_currency(total_debit_base, base_currency))
        self.credit_total_label.setText(format_currency(total_credit_base, base_currency))
        diff = total_debit_base - total_credit_base
        if diff == 0:
            self.diff_label.setText(f"{diff} ✓ متوازن")
            self.diff_label.setStyleSheet(
                "font-weight: bold; font-size: 14px; color: #16A34A; "
                "background-color: #F0FDF4; padding: 4px 12px; border-radius: 6px;"
            )
        else:
            self.diff_label.setText(f"{diff} ⚠ غير متوازن")
            self.diff_label.setStyleSheet(
                "font-weight: bold; font-size: 14px; color: #DC2626; "
                "background-color: #FEF2F2; padding: 4px 12px; border-radius: 6px;"
            )
        self._apply_post_button_state(diff == 0)

    # -- حفظ وترحيل --------------------------------------------------------
    def _next_ref_no(self) -> str:
        count = self.session.query(JournalEntry).filter(
            JournalEntry.ref_no.like("JV-%")
        ).count()
        return f"JV-{count + 1:06d}"

    def _save_draft(self) -> None:
        default_currency = self.default_currency_combo.currentText()
        default_rate = Decimal(str(self.default_exchange_rate_spin.value()))

        if self.entry is None:
            self.entry = JournalEntry(
                entry_date=self.date_edit.date().toPython(),
                ref_no=self._next_ref_no(),
                description=self.description_edit.text().strip(),
                source_type="manual", currency_code=default_currency, exchange_rate=default_rate,
                status=JournalEntryStatus.DRAFT,
            )
            self.session.add(self.entry)
            self.session.flush()
        else:
            self.entry.description = self.description_edit.text().strip()
            self.entry.currency_code = default_currency
            self.entry.exchange_rate = default_rate
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
            # عملة/سعر خاصان بهذا السطر فقط لو المستخدم فعلاً عبّاهم — وإلا
            # None فيرث السطر عملة القيد الافتراضية (السلوك القديم بدون تغيير)
            line_currency_txt = (self.grid.item(row, COL_CURRENCY).text() or "").strip()
            line_rate_txt = (self.grid.item(row, COL_RATE).text() or "").replace(",", "").strip()
            line_currency_code = line_currency_txt or None
            line_exchange_rate = Decimal(line_rate_txt) if line_rate_txt else None
            try:
                add_manual_line(
                    self.session, self.entry, account_id=match.id,
                    debit=debit_txt or 0, credit=credit_txt or 0,
                    exchange_rate=default_rate,
                    line_currency_code=line_currency_code, line_exchange_rate=line_exchange_rate,
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
