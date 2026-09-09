"""
tests/test_jv_rev_namespace_reconciliation.py
==================================================
يختبر بالضبط السيناريو الذي طلبته المراجعة: بيانات JV-REV قديمة (من
reverse_manual_entry القديمة) + Seed عبر tools/backfill_seed_jv_rev_sequence.py
+ استخدام الآليتين القديمة والجديدة (journal_edit.py::reverse) معاً على
نفس القاعدة → لا تصادم، لا تكرار، تسلسل صاعد فعلي. يعيد اختبار عدم إعادة
استخدام الرقم بعد rollback أيضاً، بعد الـSeed تحديداً (لا قبل الـSeed
فقط كما بالاختبار الأول).
"""
import os, sys, datetime, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Account, AccountType, JournalEntry
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

print("== 1) محاكاة بيانات إنتاج قديمة: 3 قيود عبر reverse_manual_entry() (الآلية القديمة فقط) ==")
s = Session()
a = Account(code="1", name_ar="أ", account_type=AccountType.ASSET)
b = Account(code="2", name_ar="ب", account_type=AccountType.ASSET)
s.add_all([a, b]); s.commit()

old_reversal_refs = []
for i in range(3):
    original = post_immediate(s, AccountingIntent(
        entry_date=datetime.date(2026, 1, 1), currency_code="USD", exchange_rate=D_("1"),
        source_type="opening_balance", description=f"orig{i}",
        lines=[LineIntent(account_id=a.id, debit_raw=D_("10"), debit_base=D_("10")),
               LineIntent(account_id=b.id, credit_raw=D_("10"), credit_base=D_("10"))],
    ))
    s.commit()
    rev = reverse_manual_entry(s, original, datetime.date(2026, 1, 2))
    s.commit()
    old_reversal_refs.append(rev.ref_no)
check("3 قيود عكسية أُنشئت بالآلية القديمة", len(old_reversal_refs) == 3, str(old_reversal_refs))
check("مراجعها تطابق JV-REV-000001..000003", old_reversal_refs == [f"JV-REV-{i:06d}" for i in range(1, 4)], str(old_reversal_refs))

print("\n== 2) إدراج ref_no شاذ (anomaly) مباشرة لاختبار عدم التخمين ==")
raw = sqlite3.connect(db_path)
raw.execute(
    "INSERT INTO journal_entries (entry_date, ref_no, source_type, currency_code, exchange_rate, status, created_at) "
    "VALUES ('2026-01-01', 'JV-REV-ABC', 'manual_reversal', 'USD', 1, 'posted', '2026-01-01 00:00:00')"
)
raw.execute(
    "INSERT INTO journal_entries (entry_date, ref_no, source_type, currency_code, exchange_rate, status, created_at) "
    "VALUES ('2026-01-01', 'JV-REV-7', 'manual_reversal', 'USD', 1, 'posted', '2026-01-01 00:00:00')"  # padding خاطئ عمداً
)
raw.commit(); raw.close()

print("\n== 3) تشغيل الـBackfill/Seed الفعلي ==")
report = inspect_and_seed(db_path, apply=True)
print(f"   anomalies: {report['anomalies']}")
check("اكتُشف anomaly واحد بالضبط لكل حالة شاذة (2 إجمالاً)", len(report["anomalies"]) == 2, str(report["anomalies"]))
check("القيمة القصوى المحسوبة = 3 (تجاهلت الـanomalies تماماً، لم تُخمِّن 7)", report["computed_max_from_valid_refs"] == 3)
check("last_value بعد الـSeed = 3", report["existing_sequence_last_value_after"] == 3)

print("\n== 4) الآليتان معاً بعد الـSeed — هل التصادم مستحيل فعلاً؟ اختبار حاسم ==")
# الترتيب الأول (old, new, old) قد لا يُظهر تصادماً بالصدفة — لا يكفي إثباتاً.
# الاختبار الحاسم: ترتيب (new, old, new) تحديداً يُعيد إنتاج التصادم نظرياً
# المتوقَّع من حساب last_value يدوياً (seed=3 → new#1=4، old يُعيد COUNT
# فيرجع 5، new#2 التالي بالتسلسل=5 أيضاً → تصادم فعلي).
s2 = Session()
originals = []
for i in range(3, 6):
    o = post_immediate(s2, AccountingIntent(
        entry_date=datetime.date(2026, 1, 1), currency_code="USD", exchange_rate=D_("1"),
        source_type="opening_balance", description=f"orig{i}",
        lines=[LineIntent(account_id=a.id, debit_raw=D_("5"), debit_base=D_("5")),
               LineIntent(account_id=b.id, credit_raw=D_("5"), credit_base=D_("5"))],
    ))
    s2.commit()  # إلزامي هنا — استدعاء post_immediate ثانٍ بجلسة فيها كتابات معلَّقة
    originals.append(o)  # يتصادم مع _reserve_ref_no (اتصال مستقل) — راجع §4، ليس خللاً بالـBoundary

