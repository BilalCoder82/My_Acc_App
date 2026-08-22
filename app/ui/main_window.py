"""
Main Window — نطاق v1 المعتمد (راجع UI_DESIGN.md)
======================================================
Navigation بسيطة (بدون Section Map منفصل) + Workspace Tabs.
عربي RTL ثابت — بدون نظام ترجمة. النصوص مفصولة كثوابت بأعلى الملف
لتسهيل استخراجها لاحقاً لو احتجنا ترجمة فعلية (بدون بناء النظام الآن).

قاعدة صارمة: هذا الملف لا يحتوي أي منطق محاسبي ولا استعلام SQLAlchemy
مباشر — فقط بناء واجهة واستدعاء app/services/ و app/db.py.
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QMenuBar, QStatusBar, QWidget, QVBoxLayout, QLabel
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from sqlalchemy.orm import Session


# نصوص الواجهة — منفصلة هنا عمداً (سهولة استخراج لاحق للترجمة، بدون نظام ترجمة فعلي الآن)
LABELS = {
    "app_title": "نظام محاسبة سطح مكتب",
    "home": "الرئيسية",
    "accounting": "المحاسبة",
    "sales": "المبيعات",
    "purchases": "المشتريات",
    "inventory": "المخزون",
    "reports": "التقارير",
    "settings": "الإعدادات",
    "coa": "دليل الحسابات",
    "journal_vouchers": "سندات القيد",
    "ledger": "دفتر الأستاذ",
    "trial_balance": "ميزان المراجعة",
    "closing_accounts": "الحسابات الختامية",
    "sales_invoices": "فواتير البيع",
    "sales_returns": "مرتجعات البيع",
    "purchase_invoices": "فواتير الشراء",
    "purchase_returns": "مرتجعات الشراء",
    "items": "دليل المواد",
    "warehouses": "المستودعات",
    "stock_transfer": "تحويل مخزني",
    "ready": "جاهز",
}


class MainWindow(QMainWindow):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session  # جلسة قاعدة بيانات العميل الحالي المفتوح

        self.setWindowTitle(LABELS["app_title"])
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1280, 800)

        self._build_menu()
        self._build_workspace()
        self._build_status_bar()

    # -- بناء القائمة العلوية (Navigation) ----------------------------------
    def _build_menu(self) -> None:
        menu_bar: QMenuBar = self.menuBar()

        home_action = QAction(LABELS["home"], self)
        home_action.triggered.connect(self._open_dashboard)
        menu_bar.addAction(home_action)

        accounting_menu = menu_bar.addMenu(LABELS["accounting"])
        self._add_menu_item(accounting_menu, LABELS["coa"], self._open_chart_of_accounts)
        self._add_menu_item(accounting_menu, LABELS["journal_vouchers"], self._open_not_implemented)
        self._add_menu_item(accounting_menu, LABELS["ledger"], self._open_not_implemented)
        self._add_menu_item(accounting_menu, LABELS["trial_balance"], self._open_not_implemented)
        self._add_menu_item(accounting_menu, LABELS["closing_accounts"], self._open_not_implemented)

        sales_menu = menu_bar.addMenu(LABELS["sales"])
        self._add_menu_item(sales_menu, LABELS["sales_invoices"], self._open_sales_invoice_list)
        self._add_menu_item(sales_menu, LABELS["sales_returns"], self._open_not_implemented)

        purchases_menu = menu_bar.addMenu(LABELS["purchases"])
        self._add_menu_item(purchases_menu, LABELS["purchase_invoices"], self._open_not_implemented)
        self._add_menu_item(purchases_menu, LABELS["purchase_returns"], self._open_not_implemented)

        inventory_menu = menu_bar.addMenu(LABELS["inventory"])
        self._add_menu_item(inventory_menu, LABELS["items"], self._open_not_implemented)
        self._add_menu_item(inventory_menu, LABELS["warehouses"], self._open_not_implemented)
        self._add_menu_item(inventory_menu, LABELS["stock_transfer"], self._open_not_implemented)

        reports_menu = menu_bar.addMenu(LABELS["reports"])
        self._add_menu_item(reports_menu, LABELS["trial_balance"], self._open_not_implemented)

        settings_action = QAction(LABELS["settings"], self)
        settings_action.triggered.connect(self._open_not_implemented)
        menu_bar.addAction(settings_action)

    @staticmethod
    def _add_menu_item(menu, text: str, handler) -> None:
        action = QAction(text, menu)
        action.triggered.connect(handler)
        menu.addAction(action)

    # -- Workspace Tabs -------------------------------------------------------
    def _build_workspace(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.setCentralWidget(self.tabs)

    def _open_tab(self, title: str, widget: QWidget) -> None:
        """يفتح تاب جديد، أو يبدّل لتاب موجود بنفس العنوان بدل التكرار."""
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == title:
                self.tabs.setCurrentIndex(i)
                return
        index = self.tabs.addTab(widget, title)
        self.tabs.setCurrentIndex(index)

    def _close_tab(self, index: int) -> None:
        self.tabs.removeTab(index)

    def _build_status_bar(self) -> None:
        bar: QStatusBar = self.statusBar()
        bar.showMessage(LABELS["ready"])

    # -- معالجات فتح الشاشات ----------------------------------------------
    def _open_dashboard(self) -> None:
        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        layout.addWidget(QLabel(f"مرحباً — {LABELS['app_title']}"))
        self._open_tab(LABELS["home"], placeholder)

    def _open_chart_of_accounts(self) -> None:
        from app.ui.accounting.chart_of_accounts_view import ChartOfAccountsView
        self._open_tab(LABELS["coa"], ChartOfAccountsView(self.session))

    def _open_sales_invoice_list(self) -> None:
        from app.ui.sales.invoice_list import SalesInvoiceListView
        view = SalesInvoiceListView(self.session)
        view.invoice_opened.connect(self._open_sales_invoice_form)
        self._open_tab(LABELS["sales_invoices"], view)

    def _open_sales_invoice_form(self, invoice_id: int | None, title: str) -> None:
        from app.ui.sales.invoice_form import SalesInvoiceFormView
        self._open_tab(title, SalesInvoiceFormView(self.session, invoice_id=invoice_id))

    def _open_not_implemented(self) -> None:
        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        layout.addWidget(QLabel("هذه الشاشة لم تُبنَ بعد — قيد التطوير"))
        self._open_tab("قريباً", placeholder)
