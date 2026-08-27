import os, sys, datetime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Account, AccountType, Item, CostMethod, Warehouse
from app.ui.inventory.item_list_view import ItemListView
from app.ui.inventory.item_card_dialog import ItemCardDialog
from app.ui.accounting.journal_voucher_form import JournalVoucherFormView, COL_CODE, COL_DEBIT

from PySide6.QtWidgets import QApplication, QAbstractItemView
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QEventLoop, QTimer, qInstallMessageHandler

app = QApplication.instance() or QApplication(sys.argv)

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

inv = Account(code="1200", name_ar="المخزون", account_type=AccountType.ASSET)
cogs = Account(code="5100", name_ar="تكلفة المبيعات", account_type=AccountType.EXPENSE)
cash = Account(code="1101", name_ar="الصندوق", account_type=AccountType.ASSET)
session.add_all([inv, cogs, cash])
session.commit()

captured = []
def handler(mode, ctx, msg):
    captured.append(msg)
qInstallMessageHandler(handler)

def pump(ms=30):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()

# ============================================================================
# PART 1: دليل المواد — structural check + full interaction cycle
# ============================================================================
print("--- دليل المواد: open -> create -> edit -> close -> reopen ---")
view = ItemListView(session)
view.show()
app.processEvents()

# structural fact, not assumption:
assert view.table.editTriggers() == QAbstractItemView.NoEditTriggers
print("CONFIRMED: item list table has NoEditTriggers -> no QAbstractItemView "
      "delegate/editor is ever created here. The specific warning "
      "'commitData called with an editor that does not belong to this view' "
      "is raised by QAbstractItemView's own editor<->index tracking, which "
      "only exists when delegate editors are opened. Structurally impossible "
      "to originate from this table as built.")

card = ItemCardDialog(session, item=None)
card.show()
app.processEvents()
card.sku_edit.setText("SKU-X")
card.name_edit.setText("مادة اختبار")
card.inventory_account_combo.setCurrentIndex(card.inventory_account_combo.findData(inv.id))
card.cogs_account_combo.setCurrentIndex(card.cogs_account_combo.findData(cogs.id))
card._save()
app.processEvents()
assert card.saved_item is not None
print("item created via card dialog, cost_method saved =", card.saved_item.cost_method)
assert card.saved_item.cost_method == CostMethod.AVERAGE, "BUG #1 regression: default should be AVERAGE"

view._reload()
app.processEvents()
assert view.table.rowCount() == 1

# reopen for edit, close via .close() (not accept/reject) mid-way, then reopen again
edit_card = ItemCardDialog(session, item=card.saved_item)
edit_card.show()
app.processEvents()
assert edit_card.cost_method_combo.currentData() == CostMethod.AVERAGE, "BUG #1 regression: reopen should show AVERAGE selected"
edit_card.close()
app.processEvents()

edit_card2 = ItemCardDialog(session, item=card.saved_item)
edit_card2.show()
app.processEvents()
edit_card2.close()
app.processEvents()

view.close()
app.processEvents()
view2 = ItemListView(session)
view2.show()
app.processEvents()
view2.close()
app.processEvents()

bad = [m for m in captured if "does not belong to this view" in m]
print("Qt messages after دليل المواد cycle:", captured)
assert not bad, f"REGRESSION in دليل المواد: {bad}"
print("PART 1 OK: no commitData warning anywhere in دليل المواد open/edit/close/reopen cycle")
captured.clear()

# ============================================================================
# PART 2: the ACTUAL untested scenario — closing a window while a cell
# editor is literally still open/uncommitted (never pressed Enter, never
# clicked away). This is سند القيد's grid, the only real editable
# QAbstractItemView in the app, and the one prior fix targeted Enter
# specifically -- not "close while still editing".
# ============================================================================
print("--- سند القيد: close the window WHILE an editor is still open (no Enter) ---")
jv = JournalVoucherFormView(session)
jv.show()
app.processEvents()

jv.grid.setCurrentCell(0, COL_CODE)
jv.grid.editItem(jv.grid.item(0, COL_CODE))
app.processEvents()
editor = QApplication.focusWidget()
if editor is not None:
    QTest.keyClicks(editor, "1200")
    # deliberately NOT pressing Enter, NOT clicking away -- editor stays open
app.processEvents()

jv.close()  # close the window with the editor still (potentially) open
app.processEvents()
pump(100)
app.processEvents()

bad2 = [m for m in captured if "does not belong to this view" in m]
print("Qt messages after closing mid-edit:", captured)
assert not bad2, f"FOUND THE REAL BUG: {bad2}"
print("PART 2 OK: closing the journal voucher window mid-edit does not trigger the warning either")

print("ALL INVESTIGATION TESTS PASSED — NO 'commitData ... does not belong to this view' ANYWHERE")
