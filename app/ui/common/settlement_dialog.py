"""
app/ui/common/settlement_dialog.py
=====================================
نافذة Settlement منبثقة (Dialog) — تُفتح من زر داخل نموذج الفاتورة
(document_form.py)، ولا تُبنى كتبويب داخل النموذج نفسه (قرار صريح من
Bilal). كل قواعد السماح/الرفض (DRAFT، تجاوز الرصيد، فاتورة نقدية...)
تبقى حصراً في app/services/settlements.py — هذه النافذة تعرض الحالة
وتستدعي الخدمة فقط، لا تكرر أي منطق محاسبي هنا.
"""
from __future__ import annotations
from datetime import date
from decimal import Decimal, InvalidOperation

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QGridLayout, QLabel, QLineEdit, QDoubleSpinBox,
    QComboBox, QPushButton, QMessageBox, QDateEdit, QHBoxLayout, QFrame,
)
from PySide6.QtCore import Qt, QDate
from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceKind, InvoiceStatus, AccountType
from app.services.account_queries import list_postable_accounts
from app.services.settlements import post_receipt, post_payment, get_invoice_balance_due, SettlementError
from app.ui.common.numeric_delegate import format_currency


class SettlementDialog(QDialog):
    """
    تُفتح من زر "تسوية (قبض/دفع)" في document_form.py. عند نجاح التسوية،
    تُغلَق النافذة بـaccept() ليقوم المستدعي (document_form.py) بتحديث
    عرض الرصيد المستحق في نموذج الفاتورة مباشرة — هذه النافذة لا تُحدِّث
    أي شيء خارج نفسها.
    """

    def __init__(self, session: Session, invoice: Invoice, parent=None):
        super().__init__(parent)
        self.session = session
        self.invoice = invoice
        self.setWindowTitle(f"تسوية — {invoice.invoice_no}")
        self.setMinimumWidth(420)
        self.setLayoutDirection(Qt.RightToLeft)
        self._build_ui()

    # -- بناء الواجهة -----------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info_frame = QFrame()
        info_frame.setStyleSheet(
            "QFrame { background: #F5F7FA; border-radius: 6px; padding: 10px; }"
        )
        info_layout = QGridLayout(info_frame)
        info_layout.setVerticalSpacing(6)

        info_layout.addWidget(QLabel("الفاتورة"), 0, 1)
        info_layout.addWidget(QLabel(self.invoice.invoice_no), 0, 0)
        info_layout.addWidget(QLabel("الجهة"), 1, 1)
        info_layout.addWidget(QLabel(self.invoice.party_name), 1, 0)
        info_layout.addWidget(QLabel("العملة"), 2, 1)
        info_layout.addWidget(QLabel(self.invoice.currency_code), 2, 0)

        # الرصيد المستحق يُحسَب ديناميكياً دائماً من الخدمة، لا حقل مخزَّن.
        # get_invoice_balance_due() ترفض SettlementError الآن لأي حالة
        # غير POSTED (قرار §52 بـsettlements.py — invariant مركزي بالخدمة
        # نفسها بعد مراجعة Bilal، لا فحصاً مكرَّراً هنا بالواجهة).
        try:
            balance_due = get_invoice_balance_due(self.session, self.invoice)
            error = None
            if balance_due <= 0:
                error = f"الفاتورة {self.invoice.invoice_no} مُسوَّاة بالكامل — لا رصيد مستحق"
        except SettlementError as e:
            balance_due = Decimal("0")
            error = str(e)

        balance_label = QLabel(format_currency(balance_due, self.invoice.currency_code))
        balance_label.setStyleSheet("font-weight: bold; font-size: 15px; color: #2563EB;")
        info_layout.addWidget(QLabel("الرصيد المستحق"), 3, 1)
        info_layout.addWidget(balance_label, 3, 0)
        layout.addWidget(info_frame)

        if error:
            # مثلاً: فاتورة DRAFT، أو ملغاة، أو نقدية بالكامل — الرفض هنا
            # نفس رفض الخدمة حرفياً، لا صياغة UI جديدة له
            warn = QLabel(error)
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #DC2626; font-weight: bold;")
            layout.addWidget(warn)
            self._form_disabled = True
        else:
            self._form_disabled = False
            self._build_form(layout, balance_due)

        btn_row = QHBoxLayout()
        close_btn = QPushButton("إغلاق")
        close_btn.setStyleSheet(
            "padding: 8px 20px; border: 1px solid #D1D5DB; border-radius: 4px; background: white;"
        )
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        btn_row.addStretch()

        if not self._form_disabled:
            self.confirm_btn = QPushButton("تأكيد التسوية")
            self.confirm_btn.setStyleSheet(
                "background-color: #16A34A; color: white; font-weight: bold; "
                "padding: 8px 24px; border-radius: 4px;"
            )
            self.confirm_btn.clicked.connect(self._confirm)
            btn_row.addWidget(self.confirm_btn)

        layout.addLayout(btn_row)

    def _build_form(self, layout: QVBoxLayout, balance_due: Decimal) -> None:
        form = QGridLayout()
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(10)

        form.addWidget(QLabel("مبلغ التسوية *"), 0, 1)
        self.amount_spin = QDoubleSpinBox()
        self.amount_spin.setDecimals(2)
        self.amount_spin.setMaximum(float(balance_due))
        self.amount_spin.setMinimum(0.01)
        self.amount_spin.setValue(float(balance_due))  # افتراضياً تسوية كاملة، قابل للتعديل لتسوية جزئية
        form.addWidget(self.amount_spin, 0, 0)

        form.addWidget(QLabel("تاريخ التسوية"), 1, 1)
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        form.addWidget(self.date_edit, 1, 0)

        form.addWidget(QLabel("سعر الصرف (يوم التسوية)"), 2, 1)
        self.rate_spin = QDoubleSpinBox()
        self.rate_spin.setDecimals(4)
        self.rate_spin.setMaximum(1_000_000)
        self.rate_spin.setValue(float(self.invoice.exchange_rate))
        form.addWidget(self.rate_spin, 2, 0)

        form.addWidget(QLabel("حساب القبض/الدفع *"), 3, 1)
        all_postable = list_postable_accounts(self.session)
        cash_accounts = [a for a in all_postable if a.account_type == AccountType.ASSET]
        self.cash_account_combo = QComboBox()
        self.cash_account_combo.setLayoutDirection(Qt.RightToLeft)
        for acc in cash_accounts:
            self.cash_account_combo.addItem(f"{acc.code} — {acc.name_ar}", acc.id)
        form.addWidget(self.cash_account_combo, 3, 0)

        layout.addLayout(form)

    # -- تأكيد -----------------------------------------------------------
    def _confirm(self) -> None:
        cash_account_id = self.cash_account_combo.currentData()
        if cash_account_id is None:
            QMessageBox.warning(self, "تنبيه", "اختر حساب القبض/الدفع أولاً")
            return
        try:
            amount = Decimal(str(self.amount_spin.value()))
            rate = Decimal(str(self.rate_spin.value()))
        except InvalidOperation:
            QMessageBox.warning(self, "تنبيه", "قيمة غير صالحة")
            return

        settlement_fn = post_receipt if self.invoice.kind == InvoiceKind.SALES else post_payment
        try:
            settlement_fn(
                self.session, self.invoice, amount, self.date_edit.date().toPython(),
                rate, cash_account_id,
            )
            self.session.commit()
        except SettlementError as e:
            self.session.rollback()
            QMessageBox.critical(self, "تعذّرت التسوية", str(e))
            return
        except Exception as e:
            self.session.rollback()
            QMessageBox.critical(self, "خطأ غير متوقع", str(e))
            return

        QMessageBox.information(self, "تم", "تمت التسوية بنجاح")
        self.accept()
