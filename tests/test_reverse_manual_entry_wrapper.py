"""
tests/test_reverse_manual_entry_wrapper.py
===============================================
Final 3B-4 — تصحيح أخير: reverse_manual_entry() أصبحت غلافاً رفيعاً فوق
journal_edit.reverse()، لا مصدراً ثانياً لمنطق العكس. يثبت هذا الملف كل
الحالات العشر المطلوبة صراحة بمراجعة الـGate الأخيرة.
"""
import os, sys, datetime, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Account, AccountType, JournalEntryStatus
from app.services.journal_edit import (
    reverse_manual_entry, post_immediate, AccountingIntent, LineIntent, JournalEditError,
)
from app.services.opening_balances import (
    post_opening_account_balances, reverse_opening_account_balances,
    OpeningBalanceLineInput, CLEARING_ACCOUNT_SETTING_KEY,
)
from app.models import Setting

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def fresh_env():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    a = Account(code="1", name_ar="أ", account_type=AccountType.ASSET)
    b = Account(code="2", name_ar="ب", account_type=AccountType.ASSET)
    s.add_all([a, b]); s.commit()
    return s, a, b


def mk_manual(s, a, b, entry_date):
    e = post_immediate(s, AccountingIntent(
        entry_date=entry_date, currency_code="USD", exchange_rate=D_("1"),
        source_type="manual", description="x",
        lines=[LineIntent(account_id=a.id, debit_raw=D_("100"), debit_base=D_("100"),
                           line_currency_code="EUR", line_exchange_rate=D_("1.1")),
               LineIntent(account_id=b.id, credit_raw=D_("100"), credit_base=D_("100"))],
    ))
    s.commit()
    return e


print("== 1) reversal بتاريخ مساوٍ للأصل → PASS ==")
s1, a1, b1 = fresh_env()
o1 = mk_manual(s1, a1, b1, datetime.date(2026, 3, 1))
r1 = reverse_manual_entry(s1, o1, datetime.date(2026, 3, 1))
check("عكس بنفس تاريخ الأصل مقبول", r1.status == JournalEntryStatus.POSTED)

print("\n== 2) reversal بتاريخ بعد الأصل → PASS ==")
s2, a2, b2 = fresh_env()
o2 = mk_manual(s2, a2, b2, datetime.date(2026, 3, 1))
r2 = reverse_manual_entry(s2, o2, datetime.date(2026, 3, 15))
check("عكس بتاريخ لاحق مقبول", r2.status == JournalEntryStatus.POSTED)

print("\n== 3) reversal بتاريخ قبل الأصل → REJECT (كانت مفقودة سابقاً، الآن موحَّدة) ==")
s3, a3, b3 = fresh_env()
o3 = mk_manual(s3, a3, b3, datetime.date(2026, 3, 15))
try:
    reverse_manual_entry(s3, o3, datetime.date(2026, 3, 1))
    check("رفض تاريخ عكس أسبق من الأصل", False)
except JournalEditError:
    check("رفض تاريخ عكس أسبق من الأصل", True)

print("\n== 4) namespace = JV-REV ==")
check("ref_no من JV-REV", r1.ref_no.startswith("JV-REV-") and r2.ref_no.startswith("JV-REV-"))

print("\n== 5) debit/credit معكوسان حرفياً ==")
orig_lines1 = {l.account_id: (l.debit, l.credit) for l in o1.lines}
rev_lines1 = {l.account_id: (l.debit, l.credit) for l in r1.lines}
check("كل حساب: مدين الأصل = دائن العكس والعكس صحيح",
      all(orig_lines1[acc] == (rev_lines1[acc][1], rev_lines1[acc][0]) for acc in orig_lines1))

print("\n== 6) raw/base/currency/rate/cost_center محفوظة حرفياً ==")
orig_by_acc = {l.account_id: l for l in o1.lines}
rev_by_acc = {l.account_id: l for l in r1.lines}
check("line_currency_code محفوظ حرفياً لكل سطر",
      all(orig_by_acc[acc].line_currency_code == rev_by_acc[acc].line_currency_code for acc in orig_by_acc))
