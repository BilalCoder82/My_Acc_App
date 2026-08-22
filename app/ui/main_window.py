"""
Main Window — نطاق v1 المعتمد (مُحسَّن v2)
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QMenuBar, QStatusBar, QWidget, QVBoxLayout, QLabel
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont
from sqlalchemy.orm import Session


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
        self.session = session

        self.setWindowTitle(LABELS["app_title"])
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1400, 900)
        self.setStyleSheet("background-color: #F5F7FA;")

        self._build_menu()
        self._build_workspace()
        self._build_status_bar()

    def _build_menu(self) -> None:
        menu_bar: QMenuBar = self.menuBar()
        menu_bar.setStyleSheet(
            "QMenuBar { background: #1E3A5F; color: white; padding: 4px; font-size: 12px; }"
            "QMenuBar::item:selected { background: #2563EB; }"
            "QMenu { background: white; border: 1px solid #E5E7EB; }"
            "QMenu::item { padding: 6px 16px; }"
            "QMenu::item:selected { background: #DBEAFE; color: #1E40AF; }"
        )

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

    def _build_workspace(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setStyleSheet(
            "QTabWidget::pane { border: none; background: #F5F7FA; }"
            "QTabBar::tab { background: #E5E7EB; padding: 8px 16px; "
            "border-top-left-radius: 6px; border-top-right-radius: 6px; "
            "font-size: 12px; color: #4B5563; }"
            "QTabBar::tab:selected { background: white; color: #1E40AF; "
            "font-weight: bold; border-top: 2px solid #2563EB; }"
            "QTabBar::tab:!selected { margin-top: 2px; }"
        )
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.setCentralWidget(self.tabs)

    def _open_tab(self, title: str, widget: QWidget) -> None:
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
        bar.setStyleSheet(
            "QStatusBar { background: #1E3A5F; color: white; padding: 4px 12px; "
            "font-size: 11px; }"
        )
        bar.showMessage(LABELS["ready"])

    def _open_dashboard(self) -> None:
        placeholder = QWidget()
        placeholder.setStyleSheet("background: #F5F7FA;")
        layout = QVBoxLayout(placeholder)
        title = QLabel(f"مرحباً — {LABELS['app_title']}")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #1E3A5F;")
        title.setAlignment(Qt.AlignCenter)
        layout.addStretch()
        layout.addWidget(title)
        layout.addStretch()
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
        placeholder.setStyleSheet("background: #F5F7FA;")
        layout = QVBoxLayout(placeholder)
        lbl = QLabel("هذه الشاشة لم تُبنَ بعد — قيد التطوير")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("color: #9CA3AF; font-size: 14px;")
        layout.addStretch()
        layout.addWidget(lbl)
        layout.addStretch()
        self._open_tab("قريباً", placeholder)