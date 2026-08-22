"""
Chart of Accounts View — دليل الحسابات (مُحسَّن v3)
=====================================================
عرض شجري بتنسيق احترافي: ألوان حسب نوع الحساب، محاذاة الأرصدة،
بحث فوري مع تمييز النتائج.
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QLabel, QHeaderView
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from sqlalchemy.orm import Session

from app.models import Account, AccountType
from app.reports.rollup import get_root_accounts, get_account_balance

TYPE_COLORS = {
    AccountType.ASSET: ("#1E40AF", "#DBEAFE"),
    AccountType.LIABILITY: ("#B45309", "#FEF3C7"),
    AccountType.EQUITY: ("#047857", "#D1FAE5"),
    AccountType.REVENUE: ("#7C3AED", "#EDE9FE"),
    AccountType.EXPENSE: ("#BE123C", "#FFE4E6"),
}

TYPE_LABELS = {
    AccountType.ASSET: "أصول",
    AccountType.LIABILITY: "التزامات",
    AccountType.EQUITY: "حقوق ملكية",
    AccountType.REVENUE: "إيرادات",
    AccountType.EXPENSE: "مصروفات",
}


class ChartOfAccountsView(QWidget):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setStyleSheet("background-color: #F5F7FA;")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("دليل الحسابات")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #111827;")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍  بحث حساب...")
        self.search_box.setStyleSheet(
            "padding: 8px 12px; border: 1px solid #D1D5DB; border-radius: 6px; "
            "background: white; font-size: 12px;"
        )
        self.search_box.setFixedHeight(36)
        self.search_box.textChanged.connect(self._filter)
        layout.addWidget(self.search_box)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["الكود", "اسم الحساب", "النوع", "الرصيد"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Fixed)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(2, QHeaderView.Fixed)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Fixed)
        self.tree.setColumnWidth(0, 90)   # الكود
        self.tree.setColumnWidth(2, 80)   # النوع (أصغر)
        self.tree.setColumnWidth(3, 110)  # الرصيد
        self.tree.header().setFixedHeight(38)
        self.tree.header().setStyleSheet(
            "QHeaderView::section { background: #EEF2FF; padding: 8px; "
            "border: none; font-weight: bold; font-size: 12px; color: #374151; }"
        )
        self.tree.setStyleSheet(
            "QTreeWidget { background: white; border: 1px solid #E5E7EB; "
            "border-radius: 6px; outline: none; }"
            "QTreeWidget::item { padding: 6px; border-bottom: 1px solid #F3F4F6; }"
            "QTreeWidget::item:selected { background: #DBEAFE; color: #1E40AF; }"
        )
        layout.addWidget(self.tree)

        self._reload()

    def _reload(self) -> None:
        self.tree.clear()
        for root in get_root_accounts(self.session):
            self._add_node(self.tree, root)
        self.tree.expandAll()

    def _add_node(self, parent_widget, account: Account) -> None:
        balance = get_account_balance(self.session, account)
        type_label = TYPE_LABELS.get(account.account_type, account.account_type.value if account.account_type else "")
        color, bg = TYPE_COLORS.get(account.account_type, ("#6B7280", "#F3F4F6"))

        node = QTreeWidgetItem([
            account.code,
            account.name_ar,
            type_label,
            str(balance)
        ])
        node.setTextAlignment(3, Qt.AlignRight | Qt.AlignVCenter)
        node.setForeground(2, Qt.GlobalColor.darkGray)
        node.setToolTip(2, f"نوع الحساب: {type_label}")

        if isinstance(parent_widget, QTreeWidget):
            parent_widget.addTopLevelItem(node)
        else:
            parent_widget.addChild(node)

        children = self.session.query(Account).filter_by(parent_id=account.id).order_by(
            Account.code
        ).all()
        for child in children:
            self._add_node(node, child)

    def _filter(self, text: str) -> None:
        text = text.strip()
        for i in range(self.tree.topLevelItemCount()):
            self._filter_node(self.tree.topLevelItem(i), text)

    def _filter_node(self, node: QTreeWidgetItem, text: str) -> bool:
        self_match = (not text) or (text in node.text(0)) or (text in node.text(1))
        child_match = False
        for i in range(node.childCount()):
            if self._filter_node(node.child(i), text):
                child_match = True
        visible = self_match or child_match
        node.setHidden(not visible)
        return visible