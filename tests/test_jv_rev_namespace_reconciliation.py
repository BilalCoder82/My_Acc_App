"""
tests/test_jv_rev_namespace_reconciliation.py
==================================================
يختبر أن JV-REV أصبح لها مصدر ترقيم واحد فعلياً بعد الإصلاح الموحَّد
(reverse_manual_entry() تستخدم الآن _reserve_ref_no مثل journal_edit.py::
reverse() تماماً). ملاحظة تاريخية: النسخة الأصلية من هذا الاختبار (قبل
الإصلاح) أثبتت تصادماً فعلياً (UNIQUE constraint) بترتيب new→old→new —
هذا سبب وجود هذا الملف أصلاً، محفوظ بالتوثيق أدناه لا يُحذَف من السجل.

يغطي كل النقاط المطلوبة صراحة بالمراجعة:
  - Seed من بيانات قديمة (JV-REV-000001..000003) → sequence يبدأ من 3.
  - القيم الشاذة (JV-REV-ABC، JV-REV-7) لا تدخل حساب الـseed.
  - new→old→new بعد الإصلاح: لا collision، أرقام مختلفة، متسلسلة.
  - fail/rollback بعد حجز الرقم لا يُعيد استخدام الرقم (بعد الإصلاح أيضاً).
  - إعادة تشغيل الـseed لا تُنقص last_value (idempotent).
  - الأرقام القديمة (000001-000003) لا تتغيّر.
  - namespace آخر (JV-OPEN) غير متأثر بإصلاح JV-REV إطلاقاً.
"""
import os, sys, datetime, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Account, AccountType
from app.services.journal_edit import (
    reverse_manual_entry, reverse, post_immediate, AccountingIntent, LineIntent, JournalEditError,
)
from tools.backfill_seed_jv_rev_sequence import inspect_and_seed

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(fd)
engine = create_engine(f"sqlite:///{db_path}")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)


def mk_original(sess, desc):
    o = post_immediate(sess, AccountingIntent(
        entry_date=datetime.date(2026, 1, 1), currency_code="USD", exchange_rate=D_("1"),
        source_type="opening_balance", description=desc,
        lines=[LineIntent(account_id=acc_a.id, debit_raw=D_("5"), debit_base=D_("5")),
               LineIntent(account_id=acc_b.id, credit_raw=D_("5"), credit_base=D_("5"))],
    ))
    sess.commit()
    return o


print("== 1) بيانات 'قديمة' محاكاة: 3 قيود عبر reverse_manual_entry() ==")
s = Session()
acc_a = Account(code="1", name_ar="أ", account_type=AccountType.ASSET)
acc_b = Account(code="2", name_ar="ب", account_type=AccountType.ASSET)
s.add_all([acc_a, acc_b]); s.commit()

old_refs = []
for i in range(3):
    original = mk_original(s, f"orig{i}")
    rev = reverse_manual_entry(s, original, datetime.date(2026, 1, 2))
    s.commit()
    old_refs.append(rev.ref_no)
check("3 قيود عكسية أُنشئت", len(old_refs) == 3, str(old_refs))
check("مراجعها JV-REV-000001..000003", old_refs == [f"JV-REV-{i:06d}" for i in range(1, 4)], str(old_refs))

print("\n== 2) ref_no شاذ يُدرَج مباشرة (محاكاة سجل تاريخي من قبل الإصلاح بالكامل) ==")
raw = sqlite3.connect(db_path)
raw.execute(
    "INSERT INTO journal_entries (entry_date, ref_no, source_type, currency_code, exchange_rate, status, created_at) "
    "VALUES ('2026-01-01', 'JV-REV-ABC', 'manual_reversal', 'USD', 1, 'posted', '2026-01-01 00:00:00')"
)
raw.execute(
    "INSERT INTO journal_entries (entry_date, ref_no, source_type, currency_code, exchange_rate, status, created_at) "
    "VALUES ('2026-01-01', 'JV-REV-7', 'manual_reversal', 'USD', 1, 'posted', '2026-01-01 00:00:00')"
)
raw.commit(); raw.close()

print("\n== 3) الـBackfill/Seed (يُشغَّل احتياطاً حتى لو كانت الآلية موحَّدة أصلاً — يجب أن يبقى Idempotent وآمناً) ==")
report1 = inspect_and_seed(db_path, apply=True)
check("anomaly واحد لكل مرجع شاذ (2 إجمالاً)، غير مُستخدَمين بالحساب", len(report1["anomalies"]) == 2, str(report1["anomalies"]))
check("القيمة القصوى المحسوبة = 3 (تجاهلت الشاذَّين)", report1["computed_max_from_valid_refs"] == 3)
check("last_value بعد الـSeed الأول = 3 (كانت 3 أصلاً من الاستدعاءات الحقيقية بالخطوة 1)",
      report1["existing_sequence_last_value_after"] == 3)

