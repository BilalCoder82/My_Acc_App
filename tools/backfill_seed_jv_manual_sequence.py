"""
tools/backfill_seed_jv_manual_sequence.py
=============================================
حسم namespace "JV" (Manual Journal/Voucher) — نفس فئة مشكلة JV-REV
المُكتشَفة والمُصلَحة سابقاً (راجع tools/backfill_seed_jv_rev_sequence.py)،
مُطبَّقة هنا استباقياً *قبل* اعتماد قاعدة بيانات عميل حقيقية على
begin_entry() الجديدة، لا بعد اكتشاف تصادم فعلياً كما حدث بـJV-REV.

السبب: journal_voucher_form.py::_next_ref_no() القديمة كانت تستخدم
`ref_no.like("JV-%")` (COUNT-based) لتوليد "JV-{count+1:06d}" — نفس
الشكل النصي بالضبط الذي ستولّده journal_number_sequences لنفس
namespace "JV" بعد الهجرة. أي قاعدة عميل فيها قيود يدوية سابقة (JV-000001
وما شابه) يجب أن تُبذَر (seed) الـSequence بقيمتها القصوى الفعلية قبل أي
استدعاء لـbegin_entry() بعد الهجرة — وإلا ستُعاد أرقام مُستخدَمة فعلاً.

منهجية مطابقة لسكربت JV-REV تماماً — لا تخمين:
  1) قراءة كل ref_no يطابق **حصراً** ^JV-\\d{6}$ (وليس LIKE "JV-%" الواسع
     الذي كانت تستخدمه الآلية القديمة نفسها خطأً — ذلك النمط الواسع كان
     يطابق JV-OPEN-%/JV-REV-%/JV-OPNPTY-% أيضاً؛ لا نكرر نفس الخطأ هنا).
  2) أي ref لا يطابق تماماً = anomaly، مُستبعَد من الحساب صراحة.
  3) Seed أحادي الاتجاه: last_value = MAX(الموجود بجدول الـSequence إن
     وُجد، القيمة القصوى المحسوبة) — Idempotent، لا يُنقَص أبداً.

الاستخدام: python3 tools/backfill_seed_jv_manual_sequence.py <db_path>
"""
from __future__ import annotations

import re
import sqlite3
import sys

NAMESPACE = "JV"
VALID_PATTERN = re.compile(r"^JV-(\d{6})$")


def inspect_and_seed(db_path: str, apply: bool = True) -> dict:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        # ملاحظة مهمة: LIKE 'JV-%' بالاستعلام هنا هو فقط لجلب *كل المرشَّحين*
        # للفحص (خطوة قراءة أولية واسعة عمداً)، ثم كل واحد يُطابَق بدقة
        # بالـVALID_PATTERN الصارم بعدها فردياً — الفلترة النهائية بالـregex
        # الحصري، لا بهذا LIKE الواسع.
        rows = conn.execute("SELECT id, ref_no FROM journal_entries WHERE ref_no LIKE 'JV-%'").fetchall()

        valid_numbers: list[int] = []
        anomalies: list[tuple[int, str]] = []
        for entry_id, ref_no in rows:
            m = VALID_PATTERN.match(ref_no)
            if m:
                valid_numbers.append(int(m.group(1)))
            else:
                # يشمل هذا عمداً كل JV-OPEN-*/JV-REV-*/JV-OPNPTY-* — ليست
                # anomalies حقيقية، بل namespaces أخرى مختلفة تماماً؛ نُدرِجها
                # هنا فقط لتوثيق أن الفحص رآها ولم يخلطها بالحساب، لا كخطأ.
                anomalies.append((entry_id, ref_no))

        computed_max = max(valid_numbers) if valid_numbers else 0

        existing = conn.execute(
            "SELECT last_value FROM journal_number_sequences WHERE namespace = ?", (NAMESPACE,)
        ).fetchone()
        current_last_value = existing[0] if existing else None
        seed_value = max(computed_max, current_last_value or 0)

        report = {
            "db_path": db_path,
            "total_candidates_scanned": len(rows),
            "valid_jv_manual_refs_count": len(valid_numbers),
            "other_namespace_or_anomaly_refs": anomalies,
            "computed_max_from_valid_refs": computed_max,
            "existing_sequence_last_value_before": current_last_value,
            "seed_value_to_apply": seed_value,
            "applied": False,
        }

        if apply:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO journal_number_sequences (namespace, last_value) VALUES (?, 0) "
                "ON CONFLICT(namespace) DO NOTHING",
                (NAMESPACE,),
            )
            conn.execute(
                "UPDATE journal_number_sequences SET last_value = MAX(last_value, ?) WHERE namespace = ?",
                (seed_value, NAMESPACE),
            )
            conn.commit()
            after = conn.execute(
                "SELECT last_value FROM journal_number_sequences WHERE namespace = ?", (NAMESPACE,)
            ).fetchone()
            report["existing_sequence_last_value_after"] = after[0]
            report["applied"] = True
        return report
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("الاستخدام: python3 tools/backfill_seed_jv_manual_sequence.py <db_path>")
        sys.exit(1)
    r = inspect_and_seed(sys.argv[1])
    print(f"قاعدة البيانات: {r['db_path']}")
    print(f"مراجع صالحة لـJV اليدوي (^JV-\\d{{6}}$): {r['valid_jv_manual_refs_count']}")
    print(f"مرشَّحون آخرون (namespaces أخرى مثل JV-OPEN/JV-REV، ليست anomalies): {len(r['other_namespace_or_anomaly_refs'])}")
    print(f"القيمة القصوى المحسوبة: {r['computed_max_from_valid_refs']}")
    print(f"last_value قبل/بعد الـSeed: {r['existing_sequence_last_value_before']} → {r.get('existing_sequence_last_value_after')}")
