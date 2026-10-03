"""
tools/backfill_seed_je_sal_sequence.py
==========================================
حسم namespace "JE-SAL" (Sales Invoice Posting) — نفس فئة مشكلة JV/JV-REV.
_next_ref_no() القديمة بـposting.py كانت تولّد "JE-SAL-{count+1:05d}"
(5 أرقام). journal_number_sequences يولّد الآن "JE-SAL-{last_value:06d}"
(6 أرقام) لنفس الـnamespace. فرق عدد الأرقام يمنع التصادم النصي المباشر
حالياً عملياً، لكن — بنفس انضباط JV/JV-REV — لا نعتمد على ذلك؛ نُبذر
(seed) القيمة الفعلية القصوى صراحة، لا نخمّنها ولا نتجاهلها.

الاستخدام: python3 tools/backfill_seed_je_sal_sequence.py <db_path>
"""
from __future__ import annotations

import re
import sqlite3
import sys

NAMESPACE = "JE-SAL"
VALID_PATTERN = re.compile(r"^JE-SAL-(\d+)$")


def inspect_and_seed(db_path: str, apply: bool = True) -> dict:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        rows = conn.execute("SELECT id, ref_no FROM journal_entries WHERE ref_no LIKE 'JE-SAL-%'").fetchall()
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
            "valid_je_sal_refs_count": len(valid_numbers),
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
        print("الاستخدام: python3 tools/backfill_seed_je_sal_sequence.py <db_path>")
        sys.exit(1)
    r = inspect_and_seed(sys.argv[1])
    print(f"قاعدة البيانات: {r['db_path']}")
    print(f"مراجع JE-SAL صالحة: {r['valid_je_sal_refs_count']}")
    print(f"Anomalies: {r['anomalies'] or 'لا يوجد'}")
    print(f"last_value قبل/بعد: {r['existing_sequence_last_value_before']} → {r.get('existing_sequence_last_value_after')}")
