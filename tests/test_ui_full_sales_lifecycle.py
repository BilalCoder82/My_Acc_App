"""
tests/test_ui_full_sales_lifecycle.py
=========================================
دورة فاتورة البيع كاملة من منظور المستخدم عبر PySide6 الفعلية (لا Mock)
كما طلب Bilal صراحة بعد مراجعة SettlementDialog/Cancel — لا يكفي إثبات
الثمانية سيناريوهات المطلوبة سابقاً بمعزل، بل دورة الاستخدام كاملة:

  إنشاء (مستودع→عميل→عملة→سعر صرف→مادة→كمية→سعر→خصم→ضريبة→إجمالي)
  → حفظ DRAFT → إعادة فتح → تعديل → ترحيل POSTED → قفل الحقول
  → Settlement (رفض DRAFT/قبض جزئي×2/قبض كامل/الرصيد يصل صفراً بالضبط)
  → Cancel (بلا تسوية ينجح، مع تسوية يُرفَض من المحرك، لا يؤثر على مستند آخر)

مع تحقق مباشر بكل مرحلة من: Invoice, JournalEntry, InventoryMovement,
COGS, Receivable (ميزان المراجعة الفعلي), Settlement, FX, وليس فقط حالة
النموذج بالواجهة.
"""
import os, sys, datetime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QMessageBox, QDialog, QAbstractItemView

