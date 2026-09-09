"""
tools/backfill_seed_jv_rev_sequence.py
==========================================
حسم namespace "JV-REV" فقط — لا ترحيل reverse_manual_entry() للـBoundary
الآن (يبقى مؤجَّلاً لهجرة Manual Journal). الهدف الوحيد هنا: جعل
journal_number_sequences متوافقاً مع أي بيانات JV-REV قديمة موجودة
فعلياً على قاعدة عميل حقيقية، بحيث لا يُنتِج Boundary.reverse() رقماً
يتصادم مع رقم أنشأته reverse_manual_entry() القديمة سابقاً.

منهجية صريحة، لا تخمين (بند صريح من المراجعة):
  1) قراءة كل JournalEntry.ref_no الذي يبدأ بـ"JV-REV-" فعلياً.
  2) كل ref يُطابَق بدقة ضد النمط الوحيد الذي تُنتجه الآلية القديمة:
     ^JV-REV-\\d{6}$ (نفس f"JV-REV-{count+1:06d}" بجذر journal_edit.py).
     أي ref لا يطابق هذا النمط حرفياً = anomaly، يُسجَّل صراحة، **لا
     يُستخدَم إطلاقاً** في حساب القيمة القصوى، ولا يُخمَّن معناه.
  3) القيمة القصوى المحسوبة من الأرقام المطابقة فقط.
  4) Seed أحادي الاتجاه: last_value الجديد = MAX(الموجود حالياً بجدول
     الـSequence إن وُجد، القيمة القصوى المحسوبة) — لا يُنقَص أبداً، ولا
     يُعاد ضبطه للأسفل لو استُدعي أكثر من مرة (Idempotent بأمان).

الاستخدام: python3 tools/backfill_seed_jv_rev_sequence.py <db_path>
قراءة أولاً لطباعة تقرير، ثم يطبّق الـSeed فعلياً (عملية DB واحدة قصيرة،
Read+Write صريحان، لا Migration Schema، لا لمس لأي JournalEntry موجود).
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

NAMESPACE = "JV-REV"
VALID_PATTERN = re.compile(r"^JV-REV-(\d{6})$")


def inspect_and_seed(db_path: str, apply: bool = True) -> dict:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        rows = conn.execute(
            "SELECT id, ref_no FROM journal_entries WHERE ref_no LIKE 'JV-REV-%'"
        ).fetchall()

        valid_numbers: list[int] = []
        anomalies: list[tuple[int, str]] = []
        for entry_id, ref_no in rows:
            m = VALID_PATTERN.match(ref_no)
            if m:
                valid_numbers.append(int(m.group(1)))
            else:
                anomalies.append((entry_id, ref_no))

        computed_max = max(valid_numbers) if valid_numbers else 0

        existing = conn.execute(
            "SELECT last_value FROM journal_number_sequences WHERE namespace = ?", (NAMESPACE,)
        ).fetchone()
        current_last_value = existing[0] if existing else None

        seed_value = max(computed_max, current_last_value or 0)

        report = {
            "db_path": db_path,
            "total_jv_rev_refs_found": len(rows),
            "valid_refs_count": len(valid_numbers),
            "anomalies": anomalies,
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
        print("الاستخدام: python3 tools/backfill_seed_jv_rev_sequence.py <db_path>")
        sys.exit(1)
    r = inspect_and_seed(sys.argv[1])
    print(f"قاعدة البيانات: {r['db_path']}")
    print(f"إجمالي مراجع JV-REV-* الموجودة: {r['total_jv_rev_refs_found']}")
    print(f"مراجع صحيحة (تطابق النمط تماماً): {r['valid_refs_count']}")
    print(f"Anomalies (لا تُستخدَم بالحساب، غير مُخمَّنة): {r['anomalies'] or 'لا يوجد'}")
    print(f"القيمة القصوى المحسوبة من المراجع الصحيحة: {r['computed_max_from_valid_refs']}")
    print(f"last_value قبل الـSeed: {r['existing_sequence_last_value_before']}")
    print(f"last_value بعد الـSeed: {r.get('existing_sequence_last_value_after')}")
