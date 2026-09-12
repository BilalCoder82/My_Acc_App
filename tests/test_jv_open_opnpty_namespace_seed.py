"""
tests/test_jv_open_opnpty_namespace_seed.py
================================================
Final 3B-4 Gate — إغلاق فجوة اتساق حقيقية: JV/JV-REV/JE-SAL/JE-PUR كان
لها أدوات backfill/seed مبنية ومُختبَرة، بينما JV-OPEN وJV-OPNPTY (أول
دومينين مُهاجَرين فعلياً، Group 1) لم يكن لهما أداة مماثلة رغم أنهما
يحملان نفس فئة الخطر تماماً (بيانات قديمة بصيغة COUNT/LIKE قد تتصادم مع
Sequence الجديدة). هذا الملف يثبت الأداتين الجديدتين تعملان بنفس صرامة
الأدوات الأربع السابقة، بما في ذلك الصيغة القديمة غير المبطَّنة تحديداً
لـJV-OPNPTY (JV-OPNPTY-1 بلا أصفار بادئة — الصيغة الفعلية القديمة قبل
هذه الهجرة، لا صيغة مُخمَّنة).
"""
import os, sys, datetime, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Account, AccountType
from app.services.journal_edit import begin_entry, post, add_line, JournalEditError
from tools.backfill_seed_jv_open_sequence import inspect_and_seed as seed_jv_open
from tools.backfill_seed_jv_opnpty_sequence import inspect_and_seed as seed_jv_opnpty

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def run_case(namespace, legacy_refs, seed_fn, expected_max):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    raw = sqlite3.connect(path)
    for ref in legacy_refs:
        raw.execute(
            "INSERT INTO journal_entries (entry_date, ref_no, source_type, currency_code, exchange_rate, status, created_at) "
            "VALUES ('2026-01-01', ?, 'x', 'USD', 1, 'posted', '2026-01-01 00:00:00')", (ref,),
        )
    raw.commit(); raw.close()

    report = seed_fn(path, apply=True)
    check(f"{namespace}: last_value بعد الـSeed = {expected_max}",
          report["existing_sequence_last_value_after"] == expected_max, str(report))

    # begin_entry بعد الـSeed يجب ألا يتصادم مع أي مرجع قديم
    s = sessionmaker(bind=engine)()
    a = Account(code="1", name_ar="a", account_type=AccountType.ASSET)
    b = Account(code="2", name_ar="b", account_type=AccountType.ASSET)
    s.add_all([a, b]); s.commit()
    source_type = "opening_balance" if namespace == "JV-OPEN" else "opening_party_entry"
    entry = begin_entry(s, datetime.date(2026, 1, 2), "USD", D_("1"), source_type, "test")
    add_line(s, entry, a.id, debit_raw=D_("10"), debit_base=D_("10"))
    add_line(s, entry, b.id, credit_raw=D_("10"), credit_base=D_("10"))
    post(s, entry)
    s.commit()
    new_num = int(entry.ref_no.split("-")[-1])
    check(f"{namespace}: الرقم الجديد بعد الـSeed = {expected_max + 1} تحديداً (لا تصادم)",
          new_num == expected_max + 1, f"الفعلي: {entry.ref_no}")

    # إعادة تشغيل الـSeed — Idempotent
    report2 = seed_fn(path, apply=True)
    check(f"{namespace}: إعادة تشغيل الـSeed لا تُنقِص last_value",
          report2["existing_sequence_last_value_after"] >= expected_max + 1, str(report2))

    os.remove(path)


print("== JV-OPEN: بيانات قديمة مبطَّنة 5 أرقام (الصيغة الفعلية القديمة) ==")
run_case("JV-OPEN", ["JV-OPEN-00001", "JV-OPEN-00002", "JV-OPEN-00003"], seed_jv_open, 3)

print("\n== JV-OPNPTY: بيانات قديمة غير مبطَّنة إطلاقاً (الصيغة الفعلية القديمة قبل الهجرة) ==")
run_case("JV-OPNPTY", ["JV-OPNPTY-1", "JV-OPNPTY-2"], seed_jv_opnpty, 2)

print("\n" + "=" * 70)
print(f"النتيجة: {sum(1 for _, c in results if c)}/{len(results)} نجح")
print("=" * 70)