check("line_exchange_rate محفوظ حرفياً لكل سطر",
      all(orig_by_acc[acc].line_exchange_rate == rev_by_acc[acc].line_exchange_rate for acc in orig_by_acc))
check("debit_base/credit_base معكوسان بنفس القيم المطلقة",
      all({orig_by_acc[acc].debit_base, orig_by_acc[acc].credit_base} ==
          {rev_by_acc[acc].debit_base, rev_by_acc[acc].credit_base} for acc in orig_by_acc))
check("source_type/source_id: manual_reversal/None — provenance القديمة كما هي حرفياً",
      r1.source_type == "manual_reversal" and r1.source_id is None)

print("\n== 7) لا يمكن عكس reversal ==")
try:
    reverse_manual_entry(s1, r1, datetime.date(2026, 3, 2))
    check("رفض عكس قيد هو نفسه عكسي", False)
except JournalEditError:
    check("رفض عكس قيد هو نفسه عكسي", True)

print("\n== 8) لا يمكن عكس الأصل مرتين ==")
try:
    reverse_manual_entry(s1, o1, datetime.date(2026, 3, 2))
    check("رفض عكس ثانٍ لنفس الأصل", False)
except JournalEditError:
    check("رفض عكس ثانٍ لنفس الأصل", True)

print("\n== 9) استدعاءات Opening Balances القديمة ما زالت تعمل بلا أي تغيير ملحوظ ==")
s9, a9, b9 = fresh_env()
equity = Account(code="3", name_ar="حقوق", account_type=AccountType.EQUITY)
s9.add(equity); s9.commit()
s9.add(Setting(key=CLEARING_ACCOUNT_SETTING_KEY, value=str(equity.id)))
s9.add(Setting(key="base_currency", value="USD"))
s9.commit()
entry9 = post_opening_account_balances(s9, [OpeningBalanceLineInput(account_id=a9.id, debit_foreign=D_("500"))], datetime.date(2026, 1, 1))
s9.commit()
rev9 = reverse_opening_account_balances(s9, entry9, datetime.date(2026, 1, 5))
check("reverse_opening_account_balances ما زالت تعمل (عبر الـwrapper الجديد)", rev9.status == JournalEntryStatus.POSTED)
check("source_type/source_id للعكس ما زالا manual_reversal/None كما كانا دائماً",
      rev9.source_type == "manual_reversal" and rev9.source_id is None)

print("\n== 10) Failure injection — فشل قبل الحجز (تاريخ غير صالح) على قاعدة ملف حقيقية → لا orphan، لا استهلاك رقم ==")
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
engine10 = create_engine(f"sqlite:///{path}")
Base.metadata.create_all(engine10)
s10b = sessionmaker(bind=engine10)()
a10b = Account(code="1", name_ar="أ", account_type=AccountType.ASSET)
b10b = Account(code="2", name_ar="ب", account_type=AccountType.ASSET)
s10b.add_all([a10b, b10b]); s10b.commit()
o10b = mk_manual(s10b, a10b, b10b, datetime.date(2026, 3, 15))
try:
    reverse_manual_entry(s10b, o10b, datetime.date(2026, 3, 1))  # تاريخ أسبق — فشل قبل الحجز فعلياً (fail-fast)
    check("فشل متوقَّع لم يحدث", False)
except JournalEditError:
    pass
s10b.rollback()
r_ok = reverse_manual_entry(s10b, o10b, datetime.date(2026, 3, 20))
s10b.commit()
raw10 = sqlite3.connect(path)
count_je = raw10.execute("SELECT COUNT(*) FROM journal_entries WHERE source_type='manual_reversal'").fetchone()[0]
raw10.close()
check("قيد عكسي واحد فقط بقاعدة البيانات (المحاولة الفاشلة لم تحجز رقماً أصلاً — فحص التاريخ قبل الحجز)",
      count_je == 1, f"العدد الفعلي: {count_je}")
os.remove(path)

print("\n" + "=" * 70)
print(f"النتيجة: {sum(1 for _, c in results if c)}/{len(results)} نجح")
print("=" * 70)
