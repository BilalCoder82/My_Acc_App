"""
بطاقة الحساب — نافذة منبثقة (QDialog) بدل لوحة ثابتة بجانب الشجرة، بالضبط
كما اتُّفق: الشجرة هي المساحة الرئيسية، والتفاصيل تظهر فقط عند الحاجة.

نفس مبدأ سند القيد: هذه الواجهة لا تتحقق من شيء بنفسها ولا تلمس session
مباشرة إلا بالحفظ النهائي عبر app/services/account_edit.py — كل القواعد
المحاسبية (تكرار الكود، الدورات بالشجرة، حماية الحسابات ذات الحركات) هناك.
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QComboBox,
    QCheckBox, QLabel, QPushButton, QMessageBox, QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from sqlalchemy.orm import Session

from app.models import Account, AccountType, AccountSubtype
from app.services.account_edit import create_account, update_account, AccountEditError, VALID_CURRENCIES
from app.reports.rollup import get_account_balance, DEBIT_NORMAL_TYPES

TYPE_LABELS = {
    AccountType.ASSET: "أصول",
    AccountType.LIABILITY: "التزامات",
    AccountType.EQUITY: "حقوق ملكية",
    AccountType.REVENUE: "إيرادات",
    AccountType.EXPENSE: "مصروفات",
}
TYPE_LABELS_REV = {v: k for k, v in TYPE_LABELS.items()}

# §56 (مراجعة Bilal): تصنيف عمل صريح مستقل عن account_type — "رقم
# الحساب ليس Business Rule"، والتصنيف يُحفَظ صراحة لا يُستنتَج.
SUBTYPE_LABELS = {
    AccountSubtype.GENERAL: "عام",
    AccountSubtype.CUSTOMER: "عميل",
    AccountSubtype.SUPPLIER: "مورد",
    AccountSubtype.CASH: "صندوق",
    AccountSubtype.BANK: "بنك",
    AccountSubtype.EXPENSE: "مصروف",
    AccountSubtype.INCOME: "إيراد",
    AccountSubtype.OTHER: "أخرى",
}

COLOR_BG = "#F5F7FA"


class AccountCardDialog(QDialog):
    def __init__(
        self, session: Session, account: Account | None = None,
        default_parent: Account | None = None, parent=None,
    ):
        super().__init__(parent)
        self.session = session
        self.account = account  # None = إنشاء حساب جديد
        self.saved_account: Account | None = None  # يقرأه المستدعي بعد accept() لتحديث الشجرة

        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("بطاقة الحساب" if account else "حساب جديد")
        self.setMinimumWidth(420)
        self.setStyleSheet(f"background-color: {COLOR_BG};")

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 18, 20, 18)

        title = QLabel("بطاقة الحساب" if account else "حساب جديد")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        def field_style(w):
            w.setStyleSheet(
                "padding: 6px 8px; border: 1px solid #D1D5DB; border-radius: 5px; "
                "background: white; font-size: 12px;"
            )
            return w

        r = 0
        form.addWidget(QLabel("رمز الحساب"), r, 1)
        self.code_edit = field_style(QLineEdit(account.code if account else ""))
        form.addWidget(self.code_edit, r, 0)
        r += 1

        form.addWidget(QLabel("اسم الحساب"), r, 1)
        self.name_edit = field_style(QLineEdit(account.name_ar if account else ""))
        form.addWidget(self.name_edit, r, 0)
        r += 1

        form.addWidget(QLabel("نوع الحساب"), r, 1)
        self.type_combo = field_style(QComboBox())
        self.type_combo.setLayoutDirection(Qt.RightToLeft)
        for t in AccountType:
            self.type_combo.addItem(TYPE_LABELS[t], t)
        if account:
            self.type_combo.setCurrentText(TYPE_LABELS[account.account_type])
        self.type_combo.currentIndexChanged.connect(self._refresh_natural_balance)
        form.addWidget(self.type_combo, r, 0)
        r += 1

        form.addWidget(QLabel("الحساب الأب"), r, 1)
        self.parent_combo = field_style(QComboBox())
        self.parent_combo.setLayoutDirection(Qt.RightToLeft)
        self._populate_parent_combo(default_parent)
        form.addWidget(self.parent_combo, r, 0)
        r += 1

        form.addWidget(QLabel("العملة"), r, 1)
        self.currency_combo = field_style(QComboBox())
        self.currency_combo.setLayoutDirection(Qt.RightToLeft)
        self.currency_combo.addItems(sorted(VALID_CURRENCIES))
        self.currency_combo.setCurrentText(account.currency_code if account else "SYP")
        form.addWidget(self.currency_combo, r, 0)
        r += 1

        form.addWidget(QLabel("طبيعة الحساب"), r, 1)
        self.natural_balance_label = QLabel()
        self.natural_balance_label.setStyleSheet("color: #374151; font-weight: bold;")
        form.addWidget(self.natural_balance_label, r, 0)
        r += 1

        # §56: تصنيف العمل الفرعي — منفصل تماماً عن "نوع الحساب"
        # (account_type) أعلاه. لا استنتاج تلقائي هنا (المستخدم يختار
        # صراحة)، ولا تفعيل تلقائي للتسوية بمجرد اختيار عميل/مورد —
        # allow_reconciliation خانة مستقلة تماماً بالأسفل.
        form.addWidget(QLabel("نوع الحساب الفرعي"), r, 1)
        self.subtype_combo = field_style(QComboBox())
        self.subtype_combo.setLayoutDirection(Qt.RightToLeft)
        for st in AccountSubtype:
            self.subtype_combo.addItem(SUBTYPE_LABELS[st], st)
        if account:
            self.subtype_combo.setCurrentText(SUBTYPE_LABELS[account.subtype])
        form.addWidget(self.subtype_combo, r, 0)
        r += 1

        layout.addLayout(form)

        checks_row = QHBoxLayout()
        self.is_group_check = QCheckBox("حساب تجميعي (لا يقبل قيوداً مباشرة)")
        self.is_group_check.setChecked(bool(account.is_group) if account else False)
        checks_row.addWidget(self.is_group_check)
        self.is_active_check = QCheckBox("الحساب نشط")
        self.is_active_check.setChecked(bool(account.is_active) if account else True)
        checks_row.addWidget(self.is_active_check)
        # §56: allow_reconciliation خانة صريحة مستقلة — القاعدة التي
        # تتحقق منها الخدمة فعلياً (settlements.py)، لا subtype ولا
        # account_type ولا رقم الحساب. لا تُفعَّل تلقائياً حتى لو
        # subtype=CUSTOMER/SUPPLIER — قرار صريح لكل حساب على حدة.
        self.allow_reconciliation_check = QCheckBox("يسمح بتسوية الفواتير")
        self.allow_reconciliation_check.setChecked(bool(account.allow_reconciliation) if account else False)
        checks_row.addWidget(self.allow_reconciliation_check)
        layout.addLayout(checks_row)

        self._refresh_natural_balance()

        if account is not None:
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet("color: #E5E7EB;")
            layout.addWidget(sep)

            balance = get_account_balance(session, account)
            balance_row = QHBoxLayout()
            balance_row.addWidget(QLabel("الرصيد الحالي:"))
            balance_label = QLabel(f"{balance:,.2f} {account.currency_code}")
            bf = QFont()
            bf.setBold(True)
            balance_label.setFont(bf)
            balance_row.addWidget(balance_label)
            balance_row.addStretch()
            layout.addLayout(balance_row)

        buttons_row = QHBoxLayout()
        save_btn = QPushButton("حفظ")
        save_btn.setStyleSheet(
            "background: #2563EB; color: white; padding: 8px 20px; border-radius: 5px; font-weight: bold;"
        )
        save_btn.clicked.connect(self._save)
        buttons_row.addWidget(save_btn)

        statement_btn = QPushButton("كشف الحساب")
        statement_btn.setStyleSheet("padding: 8px 20px; border-radius: 5px; border: 1px solid #D1D5DB; background: white;")
        statement_btn.setEnabled(account is not None)
        statement_btn.clicked.connect(self._open_statement)
        buttons_row.addWidget(statement_btn)

        buttons_row.addStretch()

        cancel_btn = QPushButton("إلغاء")
        cancel_btn.setStyleSheet("padding: 8px 20px; border-radius: 5px; border: 1px solid #D1D5DB; background: white;")
        cancel_btn.clicked.connect(self.reject)
        buttons_row.addWidget(cancel_btn)

        layout.addLayout(buttons_row)

    # -- مساعدة --------------------------------------------------------------
    def _populate_parent_combo(self, default_parent: Account | None) -> None:
        """يستبعد الحساب نفسه وكل أحفاده — اختيار أيٍّ منهم كأب ينشئ دورة
        بالشجرة (نفس القاعدة مُطبَّقة أيضاً بـaccount_edit.py دفاعياً)."""
        excluded_ids: set[int] = set()
        if self.account is not None:
            excluded_ids.add(self.account.id)
            excluded_ids |= self._descendant_ids(self.account)

        self.parent_combo.addItem("— بلا (حساب رئيسي) —", None)
        accounts = self.session.query(Account).order_by(Account.code).all()
        selected_index = 0
        current_parent_id = (
            self.account.parent_id if self.account is not None
            else (default_parent.id if default_parent is not None else None)
        )
        for acc in accounts:
            if acc.id in excluded_ids:
                continue
            self.parent_combo.addItem(f"{acc.code} — {acc.name_ar}", acc.id)
            if acc.id == current_parent_id:
                selected_index = self.parent_combo.count() - 1
        self.parent_combo.setCurrentIndex(selected_index)

    def _descendant_ids(self, account: Account) -> set[int]:
        ids: set[int] = set()
        children = self.session.query(Account).filter_by(parent_id=account.id).all()
        for child in children:
            ids.add(child.id)
            ids |= self._descendant_ids(child)
        return ids

    def _refresh_natural_balance(self) -> None:
        account_type = self.type_combo.currentData()
        if account_type in DEBIT_NORMAL_TYPES:
            self.natural_balance_label.setText("مدين")
        else:
            self.natural_balance_label.setText("دائن")

    # -- أزرار -----------------------------------------------------------------
    def _save(self) -> None:
        account_type = self.type_combo.currentData()
        parent_id = self.parent_combo.currentData()
        kwargs = dict(
            code=self.code_edit.text(), name_ar=self.name_edit.text(), account_type=account_type,
            parent_id=parent_id, currency_code=self.currency_combo.currentText(),
            is_group=self.is_group_check.isChecked(), is_active=self.is_active_check.isChecked(),
            subtype=self.subtype_combo.currentData(),
            allow_reconciliation=self.allow_reconciliation_check.isChecked(),
        )
        try:
            if self.account is None:
                self.saved_account = create_account(self.session, **kwargs)
            else:
                self.saved_account = update_account(self.session, self.account, **kwargs)
            self.session.commit()
        except AccountEditError as e:
            self.session.rollback()
            QMessageBox.warning(self, "تعذّر الحفظ", str(e))
            return
        self.accept()

    def _open_statement(self) -> None:
        if self.account is None:
            return
        from app.ui.accounting.account_statement_dialog import AccountStatementDialog
        dlg = AccountStatementDialog(self.session, self.account, parent=self)
        dlg.exec()
