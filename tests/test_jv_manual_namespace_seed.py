"""
tests/test_jv_manual_namespace_seed.py
==========================================
يختبر tools/backfill_seed_jv_manual_sequence.py: بيانات JV-NNNNNN قديمة
(محاكاة الآلية القديمة LIKE "JV-%" بـjournal_voucher_form.py) + Seed
+ begin_entry() الجديدة بعده → لا تصادم، الرقم التالي صحيح. يتحقق أيضاً
أن namespaces أخرى بنفس البادئة "JV-" (JV-OPEN، JV-REV، JV-OPNPTY) لا
تُخلَط بحساب الـSeed لهذا الـnamespace تحديداً.
"""
import os, sys, datetime, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Account, AccountType
from app.services.journal_edit import begin_entry, add_line, post
from tools.backfill_seed_jv_manual_sequence import inspect_and_seed

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
s = Session()
a = Account(code="1", name_ar="أ", account_type=AccountType.ASSET)
b = Account(code="2", name_ar="ب", account_type=AccountType.ASSET)
s.add_all([a, b]); s.commit()

print("== 1) محاكاة بيانات قديمة: 3 قيود JV-NNNNNN + قيود من namespaces أخرى بنفس البادئة ==")
raw = sqlite3.connect(db_path)
seed_rows = [
    ("JV-000001", "manual"), ("JV-000002", "manual"), ("JV-000003", "manual"),
    ("JV-OPEN-000001", "opening_balance"),
    ("JV-REV-000001", "manual_reversal"),
    ("JV-OPNPTY-000001", "opening_party_entry"),
    ("JV-XYZ", "manual"),  # anomaly حقيقي لهذا الـnamespace تحديداً
]
for ref, st in seed_rows:
    raw.execute(
        "INSERT INTO journal_entries (entry_date, ref_no, source_type, currency_code, exchange_rate, status, created_at) "
        "VALUES ('2026-01-01', ?, ?, 'USD', 1, 'posted', '2026-01-01 00:00:00')", (ref, st),
    )
raw.commit(); raw.close()

print("\n== 2) تشغيل الـSeed ==")
report = inspect_and_seed(db_path, apply=True)
check("مراجع JV اليدوي الصالحة = 3 فقط (لا تخلط مع namespaces أخرى بنفس البادئة)",
      report["valid_jv_manual_refs_count"] == 3, str(report))
check("القيمة القصوى المحسوبة = 3", report["computed_max_from_valid_refs"] == 3)
check("last_value بعد الـSeed = 3", report["existing_sequence_last_value_after"] == 3)

print("\n== 3) إعادة تشغيل الـSeed — Idempotent ==")
report2 = inspect_and_seed(db_path, apply=True)
check("لا نقصان عند إعادة التشغيل", report2["existing_sequence_last_value_after"] == 3)

print("\n== 4) begin_entry() الجديدة بعد الـSeed — لا تصادم مع البيانات القديمة ==")
entry = begin_entry(s, datetime.date(2026, 1, 2), "USD", D_("1"), "manual", "test after seed")
check("الرقم الجديد = JV-000004 تحديداً (التالي الصحيح بعد الثلاثة القديمة)",
      entry.ref_no == "JV-000004", f"الفعلي: {entry.ref_no}")

print("\n== 5) الأرقام القديمة الثلاثة ما زالت موجودة كما هي ==")
raw2 = sqlite3.connect(db_path)
still = raw2.execute("SELECT ref_no FROM journal_entries WHERE ref_no IN ('JV-000001','JV-000002','JV-000003')").fetchall()
raw2.close()
check("الثلاثة موجودون بلا تغيير", {r[0] for r in still} == {"JV-000001", "JV-000002", "JV-000003"})

os.remove(db_path)

print("\n" + "=" * 70)
print(f"النتيجة: {sum(1 for _, c in results if c)}/{len(results)} نجح")
print("=" * 70)