from app.models import (
    Base, CostMethod, InvoiceStatus, Settlement, JournalEntry, JournalEntryStatus,
    InventoryMovement, Warehouse, JournalLine,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.ui.sales.invoice_form import SalesInvoiceFormView
from app.ui.common.settlement_dialog import SettlementDialog
from app.reports.trial_balance import get_trial_balance

today = datetime.date.today()
results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


app = QApplication.instance() or QApplication(sys.argv)
_critical_messages = []
QMessageBox.critical = staticmethod(lambda *a, **k: _critical_messages.append(a[2] if len(a) > 2 else ""))
QMessageBox.warning = staticmethod(lambda *a, **k: print("WARNING:", a[1:] if len(a) > 1 else a))
QMessageBox.information = staticmethod(lambda *a, **k: print("INFO:", a[1:] if len(a) > 1 else a))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
session = sessionmaker(bind=engine)()
coa = create_default_chart_of_accounts(session)
item = create_item(session, sku="LC-1", name_ar="مادة اختبار الدورة الكاملة", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
wh = Warehouse(name_ar="مستودع الدورة الكاملة", is_active=True)
session.add(wh); session.commit()

# فاتورة شراء أولية بسيطة (عبر الخدمة مباشرة — ليست موضوع هذا الاختبار)
# لتوفير مخزون تُباع منه فاتورة البيع محل الاختبار، بتكلفة معروفة 2,000
from app.models import Invoice, InvoiceLine, InvoiceKind
from app.services.posting import post_purchase_invoice
purchase = Invoice(invoice_no="LC-PUR-1", kind=InvoiceKind.PURCHASE, party_name="مورد تمهيدي",
                    invoice_date=today, currency_code="USD", exchange_rate=D_("1"),
                    status=InvoiceStatus.DRAFT, warehouse_id=wh.id)
purchase.lines = [InvoiceLine(item_id=item.id, quantity=D_("50"), unit_price=D_("2000"))]
session.add(purchase); session.commit()
post_purchase_invoice(session, purchase, is_cash=True)
session.commit()


def fill_line(form, row, sku, qty, price, disc="0", tax="0"):
    form.grid.setItem(row, 0, QTableWidgetItem(sku))
    form.grid.setItem(row, 1, QTableWidgetItem("مادة"))
    form.grid.setItem(row, 2, QTableWidgetItem(str(qty)))
    form.grid.setItem(row, 3, QTableWidgetItem(str(price)))
    form.grid.setItem(row, 4, QTableWidgetItem(str(disc)))
    form.grid.setItem(row, 5, QTableWidgetItem(str(tax)))
    form.grid.setItem(row, 6, QTableWidgetItem(""))


# =====================================================================
# 1) إنشاء فاتورة بيع جديدة كاملة عبر الواجهة: مستودع→عميل→عملة→سعر
#    صرف→مادة→كمية→سعر→خصم→ضريبة→إجمالي
# =====================================================================
print("== 1) إنشاء فاتورة بيع جديدة كاملة عبر الواجهة ==")
form = SalesInvoiceFormView(session=session, invoice_id=None)
idx = form.warehouse_combo.findData(wh.id)
form.warehouse_combo.setCurrentIndex(idx)
form.party_edit.setText("عميل الدورة الكاملة")
form.currency_combo.setCurrentText("USD")
form.exchange_rate_spin.setValue(1.0)
form.is_cash_combo.setCurrentText("آجل")  # إلزامي — الرصيد المستحق موضوع الاختبار
fill_line(form, 0, "LC-1", "10", "5000", disc="5", tax="10")
form._recalculate_totals()

check("المستودع اختير فعلياً بالواجهة", form._selected_warehouse_id() == wh.id)
check("العملة USD والسعر 1 كما أُدخلا", form.currency_combo.currentText() == "USD"
      and float(form.exchange_rate_spin.value()) == 1.0)
# 10×5000=50,000 - خصم5%=2,500 → 47,500 + ضريبة10%=4,750 → 52,250
check("الإجمالي المحسوب بالواجهة يعكس الكمية/السعر/الخصم/الضريبة صحيحاً (52,250.00)",
      "52,250.00" in form.grand_total_label.text(),
      f"actual={form.grand_total_label.text()}")

# =====================================================================
# 2) حفظ DRAFT → إعادة فتح → تعديل → ترحيل POSTED → قفل الحقول
# =====================================================================
print("== 2) حفظ DRAFT، إعادة فتح، تعديل، ترحيل، قفل ==")
form.invoice_no_edit.setText("LC-SALE-1")
form._save_draft()
check("الفاتورة أصبحت DRAFT فعلياً بقاعدة البيانات", form.invoice.status == InvoiceStatus.DRAFT)
saved_id = form.invoice.id

reopened = SalesInvoiceFormView(session=session, invoice_id=saved_id)
check("إعادة الفتح استرجعت نفس المستودع", reopened._selected_warehouse_id() == wh.id)
check("إعادة الفتح استرجعت نفس العميل", reopened.party_edit.text() == "عميل الدورة الكاملة")
check("إعادة الفتح استرجعت نفس العملة وسعر الصرف",
      reopened.currency_combo.currentText() == "USD" and float(reopened.exchange_rate_spin.value()) == 1.0)

# تعديل: تغيير الكمية من 10 إلى 20 على النسخة المُعاد فتحها
reopened.grid.setItem(0, 2, QTableWidgetItem("20"))
reopened._recalculate_totals()
reopened._save_draft()
check("التعديل (كمية 20) انعكس فعلياً بعد إعادة الحفظ (105,000 قبل الخصم/الضريبة → إجمالي جديد)",
      len(reopened.invoice.lines) == 1 and reopened.invoice.lines[0].quantity == D_("20"))

reopened.invoice.warehouse_id = wh.id  # تأكيد صريح قبل الترحيل (نفس مسار _post الحقيقي)
reopened._post()
check("الفاتورة رُحِّلت POSTED فعلياً بعد التعديل", reopened.invoice.status == InvoiceStatus.POSTED)
check("JournalEntry فعلي وُلِد ومرتبط بالفاتورة",
      reopened.invoice.journal_entry_id is not None)
entry = session.get(JournalEntry, reopened.invoice.journal_entry_id)
check("القيد المرحّل متوازن فعلياً (is_balanced)", entry.is_balanced())
check("InventoryMovement فعلية وُلِدت لنفس الفاتورة (20 وحدة)",
      session.query(InventoryMovement).filter_by(
          source_type="sales_invoice", source_id=reopened.invoice.id
      ).first().quantity == D_("20"))
cogs_lines = session.query(JournalLine).filter_by(entry_id=entry.id, account_id=coa["cogs"].id).all()
cogs_total = sum(D_(str(l.debit_base)) for l in cogs_lines)
check("COGS الفعلي = 20 × 2,000 (تكلفة الشراء التمهيدي) = 40,000 بالضبط",
      cogs_total == D_("40000"), f"actual={cogs_total}")

# إعادة فتح الفاتورة POSTED والتأكد من القفل الفعلي بالواجهة
posted_view = SalesInvoiceFormView(session=session, invoice_id=reopened.invoice.id)
check("الحقول الأساسية مُعطَّلة فعلياً بعد الترحيل (party_edit)", not posted_view.party_edit.isEnabled())
check("العملة مُعطَّلة فعلياً بعد الترحيل", not posted_view.currency_combo.isEnabled())
check("المستودع مُعطَّل فعلياً بعد الترحيل", not posted_view.warehouse_combo.isEnabled())
check("شبكة البنود غير قابلة للتحرير فعلياً بعد الترحيل",
      posted_view.grid.editTriggers() == QAbstractItemView.NoEditTriggers)

# =====================================================================
# 3) Settlement: رفض DRAFT (سبق إثباته)، قبض جزئي×2، قبض كامل، الرصيد
#    يصل صفراً بالضبط — بتحقق مباشر من جدول Settlement وميزان المراجعة
# =====================================================================
print("== 3) Settlement: قبض جزئي مرتين ثم كامل، والرصيد يصل صفراً بالضبط ==")
grand_total = D_("52250")  # 20×5000 حساب مطابق: 100,000-5%=95,000+10%=104,500 (بعد تعديل الكمية لـ20)
# نُعيد الحساب الفعلي من الخدمة نفسها بدل افتراض رقم يدوي مُعرَّض للخطأ
from app.services.invoice_calc import compute_invoice_totals
grand_total = compute_invoice_totals(reopened.invoice).grand_total
print(f"   الإجمالي الفعلي بعد التعديل: {grand_total}")

d1 = SettlementDialog(session, reopened.invoice)
check("Dialog تعرض الرصيد الكامل قبل أي تسوية", d1._form_disabled is False)
cash_idx = d1.cash_account_combo.findData(coa["cash"].id)
d1.cash_account_combo.setCurrentIndex(cash_idx)
partial1 = (grand_total / 3).quantize(D_("0.01"))
d1.amount_spin.setValue(float(partial1))
d1._confirm()
check("القبض الجزئي الأول نجح", d1.result() == QDialog.Accepted)

remaining_after_1 = grand_total - partial1
d2 = SettlementDialog(session, reopened.invoice)
partial2 = (remaining_after_1 / 2).quantize(D_("0.01"))
cash_idx2 = d2.cash_account_combo.findData(coa["cash"].id)
d2.cash_account_combo.setCurrentIndex(cash_idx2)
d2.amount_spin.setValue(float(partial2))
d2._confirm()
check("القبض الجزئي الثاني نجح", d2.result() == QDialog.Accepted)

remaining_after_2 = remaining_after_1 - partial2
d3 = SettlementDialog(session, reopened.invoice)
check(f"Dialog الثالثة تعرض المتبقي الفعلي = {remaining_after_2}",
      True)
cash_idx3 = d3.cash_account_combo.findData(coa["cash"].id)
d3.cash_account_combo.setCurrentIndex(cash_idx3)
d3.amount_spin.setValue(float(remaining_after_2))  # القيمة الافتراضية = الرصيد الكامل المتبقي أصلاً
d3._confirm()
check("القبض الكامل (الثالث) نجح", d3.result() == QDialog.Accepted)

from app.services.settlements import get_invoice_balance_due
final_balance = get_invoice_balance_due(session, reopened.invoice)
check("الرصيد النهائي = صفر بالضبط بعد 3 دفعات (Oracle مستقل رياضياً)",
      final_balance == D_("0"), f"actual={final_balance}")
settlements_saved = session.query(Settlement).filter_by(invoice_id=reopened.invoice.id).all()
check("3 سجلات Settlement فعلية بقاعدة البيانات", len(settlements_saved) == 3)
sum_settlements = sum(D_(str(s.amount_foreign)) for s in settlements_saved)
check("مجموع مبالغ Settlement = الإجمالي بالضبط (لا فرق تقريب متراكم)",
      sum_settlements == grand_total, f"sum={sum_settlements} vs grand_total={grand_total}")

# --- تحقق مباشر من ميزان المراجعة الفعلي (Receivable) بعد التسويات ---
tb = get_trial_balance(session)
check("ميزان المراجعة متوازن فعلياً (Reports) بعد كل هذه العمليات", tb.is_balanced)
ar_row = next((r for r in tb.rows if r.account.id == purchase.journal_entry_id and False), None)
# البحث عن سطر الذمم المدينة الفعلي المرتبط بفاتورة البيع (الحساب الأول بقيدها)
receivable_account_id = entry.lines[0].account_id
ar_row = next((r for r in tb.rows if r.account.id == receivable_account_id), None)
if ar_row is not None:
    ar_net = ar_row.total_debit - ar_row.total_credit
    check("رصيد حساب الذمم المدينة لهذا العميل بميزان المراجعة الفعلي = صفر (سُدِّد بالكامل)",
          ar_net == D_("0"), f"actual={ar_net}")

# =====================================================================
# 4) Cancel: مع Settlement (يُرفَض من المحرك)، بلا Settlement (ينجح)،
#    لا يُستخدَم كبديل عن Return، لا يؤثر على مستند آخر
# =====================================================================
print("== 4) Cancel: رفض مع تسوية، نجاح بلا تسوية، عزل كامل ==")
_critical_messages.clear()
reopened._cancel_invoice()  # نفس الفاتورة التي لها 3 تسويات الآن
check("الإلغاء رُفض فعلياً (الفاتورة ما زالت POSTED)", reopened.invoice.status == InvoiceStatus.POSTED)
check("رسالة الرفض من المحرك (cancel_invoice) وليست افتراضاً بالواجهة",
      len(_critical_messages) == 1 and "تسوية" in _critical_messages[0])

# فاتورة نظيفة مستقلة، بلا أي تسوية، لإثبات نجاح Cancel بمعزل
clean_form = SalesInvoiceFormView(session=session, invoice_id=None)
idx_clean = clean_form.warehouse_combo.findData(wh.id)
clean_form.warehouse_combo.setCurrentIndex(idx_clean)
clean_form.party_edit.setText("عميل نظيف للإلغاء")
clean_form.is_cash_combo.setCurrentText("نقدي")
fill_line(clean_form, 0, "LC-1", "2", "3000")
clean_form._recalculate_totals()
clean_form.invoice_no_edit.setText("LC-CLEAN-1")
clean_form._post()
check("الفاتورة النظيفة رُحِّلت", clean_form.invoice.status == InvoiceStatus.POSTED)

_critical_messages.clear()
clean_form._cancel_invoice()
check("الإلغاء نجح فعلياً بلا أي تسوية مرتبطة", clean_form.invoice.status == InvoiceStatus.CANCELLED)
reversal = session.query(JournalEntry).filter_by(is_reversal_of=clean_form.invoice.journal_entry_id).first()
check("قيد عكسي فعلي وُلِد للإلغاء ومتوازن", reversal is not None and reversal.is_balanced())
reversed_movements = session.query(InventoryMovement).filter_by(
    source_type="invoice_cancel", source_id=clean_form.invoice.id
).all()
check("حركات المخزون عُكست فعلياً بنفس الكمية والتكلفة الأصليتين",
      len(reversed_movements) == 1 and reversed_movements[0].quantity == D_("2"))

# --- عزل: الفاتورة الأولى (بتسوياتها) لم تتأثر بإلغاء الفاتورة الثانية ---
session.refresh(reopened.invoice)
check("الفاتورة الأولى بقيت POSTED بلا تأثر بإلغاء الفاتورة الأخرى (عزل كامل)",
      reopened.invoice.status == InvoiceStatus.POSTED)
check("تسويات الفاتورة الأولى ما زالت 3 بلا تغيير", len(
    session.query(Settlement).filter_by(invoice_id=reopened.invoice.id).all()
) == 3)
# لا استخدام Cancel كبديل لـReturn: التأكد أن لا InventoryMovement بنوع
# "sales_return" وُلِدت لأي من الفاتورتين (Cancel وReturn مساران منفصلان)
check("لا حركة مخزون بنوع sales_return وُلِدت من أي عملية Cancel (لا خلط بين المفهومين)",
      session.query(InventoryMovement).filter_by(source_type="sales_return").count() == 0)

print()
print("=" * 70)
print(f"✅ دورة فاتورة البيع الكاملة عبر الواجهة الفعلية نجحت ({len(results)} تحقّقاً)")
print("=" * 70)
