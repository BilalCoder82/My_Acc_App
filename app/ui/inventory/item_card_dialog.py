"""
بطاقة المادة — نافذة منبثقة، نفس فلسفة بطاقة الحساب بالضبط.

4 أقسام بلا تبويبات (بالضبط كما اتُّفق بمراجعة التصميم): معلومات أساسية،
المخزون والتكلفة (عرض فقط، مشتق حياً — لا حقل مخزَّن إطلاقاً)، الحسابات
المحاسبية، إعدادات المخزون.
"""

from __future__ import annotations
from decimal import Decimal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QComboBox,
    QCheckBox, QLabel, QPushButton, QMessageBox, QFrame, QDoubleSpinBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from sqlalchemy.orm import Session

from app.models import Item, Account, AccountType, CostMethod
from app.services.item_edit import create_item, update_item, ItemEditError
from app.services.item_queries import get_item_stock_summary
from app.services.account_queries import list_postable_accounts

COST_METHOD_LABELS = {CostMethod.AVERAGE: "متوسط مرجّح (Average)", CostMethod.FIFO: "FIFO — غير مُنفَّذ بعد"}
COLOR_BG = "#F5F7FA"


def _account_combo(accounts: list[Account], selected_id: int | None, allow_none: bool) -> QComboBox:
    combo = QComboBox()
    combo.setLayoutDirection(Qt.RightToLeft)
    combo.setStyleSheet(
        "padding: 6px 8px; border: 1px solid #D1D5DB; border-radius: 5px; background: white; font-size: 12px;"
    )
    if allow_none:
        combo.addItem("— بلا —", None)
    selected_index = 0
    for acc in accounts:
        combo.addItem(f"{acc.code} — {acc.name_ar}", acc.id)
        if acc.id == selected_id:
            selected_index = combo.count() - 1
    combo.setCurrentIndex(selected_index)
    return combo


