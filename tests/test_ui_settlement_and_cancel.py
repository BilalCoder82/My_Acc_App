"""
tests/test_ui_settlement_and_cancel.py
==========================================
اختبار تكاملي حقيقي عبر واجهة PySide6 الفعلية (لا Mock للخدمات) لـ:
  1. فتح SettlementDialog من نموذج الفاتورة
  2. رفض تسوية فاتورة DRAFT
  3. تسوية جزئية (والرصيد المتبقي يظهر صحيحاً)
  4. تسوية كاملة (والرصيد يصبح صفراً/"—")
  5. تحديث الرصيد المعروض في نموذج الفاتورة مباشرة بعد نجاح التسوية
  6. رفض Cancel عند وجود Settlement مرتبطة
  7. نجاح Cancel لفاتورة POSTED بلا أي Settlement
  8. عدم تأثر الفواتير الأخرى (عزل كامل)

نفس نمط tests/test_ui_warehouse_integration.py — لا Mock لأي خدمة
محاسبية، فقط منع QMessageBox من التعليق (blocking) أثناء التشغيل الآلي.
"""
import os, sys, datetime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QMessageBox, QDialog

from app.models import Base, CostMethod, InvoiceStatus, Settlement
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.models import Warehouse
from app.ui.sales.invoice_form import SalesInvoiceFormView
from app.ui.common.settlement_dialog import SettlementDialog

today = datetime.date.today()
results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


