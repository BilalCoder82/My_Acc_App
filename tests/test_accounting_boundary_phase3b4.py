"""
tests/test_accounting_boundary_phase3b4.py
==============================================
اختبارات Boundary مستقلة — journal_edit.py::begin_entry/add_line/post/
post_immediate/reverse (PHASE3B4_DESIGN_SPEC.md §1-§5). هذه ليست
Characterization Scenarios (لا تختبر سلوك دومين قائم) — تختبر الـBoundary
الجديد نفسه بمعزل عن أي دومين يستهلكه.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Account, AccountType, JournalEntryStatus
from app.services.journal_edit import (
    begin_entry, add_line, post, post_immediate, reverse,
    AccountingIntent, LineIntent, JournalEditError,
    _NAMESPACE_BY_SOURCE_TYPE,
)

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
    session = sessionmaker(bind=engine)()
    active = Account(code="1", name_ar="نشط", account_type=AccountType.ASSET)
    inactive = Account(code="2", name_ar="غير نشط", account_type=AccountType.ASSET, is_active=False)
    group = Account(code="3", name_ar="تجميعي", account_type=AccountType.ASSET, is_group=True)
    other = Account(code="4", name_ar="آخر", account_type=AccountType.ASSET)
    session.add_all([active, inactive, group, other])
    session.commit()
    return session, active, inactive, group, other


print("== 1) inactive account → reject ==")
s, active, inactive, group, other = fresh_env()
try:
    add_line(s, begin_entry(s, datetime.date(2026, 1, 1), "USD", 1, "opening_balance", "x"),
              inactive.id, debit_raw=D_("10"), debit_base=D_("10"))
    check("رفض حساب غير نشط", False)
except JournalEditError:
    check("رفض حساب غير نشط", True)
s.rollback()

print("\n== 2) group account → reject ==")
s, active, inactive, group, other = fresh_env()
try:
    add_line(s, begin_entry(s, datetime.date(2026, 1, 1), "USD", 1, "opening_balance", "x"),
              group.id, debit_raw=D_("10"), debit_base=D_("10"))
    check("رفض حساب تجميعي", False)
except JournalEditError:
    check("رفض حساب تجميعي", True)
s.rollback()

print("\n== 3) debit + credit معاً → reject ==")
s, active, inactive, group, other = fresh_env()
try:
    add_line(s, begin_entry(s, datetime.date(2026, 1, 1), "USD", 1, "opening_balance", "x"),
              active.id, debit_raw=D_("10"), credit_raw=D_("5"), debit_base=D_("10"), credit_base=D_("5"))
    check("رفض مدين ودائن معاً", False)
except JournalEditError:
    check("رفض مدين ودائن معاً", True)
s.rollback()

print("\n== 4) (0,0) → reject ==")
s, active, inactive, group, other = fresh_env()
try:
    add_line(s, begin_entry(s, datetime.date(2026, 1, 1), "USD", 1, "opening_balance", "x"), active.id)
    check("رفض سطر (0,0)", False)
except JournalEditError:
    check("رفض سطر (0,0)", True)
s.rollback()

print("\n== 5) قيمة raw سالبة → reject ==")
s, active, inactive, group, other = fresh_env()
try:
    add_line(s, begin_entry(s, datetime.date(2026, 1, 1), "USD", 1, "opening_balance", "x"),
              active.id, debit_raw=D_("-5"), debit_base=D_("-5"))
    check("رفض debit_raw سالب", False)
except JournalEditError:
    check("رفض debit_raw سالب", True)
s.rollback()

print("\n== 6) قيمة base سالبة (raw موجب، base سالب — عمداً غير متسقين لاختبار التحقق) → reject ==")
s, active, inactive, group, other = fresh_env()
try:
    add_line(s, begin_entry(s, datetime.date(2026, 1, 1), "USD", 1, "opening_balance", "x"),
              active.id, debit_raw=D_("5"), debit_base=D_("-5"))
    check("رفض debit_base سالب", False)
except JournalEditError:
    check("رفض debit_base سالب", True)
s.rollback()

print("\n== 7) rate <= 0 → reject ==")
s, active, inactive, group, other = fresh_env()
try:
    add_line(s, begin_entry(s, datetime.date(2026, 1, 1), "USD", 1, "opening_balance", "x"),
              active.id, debit_raw=D_("5"), debit_base=D_("5"), line_exchange_rate=D_("0"))
    check("رفض rate=0", False)
except JournalEditError:
    check("رفض rate=0", True)
s.rollback()
s, active, inactive, group, other = fresh_env()
try:
    add_line(s, begin_entry(s, datetime.date(2026, 1, 1), "USD", 1, "opening_balance", "x"),
              active.id, debit_raw=D_("5"), debit_base=D_("5"), line_exchange_rate=D_("-1"))
    check("رفض rate سالب", False)
except JournalEditError:
    check("رفض rate سالب", True)
s.rollback()

print("\n== 8) لا أسطر عند POST → reject ==")
s, active, inactive, group, other = fresh_env()
entry = begin_entry(s, datetime.date(2026, 1, 1), "USD", 1, "opening_balance", "x")
try:
    post(s, entry)
    check("رفض POST بلا أسطر", False)
except JournalEditError:
    check("رفض POST بلا أسطر", True)
s.rollback()

print("\n== 9) غير متوازن بالعملة الأساسية → reject ==")
s, active, inactive, group, other = fresh_env()
try:
    post_immediate(s, AccountingIntent(
        entry_date=datetime.date(2026, 1, 1), currency_code="USD", exchange_rate=D_("1"),
        source_type="opening_balance", description="x",
        lines=[LineIntent(account_id=active.id, debit_raw=D_("100"), debit_base=D_("100"))],
    ))
    check("رفض قيد غير متوازن", False)
except JournalEditError:
    check("رفض قيد غير متوازن", True)
s.rollback()

print("\n== 10) POSTED immutability — لا add_line ولا post ثانٍ على قيد مُرحَّل ==")
s, active, inactive, group, other = fresh_env()
posted_entry = post_immediate(s, AccountingIntent(
    entry_date=datetime.date(2026, 1, 1), currency_code="USD", exchange_rate=D_("1"),
    source_type="opening_balance", description="x",
    lines=[
        LineIntent(account_id=active.id, debit_raw=D_("100"), debit_base=D_("100")),
        LineIntent(account_id=other.id, credit_raw=D_("100"), credit_base=D_("100")),
    ],
))
s.commit()
try:
    add_line(s, posted_entry, active.id, debit_raw=D_("1"), debit_base=D_("1"))
    check("رفض إضافة سطر لقيد POSTED", False)
except JournalEditError:
    check("رفض إضافة سطر لقيد POSTED", True)
try:
    post(s, posted_entry)
    check("رفض post() ثانٍ لقيد POSTED أصلاً", False)
except JournalEditError:
    check("رفض post() ثانٍ لقيد POSTED أصلاً", True)

print("\n== 11) valid reversal ==")
rev = reverse(s, posted_entry, datetime.date(2026, 1, 2))
check("عكس صحيح تم إنشاؤه", rev.status == JournalEntryStatus.POSTED and rev.is_reversal_of == posted_entry.id)
check("عكس القيد متوازن ومقلوب فعلياً",
      rev.lines[0].debit == posted_entry.lines[0].credit and rev.lines[0].credit == posted_entry.lines[0].debit)
s.commit()

print("\n== 12) reversal date < original date → reject ==")
s2, active2, _, _, other2 = fresh_env()
e2 = post_immediate(s2, AccountingIntent(
    entry_date=datetime.date(2026, 5, 1), currency_code="USD", exchange_rate=D_("1"),
    source_type="opening_balance", description="x",
    lines=[LineIntent(account_id=active2.id, debit_raw=D_("50"), debit_base=D_("50")),
           LineIntent(account_id=other2.id, credit_raw=D_("50"), credit_base=D_("50"))],
))
s2.commit()
try:
    reverse(s2, e2, datetime.date(2026, 4, 1))  # أسبق من entry_date
    check("رفض تاريخ عكس أسبق من تاريخ القيد الأصلي", False)
except JournalEditError:
    check("رفض تاريخ عكس أسبق من تاريخ القيد الأصلي", True)

print("\n== 13) عكس العكس (reversal of reversal) → reject ==")
rev2 = reverse(s2, e2, datetime.date(2026, 5, 2))
s2.commit()
try:
    reverse(s2, rev2, datetime.date(2026, 5, 3))
    check("رفض عكس قيد هو نفسه عكسي", False)
except JournalEditError:
    check("رفض عكس قيد هو نفسه عكسي", True)

print("\n== 14) عكس مزدوج لنفس القيد الأصلي (double reversal) → reject ==")
try:
    reverse(s2, e2, datetime.date(2026, 5, 4))
    check("رفض عكس ثانٍ لنفس القيد الأصلي", False)
except JournalEditError:
    check("رفض عكس ثانٍ لنفس القيد الأصلي", True)

print("\n== 15) ref_no uniqueness / سلوك الـSequence ==")
s3, a3, _, _, b3 = fresh_env()
refs = []
for i in range(5):
    e = post_immediate(s3, AccountingIntent(
        entry_date=datetime.date(2026, 1, 1), currency_code="USD", exchange_rate=D_("1"),
        source_type="opening_balance", description=f"x{i}",
        lines=[LineIntent(account_id=a3.id, debit_raw=D_("10"), debit_base=D_("10")),
               LineIntent(account_id=b3.id, credit_raw=D_("10"), credit_base=D_("10"))],
    ))
    s3.commit()
    refs.append(e.ref_no)
check("5 أرقام مرجعية فريدة تماماً بلا تكرار", len(set(refs)) == 5, str(refs))
check("الأرقام متتابعة بلا فجوة (لا فشل حصل هنا)", refs == [f"JV-OPEN-{i:06d}" for i in range(1, 6)], str(refs))
check("تنسيق 6 أرقام حسب §1", all(r.split("-")[-1].__len__() == 6 for r in refs))

print("\n== 16) namespace غير مسجَّل → JournalEditError واضحة (لا KeyError خام) ==")
s4, a4, _, _, _ = fresh_env()
try:
    begin_entry(s4, datetime.date(2026, 1, 1), "USD", 1, "some_unmigrated_domain", "x")
    check("رفض source_type بلا namespace مُسجَّل", False)
except JournalEditError:
    check("رفض source_type بلا namespace مُسجَّل", True)

print("\n== 17) حارس دفاعي: جلسة بتغييرات معلَّقة (بلا commit) على قاعدة **ملف حقيقية** → رفض واضح لا قفل غامض ==")
import tempfile as _tempfile
_fd, _file_db_path = _tempfile.mkstemp(suffix=".db")
os.close(_fd)
_file_engine = create_engine(f"sqlite:///{_file_db_path}")
Base.metadata.create_all(_file_engine)
s17 = sessionmaker(bind=_file_engine)()
stray = Account(code="999", name_ar="حساب معلَّق", account_type=AccountType.ASSET)
s17.add(stray)  # session.new غير فارغة عمداً، بلا flush/commit
try:
    begin_entry(s17, datetime.date(2026, 1, 1), "USD", D_("1"), "opening_balance", "x")
    check("رفض حجز الرقم مع وجود تغييرات معلَّقة (ملف حقيقي)", False)
except JournalEditError as e:
    check("رفض واضح ومفهوم (لا OperationalError خام)", "تغييرات معلَّقة" in str(e))
s17.close()
os.remove(_file_db_path)

print("\n== 18) reverse() API الموسَّعة (Group 3-C) — التوافق العكسي أولاً: القيم الافتراضية تُنتِج نفس السلوك السابق حرفياً ==")
s18, a18, _, _, b18 = fresh_env()
orig18 = post_immediate(s18, AccountingIntent(
    entry_date=datetime.date(2026, 1, 1), currency_code="USD", exchange_rate=D_("1"),
    source_type="opening_balance", description="x",
    lines=[LineIntent(account_id=a18.id, debit_raw=D_("10"), debit_base=D_("10")),
           LineIntent(account_id=b18.id, credit_raw=D_("10"), credit_base=D_("10"))],
))
s18.commit()
rev18_default = reverse(s18, orig18, datetime.date(2026, 1, 2))  # بلا source_type/source_id — يجب أن يبقى كالسابق تماماً
check("source_type الافتراضي ما زال 'manual_reversal' كما كان دائماً", rev18_default.source_type == "manual_reversal")
check("source_id الافتراضي None كما كان دائماً", rev18_default.source_id is None)

print("\n== 19) reverse() بـsource_type مخصَّص + source_id صريح — العقد الجديد يعمل ==")
s19, a19, _, _, b19 = fresh_env()
orig19 = post_immediate(s19, AccountingIntent(
    entry_date=datetime.date(2026, 1, 1), currency_code="USD", exchange_rate=D_("1"),
    source_type="opening_balance", description="x",
    lines=[LineIntent(account_id=a19.id, debit_raw=D_("10"), debit_base=D_("10")),
           LineIntent(account_id=b19.id, credit_raw=D_("10"), credit_base=D_("10"))],
))
s19.commit()
rev19 = reverse(s19, orig19, datetime.date(2026, 1, 2), source_type="invoice_cancel", source_id=999)
check("source_type مُخصَّص طُبِّق فعلياً", rev19.source_type == "invoice_cancel")
check("source_id مُخصَّص طُبِّق فعلياً", rev19.source_id == 999)
check("ما زالت تحجز من نفس namespace JV-REV بصرف النظر عن source_type", rev19.ref_no.startswith("JV-REV-"))

print("\n== 20) العقد المفروض: source_type مخصَّص بلا source_id → رفض صريح (لا سجل غير قابل للتتبع) ==")
s20, a20, _, _, b20 = fresh_env()
orig20 = post_immediate(s20, AccountingIntent(
    entry_date=datetime.date(2026, 1, 1), currency_code="USD", exchange_rate=D_("1"),
    source_type="opening_balance", description="x",
    lines=[LineIntent(account_id=a20.id, debit_raw=D_("10"), debit_base=D_("10")),
           LineIntent(account_id=b20.id, credit_raw=D_("10"), credit_base=D_("10"))],
))
s20.commit()
try:
    reverse(s20, orig20, datetime.date(2026, 1, 2), source_type="invoice_cancel")  # بلا source_id عمداً
    check("رفض source_type مخصَّص بلا source_id", False)
except JournalEditError as e:
    check("رفض source_type مخصَّص بلا source_id", "source_id" in str(e))

print("\n" + "=" * 70)
print(f"النتيجة: {sum(1 for _, c in results if c)}/{len(results)} نجح")
print("=" * 70)