class ItemCardDialog(QDialog):
    def __init__(self, session: Session, item: Item | None = None, parent=None):
        super().__init__(parent)
        self.session = session
        self.item = item  # None = مادة جديدة
        self.saved_item: Item | None = None

        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("بطاقة المادة" if item else "مادة جديدة")
        self.setMinimumWidth(460)
        self.setStyleSheet(f"background-color: {COLOR_BG};")

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 18, 20, 18)

        title = QLabel("بطاقة المادة" if item else "مادة جديدة")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        def field_style(w):
            w.setStyleSheet(
                "padding: 6px 8px; border: 1px solid #D1D5DB; border-radius: 5px; "
                "background: white; font-size: 12px;"
            )
            return w

        def section_label(text: str) -> QLabel:
            lbl = QLabel(text)
            f = QFont()
            f.setBold(True)
            f.setPointSize(11)
            lbl.setFont(f)
            lbl.setStyleSheet("color: #1F2937; margin-top: 4px;")
            return lbl

        # -- 1. معلومات أساسية -------------------------------------------------
        layout.addWidget(section_label("معلومات أساسية"))
        basic = QGridLayout()
        basic.setHorizontalSpacing(10)
        basic.setVerticalSpacing(8)
        r = 0
        basic.addWidget(QLabel("كود المادة"), r, 1)
        self.sku_edit = field_style(QLineEdit(item.sku if item else ""))
        basic.addWidget(self.sku_edit, r, 0)
        r += 1
        basic.addWidget(QLabel("اسم المادة"), r, 1)
        self.name_edit = field_style(QLineEdit(item.name_ar if item else ""))
        basic.addWidget(self.name_edit, r, 0)
        r += 1
        # الوحدة: نص حر مقصود (v1) — راجع WORKFLOW.md §25، ليست قراراً نهائياً
        basic.addWidget(QLabel("الوحدة"), r, 1)
        self.unit_edit = field_style(QLineEdit(item.unit if item else "قطعة"))
        basic.addWidget(self.unit_edit, r, 0)
        r += 1
        # التصنيف: نص حر مؤقت (v1) — سيُستبدل بـItemCategory لاحقاً، راجع WORKFLOW.md §25
        basic.addWidget(QLabel("التصنيف"), r, 1)
        self.category_edit = field_style(QLineEdit(item.category if (item and item.category) else ""))
        basic.addWidget(self.category_edit, r, 0)
        r += 1
        self.is_active_check = QCheckBox("المادة نشطة")
        self.is_active_check.setChecked(bool(item.is_active) if item else True)
        basic.addWidget(self.is_active_check, r, 0, 1, 2)
        layout.addLayout(basic)

        # -- 2. المخزون والتكلفة (عرض فقط، لمادة موجودة فقط) --------------------
        if item is not None:
            sep1 = QFrame()
            sep1.setFrameShape(QFrame.HLine)
            sep1.setStyleSheet("color: #E5E7EB;")
            layout.addWidget(sep1)
            layout.addWidget(section_label("المخزون والتكلفة"))
            summary = get_item_stock_summary(session, item.id)
            stock_row = QGridLayout()
            stock_row.addWidget(QLabel("الكمية الحالية:"), 0, 1)
            stock_row.addWidget(QLabel(f"{summary.quantity:,.3f} {item.unit}"), 0, 0)
            stock_row.addWidget(QLabel("متوسط التكلفة:"), 1, 1)
            stock_row.addWidget(QLabel(f"{summary.average_cost:,.4f}"), 1, 0)
            stock_row.addWidget(QLabel("قيمة المخزون:"), 2, 1)
            value_label = QLabel(f"{summary.inventory_value:,.2f}")
            bf = QFont()
            bf.setBold(True)
            value_label.setFont(bf)
            stock_row.addWidget(value_label, 2, 0)
            layout.addLayout(stock_row)

        # -- 3. الحسابات المحاسبية --------------------------------------------
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #E5E7EB;")
        layout.addWidget(sep2)
        layout.addWidget(section_label("الحسابات المحاسبية"))
        accounts_grid = QGridLayout()
        accounts_grid.setHorizontalSpacing(10)
        accounts_grid.setVerticalSpacing(8)

        all_postable = list_postable_accounts(session)
        asset_accounts = [a for a in all_postable if a.account_type == AccountType.ASSET]
        revenue_accounts = [a for a in all_postable if a.account_type == AccountType.REVENUE]
        expense_accounts = [a for a in all_postable if a.account_type == AccountType.EXPENSE]

        r = 0
        accounts_grid.addWidget(QLabel("حساب المخزون *"), r, 1)
        self.inventory_account_combo = _account_combo(
            asset_accounts, item.inventory_account_id if item else None, allow_none=False
        )
        accounts_grid.addWidget(self.inventory_account_combo, r, 0)
        r += 1
        accounts_grid.addWidget(QLabel("حساب المبيعات"), r, 1)
        self.sales_account_combo = _account_combo(
            revenue_accounts, item.sales_account_id if item else None, allow_none=True
        )
        accounts_grid.addWidget(self.sales_account_combo, r, 0)
        r += 1
        accounts_grid.addWidget(QLabel("حساب تكلفة المبيعات *"), r, 1)
        self.cogs_account_combo = _account_combo(
            expense_accounts, item.cogs_account_id if item else None, allow_none=False
        )
        accounts_grid.addWidget(self.cogs_account_combo, r, 0)
        layout.addLayout(accounts_grid)

        note = QLabel(
            "* إلزاميان — يُستخدَمان مباشرة عند ترحيل فواتير هذه المادة.\n"
            "حساب المبيعات: لو حُدِّد يُستخدَم مباشرة بقيود بيع هذه المادة؛ لو تُرِك فارغاً يُستخدَم إعداد المبيعات العام كاحتياطي."
        )
        note.setStyleSheet("color: #6B7280; font-size: 11px;")
        note.setWordWrap(True)
        layout.addWidget(note)

        # -- 4. إعدادات المخزون ------------------------------------------------
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        sep3.setStyleSheet("color: #E5E7EB;")
        layout.addWidget(sep3)
        layout.addWidget(section_label("إعدادات المخزون"))
        settings_grid = QGridLayout()
        settings_grid.setHorizontalSpacing(10)
        settings_grid.setVerticalSpacing(8)

        settings_grid.addWidget(QLabel("حد إعادة الطلب"), 0, 1)
        self.reorder_spin = field_style(QDoubleSpinBox())
        self.reorder_spin.setRange(0, 10 ** 9)
        self.reorder_spin.setDecimals(3)
        self.reorder_spin.setValue(float(item.reorder_point) if item else 0)
        settings_grid.addWidget(self.reorder_spin, 0, 0)

        settings_grid.addWidget(QLabel("طريقة التكلفة"), 1, 1)
        self.cost_method_combo = field_style(QComboBox())
        self.cost_method_combo.setLayoutDirection(Qt.RightToLeft)
        for cm in CostMethod:
            self.cost_method_combo.addItem(COST_METHOD_LABELS[cm], cm)
        self.cost_method_combo.setCurrentIndex(0)  # Average فقط — راجع item_edit.py
        # FIFO مُعطَّل بالواجهة عمداً (index 1) — غير مُنفَّذ بمحرّك الترحيل
        self.cost_method_combo.model().item(1).setEnabled(False)
        if item and item.cost_method == CostMethod.FIFO:
            self.cost_method_combo.setCurrentIndex(1)
        settings_grid.addWidget(self.cost_method_combo, 1, 0)
        layout.addLayout(settings_grid)

        if item is not None and len(item.movements) > 0:
            lock_note = QLabel("⚠ لهذه المادة حركات مخزون مسجَّلة — طريقة التكلفة والحسابات المحاسبية مقفلة عن التعديل.")
            lock_note.setStyleSheet("color: #B45309; font-size: 11px;")
            lock_note.setWordWrap(True)
            layout.addWidget(lock_note)
            self.cost_method_combo.setEnabled(False)
            self.inventory_account_combo.setEnabled(False)
            self.sales_account_combo.setEnabled(False)
            self.cogs_account_combo.setEnabled(False)

        # -- الأزرار -------------------------------------------------------------
        buttons_row = QHBoxLayout()
        save_btn = QPushButton("حفظ")
        save_btn.setStyleSheet(
            "background: #2563EB; color: white; padding: 8px 20px; border-radius: 5px; font-weight: bold;"
        )
        save_btn.clicked.connect(self._save)
        buttons_row.addWidget(save_btn)
        buttons_row.addStretch()
        cancel_btn = QPushButton("إلغاء")
        cancel_btn.setStyleSheet("padding: 8px 20px; border-radius: 5px; border: 1px solid #D1D5DB; background: white;")
        cancel_btn.clicked.connect(self.reject)
        buttons_row.addWidget(cancel_btn)
        layout.addLayout(buttons_row)

    def _save(self) -> None:
        kwargs = dict(
            sku=self.sku_edit.text(), name_ar=self.name_edit.text(), unit=self.unit_edit.text(),
            category=self.category_edit.text(), cost_method=self.cost_method_combo.currentData(),
            reorder_point=Decimal(str(self.reorder_spin.value())),
            inventory_account_id=self.inventory_account_combo.currentData(),
            sales_account_id=self.sales_account_combo.currentData(),
            cogs_account_id=self.cogs_account_combo.currentData(),
            is_active=self.is_active_check.isChecked(),
        )
        try:
            if self.item is None:
                self.saved_item = create_item(self.session, **kwargs)
            else:
                self.saved_item = update_item(self.session, self.item, **kwargs)
            self.session.commit()
        except ItemEditError as e:
            self.session.rollback()
            QMessageBox.warning(self, "تعذّر الحفظ", str(e))
            return
        self.accept()