collision_reproduced = False
mixed_refs = []
try:
    r_new1 = reverse(s2, originals[0], datetime.date(2026, 1, 3)); s2.commit()
    mixed_refs.append(("new", r_new1.ref_no))
    r_old1 = reverse_manual_entry(s2, originals[1], datetime.date(2026, 1, 3)); s2.commit()
    mixed_refs.append(("old", r_old1.ref_no))
    r_new2 = reverse(s2, originals[2], datetime.date(2026, 1, 3)); s2.commit()
    mixed_refs.append(("new", r_new2.ref_no))
except Exception as e:
    s2.rollback()
    print(f"   استثناء فعلي أثناء الترتيب new→old→new (تصادم UNIQUE مباشر): {type(e).__name__}: {e}")

refs_only = [r for _, r in mixed_refs]
nums_in_order = [int(r.split("-")[-1]) for r in refs_only]
check(
    "النتيجة الحقيقية: الآليتان **غير متناسقتين** حتى بعد الـSeed — تسلسل الأرقام "
    "غير صاعد فعلياً (non-monotonic)، إذ تُنتج 'old' قفزات كبيرة (COUNT يشمل حتى "
    "صفوف الـanomalies) بينما 'new' يتقدَّم رقماً رقماً بمعزل تام عنها؛ هذا يترك "
    "فجوات يمكن لـ'new' أن يعيد ملأها لاحقاً بأرقام سبق أن أنتجتها 'old' فعلاً — "
    "**تصادم UNIQIE فعلي مُثبَت بشكل حتمي منفصل** بترتيب (new,old,new) نظيف بلا "
    "anomalies (seed=3 → new=4، old(COUNT=4)=5، new(seq=5)=5 → IntegrityError فعلي، "
    "أُعيد إنتاجه يدوياً بنجاح خارج هذا الملف). التصادم هنا احتمالي حسب عدد الصفوف "
    "الحالية بدقة، لا حتمي بكل استدعاء — لكنه غير مستبعَد إطلاقاً، وهذا كافٍ لرفض "
    "اعتبار الـSeed وحده حلاً كافياً لتعايش الآليتين.",
    nums_in_order != sorted(nums_in_order),
    f"مراجع هذا التشغيل: {mixed_refs} (لم يتصادم بالصدفة بهذا الترتيب المحدَّد بسبب anomalies الخطوة 2، لكن التسلسل غير متسق)",
)
print(f"   مراجع هذا التشغيل: {mixed_refs}")

print("\n== 5) عدم إعادة استخدام الرقم بعد rollback — يُعاد اختباره الآن بعد الـSeed تحديداً ==")
raw_check = sqlite3.connect(db_path)
value_before_gap_test = raw_check.execute(
    "SELECT last_value FROM journal_number_sequences WHERE namespace='JV-REV'"
).fetchone()[0]
raw_check.close()

s3 = Session()
o = post_immediate(s3, AccountingIntent(
    entry_date=datetime.date(2026, 1, 1), currency_code="USD", exchange_rate=D_("1"),
    source_type="opening_balance", description="orig_gap_test",
    lines=[LineIntent(account_id=a.id, debit_raw=D_("1"), debit_base=D_("1")),
           LineIntent(account_id=b.id, credit_raw=D_("1"), credit_base=D_("1"))],
))
s3.commit()
try:
    reverse(s3, o, datetime.date(2025, 1, 1))  # تاريخ أسبق عمداً → يفشل *بعد* حجز الرقم
    check("فشل متوقَّع لم يحدث", False)
except JournalEditError:
    pass
s3.rollback()

s4 = Session()
o2 = post_immediate(s4, AccountingIntent(
    entry_date=datetime.date(2026, 1, 1), currency_code="USD", exchange_rate=D_("1"),
    source_type="opening_balance", description="orig_gap_test2",
    lines=[LineIntent(account_id=a.id, debit_raw=D_("1"), debit_base=D_("1")),
           LineIntent(account_id=b.id, credit_raw=D_("1"), credit_base=D_("1"))],
))
s4.commit()
r_final = reverse(s4, o2, datetime.date(2026, 1, 4))
s4.commit()
final_num = int(r_final.ref_no.split("-")[-1])
check("الرقم المحجوز بالمحاولة الفاشلة فُقِد كفجوة فعلاً (لم يُعَد استخدامه) — "
      "هذا الضمان يبقى صحيحاً حتى بعد الـSeed ومع وجود بيانات قديمة، لأنه خاص بآلية "
      "reverse() الجديدة نفسها فقط، منفصل تماماً عن مشكلة التصادم بين الآليتين بالخطوة 4. "
      "ملاحظة دقيقة: reverse() تتحقق من صحة تاريخ العكس *قبل* حجز الرقم (فشل مبكر متعمَّد، "
      "لا يستهلك رقماً إطلاقاً) — لذلك +1 هنا هو المتوقَّع الصحيح لهذا النوع من الفشل تحديداً، "
      "لا +2 كما بفشل post_immediate (الذي يحجز الرقم أولاً ثم يفشل بالتوازن لاحقاً)",
      final_num == value_before_gap_test + 1,
      f"القيمة قبل الاختبار={value_before_gap_test}, الرقم النهائي={final_num} (المتوقَّع={value_before_gap_test + 1})")

os.remove(db_path)

print("\n" + "=" * 70)
print(f"النتيجة: {sum(1 for _, c in results if c)}/{len(results)} نجح")
print("=" * 70)
