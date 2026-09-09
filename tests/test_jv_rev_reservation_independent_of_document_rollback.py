"""
tests/test_jv_rev_reservation_independent_of_document_rollback.py
======================================================================
يختبر بالضبط السيناريو الذي طلبته المراجعة بعد إصلاح JV-REV: حجز رقم
→ فشل *بعد* الحجز (لا قبله) → rollback للمستند → طلب جديد → يجب أن
يحصل على N+1 لا N. الفشل هنا حقيقي وواقعي: قيد أصلي فاسد التوازن (يحاكي
بيانات تالفة سابقة) — journal_edit.py::reverse() تعكسه حرفياً كما هو
(لا تعيد حساب التوازن)، فيرث القيد العكسي نفس الخلل، فيفشل فحص
is_balanced() الداخلي — وهذا يقع *بعد* _reserve_ref_no() تماماً حسب
ترتيب الكود بالفعل (راجع journal_edit.py::reverse).
"""
import os, sys, datetime, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Account, AccountType
from app.services.journal_edit import reverse, post_immediate, AccountingIntent, LineIntent, JournalEditError

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


def mk(sess, desc):
    o = post_immediate(sess, AccountingIntent(
        entry_date=datetime.date(2026, 1, 1), currency_code="USD", exchange_rate=D_("1"),
        source_type="opening_balance", description=desc,
        lines=[LineIntent(account_id=a.id, debit_raw=D_("10"), debit_base=D_("10")),
               LineIntent(account_id=b.id, credit_raw=D_("10"), credit_base=D_("10"))],
    ))
    sess.commit()
    return o


print("== 1) قيد أصلي، ثم إفساد توازنه مباشرة بقاعدة البيانات (يحاكي بيانات تالفة سابقة) ==")
o1 = mk(s, "orig_will_fail_after_reservation")
raw = sqlite3.connect(db_path)
raw.execute("UPDATE journal_lines SET debit_base = 999 WHERE entry_id = ? AND debit_base > 0", (o1.id,))
raw.commit(); raw.close()

raw2 = sqlite3.connect(db_path)
before = raw2.execute("SELECT last_value FROM journal_number_sequences WHERE namespace='JV-REV'").fetchone()
raw2.close()
check("لا صف Sequence لـJV-REV بعد (لا استدعاء سابق)", before is None, str(before))

print("\n== 2) محاولة عكس القيد الفاسد — reverse() تحجز الرقم أولاً، ثم تفشل بفحص is_balanced() الداخلي ==")
s2 = Session()
o1_reloaded = s2.get(type(o1), o1.id)
try:
    reverse(s2, o1_reloaded, datetime.date(2026, 1, 2))
    check("فشل متوقَّع لم يحدث", False)
except JournalEditError as e:
    check("فشل فعلياً بعد الحجز (فحص is_balanced الداخلي، لا فحص التاريخ/الحالة السابق للحجز)",
          "غير متوازن" in str(e))
s2.rollback()

raw3 = sqlite3.connect(db_path)
after_fail_rollback = raw3.execute("SELECT last_value FROM journal_number_sequences WHERE namespace='JV-REV'").fetchone()[0]
raw3.close()
check("last_value بعد الفشل والـrollback = 1 (الحجز ثابت، لم يُلغَه rollback المستند إطلاقاً)",
      after_fail_rollback == 1, f"القيمة الفعلية: {after_fail_rollback}")

print("\n== 3) الضمان الجوهري: الطلب التالي السليم يحصل على N+1 (000002)، وليس N (000001) المُعاد استخدامه ==")
s3 = Session()
o2 = mk(s3, "orig_second_clean")
r_new = reverse(s3, o2, datetime.date(2026, 1, 3))
s3.commit()
check("الطلب الجديد حصل على JV-REV-000002 تحديداً (لا JV-REV-000001 المُستهلَك سابقاً)",
      r_new.ref_no == "JV-REV-000002", f"الفعلي: {r_new.ref_no}")

print("\n== 4) الرقم 000001 لم يُستخدَم بأي قيد فعلياً بقاعدة البيانات — فجوة حقيقية، لا وهمية ==")
raw4 = sqlite3.connect(db_path)
row_1 = raw4.execute("SELECT COUNT(*) FROM journal_entries WHERE ref_no = 'JV-REV-000001'").fetchone()[0]
raw4.close()
check("لا صف واحد بقاعدة البيانات يحمل JV-REV-000001 — الرقم مفقود كفجوة حقيقية بمعنى الكلمة",
      row_1 == 0, f"عدد الصفوف بهذا الرقم: {row_1}")

os.remove(db_path)

print("\n" + "=" * 70)
print(f"النتيجة: {sum(1 for _, c in results if c)}/{len(results)} نجح")
print("=" * 70)