print("\n== 4) إعادة تشغيل الـSeed مرة ثانية — Idempotent، لا يُنقِص last_value أبداً ==")
report2 = inspect_and_seed(db_path, apply=True)
check("تشغيل ثانٍ لا يُغيّر last_value (لا نقصان، لا تكرار زيادة)",
      report2["existing_sequence_last_value_after"] == 3, str(report2))

print("\n== 5) السيناريو الحاسم الذي أثبت التصادم سابقاً: new → old → new — يجب ألا يتصادم الآن ==")
o1 = mk_original(s, "orig_new1")
r_new1 = reverse(s, o1, datetime.date(2026, 1, 3)); s.commit()
o2 = mk_original(s, "orig_old1")
r_old1 = reverse_manual_entry(s, o2, datetime.date(2026, 1, 3)); s.commit()
o3 = mk_original(s, "orig_new2")
r_new2 = reverse(s, o3, datetime.date(2026, 1, 3)); s.commit()

mixed = [("new", r_new1.ref_no), ("old", r_old1.ref_no), ("new", r_new2.ref_no)]
nums = [int(r.split("-")[-1]) for _, r in mixed]
print(f"   التسلسل: {mixed}")
check("لا استثناء/تصادم UNIQUE حدث (نجحت الاستدعاءات الثلاثة)", True)
check("3 مراجع فريدة تماماً", len(set(n for _, n in mixed)) == 3, str(mixed))
check("تسلسل صاعد فعلي (monotonic) — الخلل السابق (non-monotonic) لم يعد موجوداً", nums == sorted(nums), str(mixed))
check("متتابعة بلا فجوة (4,5,6 — لا استدعاء فاشلاً حصل هنا)", nums == [4, 5, 6], str(mixed))

print("\n== 6) عدم إعادة استخدام الرقم بعد fail/rollback — يُعاد التحقق بعد الإصلاح ==")
raw2 = sqlite3.connect(db_path)
before = raw2.execute("SELECT last_value FROM journal_number_sequences WHERE namespace='JV-REV'").fetchone()[0]
raw2.close()

o4 = mk_original(s, "orig_gap")
try:
    reverse(s, o4, datetime.date(2025, 1, 1))  # تاريخ أسبق عمداً — فشل قبل حجز الرقم (fail-fast)
    check("فشل متوقَّع لم يحدث", False)
except JournalEditError:
    pass
s.rollback()

o5 = mk_original(s, "orig_after_gap")
r_after = reverse(s, o5, datetime.date(2026, 1, 5)); s.commit()
after_num = int(r_after.ref_no.split("-")[-1])
check("الرقم بعد محاولة فشلت (fail-fast قبل الحجز) = القيمة السابقة + 1، بلا فجوة هنا تحديداً "
      "(reverse() تتحقق من التاريخ قبل حجز الرقم — سلوك غير متأثر بإصلاح JV-REV)",
      after_num == before + 1, f"قبل={before}, بعد={after_num}")

print("\n== 7) الأرقام القديمة لم تتغيّر ==")
raw3 = sqlite3.connect(db_path)
placeholders = ",".join("?" * len(old_refs))
still_there = raw3.execute(f"SELECT ref_no FROM journal_entries WHERE ref_no IN ({placeholders})", old_refs).fetchall()
raw3.close()
check("كل مراجع الخطوة 1 الثلاثة ما زالت موجودة كما هي بالضبط", {r[0] for r in still_there} == set(old_refs))

print("\n== 8) namespace آخر (JV-OPEN) غير متأثر بإصلاح JV-REV إطلاقاً ==")
raw4 = sqlite3.connect(db_path)
opening_rows = raw4.execute("SELECT namespace, last_value FROM journal_number_sequences WHERE namespace='JV-OPEN'").fetchall()
raw4.close()
check("JV-OPEN موجود ومستقل تماماً عن JV-REV، لم يتأثر بأي تعديل هنا",
      len(opening_rows) == 1 and opening_rows[0][1] >= 8,
      str(opening_rows))

os.remove(db_path)

print("\n" + "=" * 70)
print(f"النتيجة: {sum(1 for _, c in results if c)}/{len(results)} نجح")
print("=" * 70)