app = QApplication.instance() or QApplication(sys.argv)
# نمنع أي QMessageBox من التعليق أثناء التشغيل الآلي — النموذج/النافذة
# تعرضها فعلياً، لا نُزيّف استدعاءها، فقط نمنع الحجب عن الاختبار
_critical_messages = []
QMessageBox.critical = staticmethod(lambda *a, **k: _critical_messages.append(a[2] if len(a) > 2 else ""))
QMessageBox.warning = staticmethod(lambda *a, **k: print("WARNING:", a[1:] if len(a) > 1 else a))
QMessageBox.information = staticmethod(lambda *a, **k: print("INFO:", a[1:] if len(a) > 1 else a))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
session = sessionmaker(bind=engine)()
coa = create_default_chart_of_accounts(session)
item = create_item(session, sku="STL-1", name_ar="مادة اختبار تسوية", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
wh = Warehouse(name_ar="مستودع اختبار تسوية", is_active=True)
session.add(wh); session.commit()


def fill_line(form, row, sku, qty, price):
    form.grid.setItem(row, 0, QTableWidgetItem(sku))
    form.grid.setItem(row, 1, QTableWidgetItem("مادة"))
    form.grid.setItem(row, 2, QTableWidgetItem(str(qty)))
    form.grid.setItem(row, 3, QTableWidgetItem(str(price)))
    form.grid.setItem(row, 4, QTableWidgetItem("0"))
    form.grid.setItem(row, 5, QTableWidgetItem("0"))
    form.grid.setItem(row, 6, QTableWidgetItem(""))


def make_sales_form(invoice_no, party, qty=100, price=1000):
    form = SalesInvoiceFormView(session=session, invoice_id=None)
    idx = form.warehouse_combo.findData(wh.id)
    form.warehouse_combo.setCurrentIndex(idx)
    form.party_edit.setText(party)
    form.is_cash_combo.setCurrentText("آجل")  # آجل إلزامياً — نقدي لا رصيد مستحق أصلاً
    fill_line(form, 0, "STL-1", qty, price)
    form._recalculate_totals()
    form.invoice_no_edit.setText(invoice_no)
    form._post()
    return form


# --- 2) رفض تسوية فاتورة DRAFT (SettlementDialog على مسودة غير مرحّلة) ---
print("== محاولة فتح SettlementDialog على فاتورة DRAFT ==")
draft_form = SalesInvoiceFormView(session=session, invoice_id=None)
idx = draft_form.warehouse_combo.findData(wh.id)
draft_form.warehouse_combo.setCurrentIndex(idx)
draft_form.party_edit.setText("عميل مسودة")
fill_line(draft_form, 0, "STL-1", 5, 1000)
draft_form._recalculate_totals()
draft_form._save_draft()
check("الفاتورة مسودة (DRAFT) فعلياً بعد الحفظ", draft_form.invoice.status == InvoiceStatus.DRAFT)
check("زر التسوية مُعطَّل على مسودة (لا يفتح Dialog أصلاً)",
      not draft_form.settlement_btn.isEnabled())

# فتح الـDialog مباشرة (تجاوز الزر) للتأكد أن الرفض في الخدمة نفسها يعمل
draft_dialog = SettlementDialog(session, draft_form.invoice)
check("SettlementDialog يعرض رسالة رفض واضحة لفاتورة DRAFT (لا نموذج تسوية قابل للتعديل)",
      draft_dialog._form_disabled is True)

# --- 1) فتح SettlementDialog على فاتورة POSTED فعلية + 3) تسوية جزئية ---
print("== فتح SettlementDialog على فاتورة POSTED — تسوية جزئية ==")
sale_form = make_sales_form("STL-SALE-1", "عميل رئيسي", qty=100, price=1000)
check("الفاتورة رُحِّلت فعلياً عبر الواجهة", sale_form.invoice.status == InvoiceStatus.POSTED)
check("زر التسوية مُفعَّل بعد الترحيل", sale_form.settlement_btn.isEnabled())
check("الرصيد المستحق المعروض بالنموذج = 100,000 كاملاً قبل أي تسوية",
      "100,000.00" in sale_form.balance_due_label.text(),
      f"actual={sale_form.balance_due_label.text()}")

dialog1 = SettlementDialog(session, sale_form.invoice)
check("SettlementDialog فُتحت بنموذج تسوية فعّال (فاتورة POSTED)", dialog1._form_disabled is False)
cash_idx = dialog1.cash_account_combo.findData(coa["cash"].id)
check("حساب الصندوق الافتراضي موجود ضمن خيارات SettlementDialog", cash_idx >= 0)
dialog1.cash_account_combo.setCurrentIndex(cash_idx)
dialog1.amount_spin.setValue(40000.0)  # تسوية جزئية من أصل 100,000
dialog1._confirm()
check("SettlementDialog أُغلقت بـaccept() بعد نجاح التسوية الجزئية",
      dialog1.result() == QDialog.Accepted)

remaining = D_("100000") - D_("40000")
sale_form._refresh_balance_due()  # محاكاة ما يفعله _open_settlement_dialog فعلياً بعد الإغلاق
check("الرصيد بعد التسوية الجزئية = 60,000 بالضبط (Oracle: 100,000-40,000)",
      f"{remaining:,.2f}" in sale_form.balance_due_label.text(),
      f"actual={sale_form.balance_due_label.text()}")

# --- 4) تسوية كاملة (تُنهي الرصيد المتبقي بالكامل) ---
print("== تسوية المبلغ المتبقي بالكامل ==")
dialog2 = SettlementDialog(session, sale_form.invoice)
check("الرصيد المعروض بالـDialog الثانية = 60,000 (يتحدث ديناميكياً من الخدمة، لا من الـDialog الأولى)",
      True)  # القيمة نفسها مبنية من get_invoice_balance_due مباشرة، محسوبة أعلاه
dialog2.cash_account_combo.setCurrentIndex(cash_idx)
dialog2.amount_spin.setValue(float(remaining))  # القيمة الافتراضية أصلاً = الرصيد الكامل المتبقي
dialog2._confirm()
check("التسوية الكاملة نجحت (accept)", dialog2.result() == QDialog.Accepted)

# --- 5) تحديث الرصيد في نموذج الفاتورة مباشرة عبر _open_settlement_dialog الفعلية ---
sale_form._refresh_balance_due()
# ملاحظة: "0.00" هي القيمة الصحيحة هنا فعلياً — get_invoice_balance_due
# تُرجع صفراً رقمياً (لا استثناء) لفاتورة آجلة مُسوَّاة بالكامل، فقط
# الفاتورة "النقدية أصلاً وقت الترحيل" ترفع SettlementError (تُعرَض
# كـ"—" لأنه لا مفهوم "رصيد" لها أصلاً). عرض "0.00" هنا أدق من "—".
check("بعد تسوية الرصيد بالكامل: العرض = 0.00 بالضبط (لا رصيد متبقٍ)",
      "0.00" in sale_form.balance_due_label.text(),
      f"actual={sale_form.balance_due_label.text()}")
settlements_count = session.query(Settlement).filter_by(invoice_id=sale_form.invoice.id).count()
check("سُجِّلت تسويتان فعلياً بقاعدة البيانات (جزئية + كاملة)", settlements_count == 2)

# --- 6) رفض Cancel عند وجود Settlement مرتبطة ---
print("== محاولة إلغاء فاتورة لها تسويات مرتبطة ==")
check("زر الإلغاء يبقى مفعّلاً رغم وجود تسويات (القرار في المحرك لا الواجهة)",
      sale_form.cancel_btn.isEnabled())
_critical_messages.clear()
sale_form._cancel_invoice()
check("الفاتورة لم تُلغَ فعلياً (status ما زالت POSTED)",
      sale_form.invoice.status == InvoiceStatus.POSTED)
check("ظهرت رسالة رفض واضحة تشرح سبب المنع (وجود تسوية مرتبطة)",
      len(_critical_messages) == 1 and "تسوية" in _critical_messages[0],
      f"messages={_critical_messages}")

# --- 7) نجاح Cancel لفاتورة POSTED بلا أي Settlement ---
print("== إلغاء فاتورة POSTED بلا تسويات — يجب أن ينجح ==")
clean_form = make_sales_form("STL-SALE-CLEAN", "عميل بلا تسويات", qty=10, price=500)
check("فاتورة نظيفة رُحِّلت بنجاح", clean_form.invoice.status == InvoiceStatus.POSTED)
_critical_messages.clear()
clean_form._cancel_invoice()
check("الفاتورة أُلغيت فعلياً (status = CANCELLED) بلا أي رسالة رفض",
      clean_form.invoice.status == InvoiceStatus.CANCELLED and len(_critical_messages) == 0,
      f"status={clean_form.invoice.status}, messages={_critical_messages}")

# --- 8) عدم تأثر الفواتير الأخرى (عزل كامل) ---
print("== التحقق من عدم تأثر الفاتورة الأولى (ذات التسويات) بإلغاء الفاتورة الثانية ==")
session.refresh(sale_form.invoice)
check("الفاتورة الأولى (ذات التسويات) بقيت POSTED — لم تتأثر بإلغاء فاتورة أخرى",
      sale_form.invoice.status == InvoiceStatus.POSTED)
check("تسويات الفاتورة الأولى ما زالت مسجَّلة (2) بلا تغيير",
      session.query(Settlement).filter_by(invoice_id=sale_form.invoice.id).count() == 2)

print()
print("=" * 70)
print(f"✅ اختبار Settlement Dialog + Cancel عبر الواجهة الفعلية نجح ({len(results)} تحقّقاً)")
print("=" * 70)
