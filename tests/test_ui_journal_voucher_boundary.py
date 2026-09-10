"""
tests/test_ui_journal_voucher_boundary.py
=============================================
يقود app/ui/accounting/journal_voucher_form.py::JournalVoucherFormView
فعلياً (لا تجاوز الواجهة، لا استدعاء journal_edit.py مباشرة) — أول
اختبار Boundary حقيقي على المسار اليدوي نفسه كما طُلِب صراحة، بعد نقل
_save_draft/_post من add_manual_line/post_manual_entry القديمتين إلى
begin_entry/add_line/remove_line/post (Boundary).
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QMessageBox

from app.models import Base, Account, AccountType, JournalEntryStatus
from app.ui.accounting.journal_voucher_form import (
    JournalVoucherFormView, COL_CODE, COL_DEBIT, COL_CREDIT,
)

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


app = QApplication.instance() or QApplication([])
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)  # تأكيد تلقائي — لا تفاعل بشري بالاختبار
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)


def fresh_env():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    cash = Account(code="100", name_ar="الصندوق", account_type=AccountType.ASSET)
    exp = Account(code="500", name_ar="مصاريف", account_type=AccountType.EXPENSE)
    inactive = Account(code="999", name_ar="غير نشط", account_type=AccountType.ASSET, is_active=False)
    group = Account(code="888", name_ar="تجميعي", account_type=AccountType.ASSET, is_group=True)
    session.add_all([cash, exp, inactive, group])
    from app.models import Setting
    session.add(Setting(key="base_currency", value="USD"))
    session.commit()
    return session, cash, exp, inactive, group


def fill_row(form, row, account_code, debit="", credit=""):
    form.grid.setItem(row, COL_CODE, QTableWidgetItem(account_code))
    form.grid.setItem(row, COL_DEBIT, QTableWidgetItem(debit))
    form.grid.setItem(row, COL_CREDIT, QTableWidgetItem(credit))


print("== 1) حفظ مسودة سليمة عبر الواجهة الفعلية (لا journal_edit مباشرة) ==")
s, cash, exp, inactive, group = fresh_env()
form = JournalVoucherFormView(s)
fill_row(form, 0, "100", debit="100")
fill_row(form, 1, "500", credit="100")
form._save_draft()
check("القيد أُنشئ فعلياً كـDRAFT", form.entry is not None and form.entry.status == JournalEntryStatus.DRAFT)
check("ref_no من namespace الـSequence الجديد 'JV' تحديداً (لا الصيغة القديمة LIKE-based)",
      form.entry.ref_no == "JV-000001", f"الفعلي: {form.entry.ref_no}")
check("سطران بالضبط أُنشئا عبر add_line", len(form.entry.lines) == 2)

print("\n== 2) الترحيل عبر الواجهة الفعلية ==")
form._post()
check("الحالة أصبحت POSTED فعلياً بعد _post() من الواجهة", form.entry.status == JournalEntryStatus.POSTED)

print("\n== 3) حساب غير نشط عبر الواجهة (New Draft) — Atomicity: لا يُحفَظ شيء إطلاقاً ==")
s2, cash2, exp2, inactive2, group2 = fresh_env()
form2 = JournalVoucherFormView(s2)
fill_row(form2, 0, "999", debit="50")  # inactive
fill_row(form2, 1, "100", credit="50")
saved = form2._save_draft()
check("لم يُحفَظ (رجعت False)", saved is False)
check("لا قيد أُنشئ إطلاقاً — self.entry بقيت None (لا حفظ جزئي)", form2.entry is None)
from app.models import JournalEntry as _JE
check("لا صف واحد بقاعدة البيانات لهذه المحاولة", s2.query(_JE).count() == 0)

print("\n== 4) حساب تجميعي عبر الواجهة (New Draft) — نفس المبدأ ==")
s3, cash3, exp3, inactive3, group3 = fresh_env()
form3 = JournalVoucherFormView(s3)
fill_row(form3, 0, "888", debit="50")  # group
fill_row(form3, 1, "100", credit="50")
saved = form3._save_draft()
check("لم يُحفَظ (رجعت False)", saved is False)
check("لا قيد أُنشئ إطلاقاً", form3.entry is None)

print("\n== 5) POSTED immutability عبر الواجهة — لا حفظ ثانٍ بعد الترحيل يغيّر الأسطر ==")
lines_before = [(l.account_id, str(l.debit), str(l.credit)) for l in form.entry.lines]
form._save_draft()  # محاولة إعادة حفظ بعد الترحيل
lines_after = [(l.account_id, str(l.debit), str(l.credit)) for l in form.entry.lines]
check("محتوى الأسطر لم يتغيّر بعد محاولة حفظ لاحقة على قيد POSTED (ensure_editable منعت التعديل الفعلي)",
      lines_before == lines_after, f"قبل: {lines_before}, بعد: {lines_after}")

print("\n== 6) ref_no تسلسلي عبر عدة قيود متتالية من الواجهة (namespace JV معزول) ==")
s4, cash4, exp4, _, _ = fresh_env()
refs = []
for i in range(3):
    f = JournalVoucherFormView(s4)
    fill_row(f, 0, "100", debit="10")
    fill_row(f, 1, "500", credit="10")
    f._save_draft()
    refs.append(f.entry.ref_no)
check("3 قيود متتالية عبر الواجهة → JV-000001/2/3 بالضبط", refs == ["JV-000001", "JV-000002", "JV-000003"], str(refs))

print("\n== 7) الحالة A — مسودة صحيحة موجودة، تُعدَّل لتحوي سطراً غير صالح + سطراً صالحاً ==")
print("       يجب أن تبقى المسودة القديمة كما هي تماماً، لا تُحذَف ولا تتغيّر")
s5, cash5, exp5, inactive5, _ = fresh_env()
form5 = JournalVoucherFormView(s5)
fill_row(form5, 0, "100", debit="200")
fill_row(form5, 1, "500", credit="200")
assert form5._save_draft() is True
old_ref = form5.entry.ref_no
old_lines_snapshot = sorted([(l.account_id, str(l.debit), str(l.credit)) for l in form5.entry.lines])
old_entry_id = form5.entry.id

# الآن نُفسد الشبكة: سطر بحساب غير نشط + سطر صالح، محاولة حفظ فوق نفس المسودة
form5.grid.setItem(0, COL_CODE, QTableWidgetItem("999"))  # inactive الآن بدل 100
saved5 = form5._save_draft()
check("رجعت False (رُفِضت المحاولة الثانية بالكامل)", saved5 is False)
check("نفس كائن القيد لم يتغيّر id ولا ref_no", form5.entry.id == old_entry_id and form5.entry.ref_no == old_ref)
new_lines_snapshot = sorted([(l.account_id, str(l.debit), str(l.credit)) for l in form5.entry.lines])
check("محتوى الأسطر مطابق تماماً لما قبل المحاولة الفاشلة — لم يُحذَف شيء ولم يتغيّر",
      old_lines_snapshot == new_lines_snapshot, f"قبل: {old_lines_snapshot}, بعد: {new_lines_snapshot}")

print("\n== 8) الحالة B — مسودة جديدة (لا قيد سابق) بسطر غير صالح + سطر صالح → لا حفظ جزئي ==")
s6, cash6, exp6, inactive6, _ = fresh_env()
form6 = JournalVoucherFormView(s6)
fill_row(form6, 0, "999", debit="80")  # inactive
fill_row(form6, 1, "100", credit="80")  # صالح
saved6 = form6._save_draft()
check("رجعت False", saved6 is False)
check("لا قيد أُنشئ إطلاقاً بالجلسة (لا سطر واحد صالح تم تخزينه منفرداً)", form6.entry is None)
check("لا أي JournalEntry بقاعدة البيانات لهذه المحاولة", s6.query(_JE).count() == 0)

print("\n== 9) الحالة C — Post مباشرة بسطر غير صالح + سطر صالح على مسودة جديدة → لا ترحيل جزئي ==")
s7, cash7, exp7, inactive7, _ = fresh_env()
form7 = JournalVoucherFormView(s7)
fill_row(form7, 0, "999", debit="90")  # inactive
fill_row(form7, 1, "100", credit="90")  # صالح
form7._post()  # يستدعي _save_draft() داخلياً، يجب أن يتوقف قبل أي محاولة ترحيل
check("لا قيد أُنشئ إطلاقاً — _post() لم يتابع للترحيل بعد فشل الحفظ", form7.entry is None)
check("لا أي JournalEntry بقاعدة البيانات — لا POSTED جزئي ولا DRAFT جزئي", s7.query(_JE).count() == 0)

print("\n== 10) عطل بنية تحتية غير متوقَّع أثناء الكتابة الفعلية (بعد نجاح كل التحقق) — Defensive Rollback ==")
import app.ui.accounting.journal_voucher_form as _jvf
s8, cash8, exp8, _, _ = fresh_env()
form8 = JournalVoucherFormView(s8)
fill_row(form8, 0, "100", debit="70")
fill_row(form8, 1, "500", credit="70")
_orig_add_line = _jvf.add_line
_jvf.add_line = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("محاكاة عطل بنية تحتية"))
try:
    saved8 = form8._save_draft()
finally:
    _jvf.add_line = _orig_add_line
check("رجعت False عند عطل غير متوقَّع بمنتصف الكتابة", saved8 is False)
check("self.entry أُعيدت None (لم تبقَ كائناً معلَّقاً بعد rollback)", form8.entry is None)
check("لا أي JournalEntry يتيم بقاعدة البيانات", s8.query(_JE).count() == 0)

print("\n" + "=" * 70)
print(f"النتيجة: {sum(1 for _, c in results if c)}/{len(results)} نجح")
print("=" * 70)
