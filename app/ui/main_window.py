"""
Main Window — نطاق v1، مُحدَّث لنمط النوافذ المنبثقة
==========================================================
تحديث مقصود عن التصميم الأصلي (Workspace Tabs): بناءً على طلب صريح من
المستخدم، كل عنصر بالقوائم الرئيسية (عدا "الرئيسية" نفسها) يفتح كنافذة
منبثقة مستقلة غير حابسة (non-modal)، بدل تبويب داخل نافذة واحدة.

ملاحظة معمارية موثّقة: هذا يخالف مبدأ "لا نفتح نوافذ فوضوية" المتفق عليه
سابقاً مع صديق المستخدم بتصميم الواجهة الأولي — قرار واعٍ من المستخدم،
موثّق هنا صراحة حتى لا يُفهم كتراجع غير مقصود لاحقاً.

كل نافذة تُتتبَّع بعنوانها: فتح نفس الشاشة مرتين يُظهر النافذة الموجودة
(bring to front) بدل فتح نسخة مكررة.
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QMainWindow, QMenuBar, QStatusBar, QWidget, QVBoxLayout, QLabel
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
        self._open_windows: dict[str, QWidget] = {}

        self.setWindowTitle(LABELS["app_title"])
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1400, 900)
        self.setStyleSheet("background-color: #F5F7FA;")

        self._build_menu()
        self._build_dashboard_central()
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
        home_action.triggered.connect(self._show_dashboard)
        menu_bar.addAction(home_action)

        accounting_menu = menu_bar.addMenu(LABELS["accounting"])
        self._add_menu_item(accounting_menu, LABELS["coa"], self._open_chart_of_accounts)
        self._add_menu_item(accounting_menu, LABELS["journal_vouchers"], self._open_not_implemented)
        self._add_menu_item(accounting_menu, LABELS["ledger"], self._open_not_implemented)
        self._add_menu_item(accounting_menu, LABELS["trial_balance"], self._open_not_implemented)
        self._add_menu_item(accounting_menu, LABELS["closing_accounts"], self._open_not_implemented)

        sales_menu = menu_bar.addMenu(LABELS["sales"])
        self._add_menu_item(sales_menu, LABELS["sales_invoices"], self._open_sales_invoice_list)
        self._add_menu_item(sales_menu, LABELS["sales_returns"], self._open_sales_return_list)

        purchases_menu = menu_bar.addMenu(LABELS["purchases"])
        self._add_menu_item(purchases_menu, LABELS["purchase_invoices"], self._open_purchase_invoice_list)
        self._add_menu_item(purchases_menu, LABELS["purchase_returns"], self._open_purchase_return_list)

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

    # -- الشاشة الرئيسية (تبقى داخل النافذة الأم، ليست منبثقة) -----------------
    def _build_dashboard_central(self) -> None:
        central = QWidget()
        central.setStyleSheet("background: #F5F7FA;")
        layout = QVBoxLayout(central)
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
        self.setCentralWidget(central)

    def _show_dashboard(self) -> None:
        self._build_dashboard_central()

    def _build_status_bar(self) -> None:
        bar: QStatusBar = self.statusBar()
        bar.setStyleSheet(
            "QStatusBar { background: #1E3A5F; color: white; padding: 4px 12px; "
            "font-size: 11px; }"
        )
        bar.showMessage(LABELS["ready"])

    # -- النوافذ المنبثقة (نمط موحّد لكل شاشات المحتوى) ------------------------
    def _open_window(self, title: str, widget: QWidget, size: tuple[int, int] = (1150, 750)) -> None:
        existing = self._open_windows.get(title)
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return

        window = QWidget()
        window.setWindowTitle(f"{title} — {LABELS['app_title']}")
        window.setLayoutDirection(Qt.RightToLeft)
        window.resize(*size)
        layout = QVBoxLayout(window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)

        def _on_destroyed():
            self._open_windows.pop(title, None)

        window.destroyed.connect(_on_destroyed)
        self._open_windows[title] = window
        window.show()
        window.raise_()
        window.activateWindow()

    def _open_chart_of_accounts(self) -> None:
        from app.ui.accounting.chart_of_accounts_view import ChartOfAccountsView
        self._open_window(LABELS["coa"], ChartOfAccountsView(self.session))

    def _open_sales_invoice_list(self) -> None:
        from app.ui.sales.invoice_list import SalesInvoiceListView
        view = SalesInvoiceListView(self.session)
        view.invoice_opened.connect(self._open_sales_invoice_form)
        self._open_window(LABELS["sales_invoices"], view)

    def _open_sales_invoice_form(self, invoice_id: int | None, title: str) -> None:
        from app.ui.sales.invoice_form import SalesInvoiceFormView
        self._open_window(title, SalesInvoiceFormView(self.session, invoice_id=invoice_id), size=(1300, 850))

    def _open_sales_return_list(self) -> None:
        from app.ui.sales.return_list import SalesReturnInvoiceListView
        view = SalesReturnInvoiceListView(self.session)
        view.invoice_opened.connect(self._open_sales_return_form)
        self._open_window(LABELS["sales_returns"], view)

    def _open_sales_return_form(self, invoice_id: int | None, title: str) -> None:
        from app.ui.sales.return_form import SalesReturnInvoiceFormView
        self._open_window(title, SalesReturnInvoiceFormView(self.session, invoice_id=invoice_id), size=(1300, 850))

    def _open_purchase_invoice_list(self) -> None:
        from app.ui.purchases.invoice_list import PurchaseInvoiceListView
        view = PurchaseInvoiceListView(self.session)
        view.invoice_opened.connect(self._open_purchase_invoice_form)
        self._open_window(LABELS["purchase_invoices"], view)

    def _open_purchase_invoice_form(self, invoice_id: int | None, title: str) -> None:
        from app.ui.purchases.invoice_form import PurchaseInvoiceFormView
        self._open_window(title, PurchaseInvoiceFormView(self.session, invoice_id=invoice_id), size=(1300, 850))

    def _open_purchase_return_list(self) -> None:
        from app.ui.purchases.return_list import PurchaseReturnInvoiceListView
        view = PurchaseReturnInvoiceListView(self.session)
        view.invoice_opened.connect(self._open_purchase_return_form)
        self._open_window(LABELS["purchase_returns"], view)

    def _open_purchase_return_form(self, invoice_id: int | None, title: str) -> None:
        from app.ui.purchases.return_form import PurchaseReturnInvoiceFormView
        self._open_window(title, PurchaseReturnInvoiceFormView(self.session, invoice_id=invoice_id), size=(1300, 850))

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
        self._open_window("قريباً", placeholder, size=(500, 300))
