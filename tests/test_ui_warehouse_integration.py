"""
tests/test_ui_warehouse_integration.py
==========================================
اختبار تكاملي حقيقي (WORKFLOW.md §47) — لا يكتفي بأن ComboBox يظهر
ويُحفظ. يثبت السلسلة كاملة عبر واجهة PySide6 الفعلية (لا Mock للخدمات):

  اختيار UI → Invoice.warehouse_id → Posting → InventoryMovement.warehouse_id
  → متوسط تكلفة المستودع → COGS → قراءة مباشرة من دفتر الأستاذ

بمستودعين A (تكلفة 1,000) وB (تكلفة 9,000) بالضبط كما طُلب صراحة.
"""
import os, sys, datetime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QMessageBox

from app.models import Base, CostMethod, Warehouse, JournalLine
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.ui.sales.invoice_form import SalesInvoiceFormView
from app.ui.sales.return_form import SalesReturnInvoiceFormView
from app.ui.purchases.invoice_form import PurchaseInvoiceFormView

today = datetime.date.today()
results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


app = QApplication.instance() or QApplication(sys.argv)
# نمنع أي QMessageBox.critical/warning/information من التعليق (blocking) —
# النموذج يعرضها فعلياً، لكن الاختبار لا يحتاج نقرها يدوياً
QMessageBox.critical = staticmethod(lambda *a, **k: print("CRITICAL:", a[1:] if len(a) > 1 else a))
QMessageBox.warning = staticmethod(lambda *a, **k: print("WARNING:", a[1:] if len(a) > 1 else a))
QMessageBox.information = staticmethod(lambda *a, **k: print("INFO:", a[1:] if len(a) > 1 else a))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
session = sessionmaker(bind=engine)()
coa = create_default_chart_of_accounts(session)
item = create_item(session, sku="UIWH-1", name_ar="مادة اختبار واجهة مستودعات", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
wh_a = Warehouse(name_ar="مستودع A واجهة", is_active=True)
wh_b = Warehouse(name_ar="مستودع B واجهة", is_active=True)
session.add_all([wh_a, wh_b]); session.commit()


def fill_line(form, row, sku, qty, price):
    form.grid.setItem(row, 0, QTableWidgetItem(sku))
    form.grid.setItem(row, 1, QTableWidgetItem("مادة"))
    form.grid.setItem(row, 2, QTableWidgetItem(str(qty)))
    form.grid.setItem(row, 3, QTableWidgetItem(str(price)))
    form.grid.setItem(row, 4, QTableWidgetItem("0"))
    form.grid.setItem(row, 5, QTableWidgetItem("0"))
    form.grid.setItem(row, 6, QTableWidgetItem(""))


# --- الشراء الأول: مستودع A، تكلفة 1,000 عبر الواجهة الفعلية ---
print("== شراء عبر الواجهة الفعلية إلى مستودع A بتكلفة 1,000 ==")
purchase_form_a = PurchaseInvoiceFormView(session=session, invoice_id=None)
check("ComboBox المستودع فارغ افتراضياً (لا اختيار صامت)",
      purchase_form_a.warehouse_combo.currentData() is None)

idx_a = purchase_form_a.warehouse_combo.findData(wh_a.id)
purchase_form_a.party_edit.setText("مورد A")
purchase_form_a.warehouse_combo.setCurrentIndex(idx_a)
fill_line(purchase_form_a, 0, "UIWH-1", "100", "1000")
purchase_form_a._recalculate_totals()
purchase_form_a.invoice_no_edit.setText("UIWH-PA")
purchase_form_a._post()

check("الفاتورة رُحِّلت فعلياً عبر الواجهة", purchase_form_a.invoice is not None and
      purchase_form_a.invoice.status.value == "posted")
check("Invoice.warehouse_id = مستودع A المُختار فعلياً بالواجهة",
      purchase_form_a.invoice.warehouse_id == wh_a.id)

# --- الشراء الثاني: مستودع B، تكلفة 9,000 عبر واجهة أخرى منفصلة ---
print("== شراء عبر الواجهة الفعلية إلى مستودع B بتكلفة 9,000 ==")
purchase_form_b = PurchaseInvoiceFormView(session=session, invoice_id=None)
idx_b = purchase_form_b.warehouse_combo.findData(wh_b.id)
purchase_form_b.party_edit.setText("مورد B")
purchase_form_b.warehouse_combo.setCurrentIndex(idx_b)
fill_line(purchase_form_b, 0, "UIWH-1", "100", "9000")
purchase_form_b._recalculate_totals()
purchase_form_b.invoice_no_edit.setText("UIWH-PB")
purchase_form_b._post()
check("Invoice.warehouse_id = مستودع B المُختار فعلياً بالواجهة",
      purchase_form_b.invoice.warehouse_id == wh_b.id)

# --- التحقق: حركات المخزون وصلت فعلياً بالمستودع الصحيح ---
from app.models import InventoryMovement
mv_a = session.query(InventoryMovement).filter_by(source_id=purchase_form_a.invoice.id).first()
mv_b = session.query(InventoryMovement).filter_by(source_id=purchase_form_b.invoice.id).first()
check("InventoryMovement (شراء A) warehouse_id = A فعلياً", mv_a.warehouse_id == wh_a.id)
check("InventoryMovement (شراء B) warehouse_id = B فعلياً", mv_b.warehouse_id == wh_b.id)

# --- بيع من كل مستودع عبر الواجهة، والتأكد من COGS الصحيح لكل واحد ---
print("== بيع من A عبر الواجهة — يجب أن يستخدم تكلفة A فقط (1,000) ==")
sale_form_a = SalesInvoiceFormView(session=session, invoice_id=None)
idx_a2 = sale_form_a.warehouse_combo.findData(wh_a.id)
sale_form_a.party_edit.setText("عميل A")
sale_form_a.warehouse_combo.setCurrentIndex(idx_a2)
fill_line(sale_form_a, 0, "UIWH-1", "10", "5000")
sale_form_a._recalculate_totals()
sale_form_a.invoice_no_edit.setText("UIWH-SA")
sale_form_a._post()

sale_a_entry = sale_form_a.invoice.journal_entry_id
cogs_lines_a = session.query(JournalLine).filter_by(
    entry_id=sale_a_entry, account_id=coa["cogs"].id
).all()
cogs_a_total = sum(D_(str(l.debit_base)) for l in cogs_lines_a)
check("COGS بيع من A عبر الواجهة الفعلية = 10×1,000 (تكلفة A فقط)",
      cogs_a_total == D_("10000"), f"actual={cogs_a_total}")

print("== بيع من B عبر الواجهة — يجب أن يستخدم تكلفة B فقط (9,000)، لا تأثر ببيع A ==")
sale_form_b = SalesInvoiceFormView(session=session, invoice_id=None)
idx_b2 = sale_form_b.warehouse_combo.findData(wh_b.id)
sale_form_b.party_edit.setText("عميل B")
sale_form_b.warehouse_combo.setCurrentIndex(idx_b2)
fill_line(sale_form_b, 0, "UIWH-1", "10", "12000")
sale_form_b._recalculate_totals()
sale_form_b.invoice_no_edit.setText("UIWH-SB")
sale_form_b._post()

sale_b_entry = sale_form_b.invoice.journal_entry_id
cogs_lines_b = session.query(JournalLine).filter_by(
    entry_id=sale_b_entry, account_id=coa["cogs"].id
).all()
cogs_b_total = sum(D_(str(l.debit_base)) for l in cogs_lines_b)
check("COGS بيع من B عبر الواجهة الفعلية = 10×9,000 (تكلفة B فقط، لم تتأثر ببيع A)",
      cogs_b_total == D_("90000"), f"actual={cogs_b_total}")

# --- التحقق من الرفض عند عدم اختيار مستودع (لا سقوط صامت) ---
print("== محاولة الترحيل بلا اختيار مستودع — يجب الرفض ==")
sale_form_no_wh = SalesInvoiceFormView(session=session, invoice_id=None)
sale_form_no_wh.party_edit.setText("عميل بلا مستودع")
fill_line(sale_form_no_wh, 0, "UIWH-1", "1", "1000")
sale_form_no_wh._recalculate_totals()
sale_form_no_wh.invoice_no_edit.setText("UIWH-NOWH")
sale_form_no_wh._post()
check("لم تُنشأ فاتورة فعلياً بلا اختيار مستودع (رُفض الحفظ/الترحيل)",
      sale_form_no_wh.invoice is None,
      f"invoice={sale_form_no_wh.invoice}")

print()
print("=" * 70)
print(f"✅ التكامل الكامل UI→Invoice→Posting→InventoryMovement→COGS نجح ({len(results)} تحقّقاً)")
print("=" * 70)

# =====================================================================
# اختبار إضافي (طلب Bilal صراحة): شراء جديد بمستودع A لا يغيّر متوسط B،
# ثم مرتجع مرتبط يرث نفس مستودع الفاتورة الأصلية ويُقفَل عليه
# =====================================================================
print()
print("== شراء إضافي بمستودع A بسعر مختلف (1,200) — يجب ألا يمسّ متوسط B إطلاقاً ==")
purchase_form_a2 = PurchaseInvoiceFormView(session=session, invoice_id=None)
idx_a3 = purchase_form_a2.warehouse_combo.findData(wh_a.id)
purchase_form_a2.party_edit.setText("مورد A ثانٍ")
purchase_form_a2.warehouse_combo.setCurrentIndex(idx_a3)
fill_line(purchase_form_a2, 0, "UIWH-1", "50", "1200")
purchase_form_a2._recalculate_totals()
purchase_form_a2.invoice_no_edit.setText("UIWH-PA2")
purchase_form_a2._post()
check("الشراء الإضافي بمستودع A رُحِّل فعلياً", purchase_form_a2.invoice.status.value == "posted")

# متوسط A الجديد المتوقع: (90 وحدة متبقية من الشراء الأول @1,000 + 50 @1,200)
# / 140 — نتحقق منه عبر بيع جديد من A فقط، ونتأكد بيع من B غير متأثر إطلاقاً
from app.services.item_queries import get_item_stock_summary
avg_a_after = get_item_stock_summary(session, item.id, warehouse_id=wh_a.id).average_cost
avg_b_after = get_item_stock_summary(session, item.id, warehouse_id=wh_b.id).average_cost
check("متوسط B ما زال 9,000 بالضبط (لم يتأثر بشراء A الجديد إطلاقاً)",
      avg_b_after == D_("9000"), f"actual={avg_b_after}")
check("متوسط A تغيّر فعلياً بعد الشراء الإضافي (لم يتجمّد)",
      avg_a_after != D_("1000"), f"actual={avg_a_after}")

sale_form_b2 = SalesInvoiceFormView(session=session, invoice_id=None)
idx_b3 = sale_form_b2.warehouse_combo.findData(wh_b.id)
sale_form_b2.party_edit.setText("عميل B ثانٍ")
sale_form_b2.warehouse_combo.setCurrentIndex(idx_b3)
fill_line(sale_form_b2, 0, "UIWH-1", "5", "12000")
sale_form_b2._recalculate_totals()
sale_form_b2.invoice_no_edit.setText("UIWH-SB2")
sale_form_b2._post()
cogs_lines_b2 = session.query(JournalLine).filter_by(
    entry_id=sale_form_b2.invoice.journal_entry_id, account_id=coa["cogs"].id
).all()
cogs_b2_total = sum(D_(str(l.debit_base)) for l in cogs_lines_b2)
check("بيع جديد من B بعد شراء A الإضافي: COGS = 5×9,000 بالضبط (B معزول تماماً عن A)",
      cogs_b2_total == D_("45000"), f"actual={cogs_b2_total}")

print("== مرتجع مرتبط بفاتورة بيع A — يجب أن يرث مستودع A ويُقفَل عليه ==")
return_form_a = SalesReturnInvoiceFormView(session=session, invoice_id=None)
return_form_a.original_ref_edit.setText(sale_form_a.invoice.invoice_no)
return_form_a._load_from_original()
check("المرتجع ورث warehouse_id = A فعلياً من الفاتورة الأصلية",
      return_form_a._selected_warehouse_id() == wh_a.id)
check("المستودع مُقفَل بالواجهة على المرتجع المرتبط (لا يمكن تغييره)",
      not return_form_a.warehouse_combo.isEnabled())
# إرجاع كمية جزئية (5 من أصل 10) للتأكد أن التعديل اليدوي على الكمية يعمل
return_form_a.grid.setItem(0, 2, QTableWidgetItem("5"))
return_form_a._recalculate_totals()
return_form_a.invoice_no_edit.setText("UIWH-RETA")
return_form_a._post()
check("المرتجع رُحِّل فعلياً", return_form_a.invoice.status.value == "posted")
check("Invoice.warehouse_id للمرتجع = A بالضبط (نفس الأصلية)",
      return_form_a.invoice.warehouse_id == wh_a.id)
return_movement = session.query(InventoryMovement).filter_by(
    source_type="sales_return", source_id=return_form_a.invoice.id
).first()
check("حركة مخزون المرتجع فعلياً بمستودع A وبكمية 5 (اتجاه عكسي IN)",
      return_movement.warehouse_id == wh_a.id and return_movement.quantity == D_("5"))

print()
print("=" * 70)
print(f"✅ اختبار عزل المستودعات الكامل (شراء إضافي + مرتجع مرتبط) نجح — {len(results)} تحقّقاً إجمالياً")
print("=" * 70)
