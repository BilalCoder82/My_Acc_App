"""
Chart of Accounts View — دليل الحسابات (Tree View)
======================================================
عرض شجري فقط بنطاق v1 — بدون تعديل من الواجهة بعد (إضافة/تعديل حساب
لاحقاً، عبر service مخصص، ليس مباشرة هنا).
"""

from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QLineEdit
from sqlalchemy.orm import Session

from app.reports.rollup import get_root_accounts, get_account_balance


class ChartOfAccountsView(QWidget):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session

        layout = QVBoxLayout(self)
        search = QLineEdit()
        search.setPlaceholderText("بحث حساب...")
        search.textChanged.connect(self._filter)
        layout.addWidget(search)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["الكود", "اسم الحساب", "الرصيد"])
        layout.addWidget(self.tree)

        self._reload()

    def _reload(self) -> None:
        self.tree.clear()
        for root in get_root_accounts(self.session):
            self._add_node(self.tree, root)
        self.tree.expandAll()

    def _add_node(self, parent_widget, account) -> None:
        balance = get_account_balance(self.session, account)
        node = QTreeWidgetItem([account.code, account.name_ar, str(balance)])
        if isinstance(parent_widget, QTreeWidget):
            parent_widget.addTopLevelItem(node)
        else:
            parent_widget.addChild(node)
        children = self.session.query(type(account)).filter_by(parent_id=account.id).order_by(
            type(account).code
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
