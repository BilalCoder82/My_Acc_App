"""
tools/characterization_baseline.py
=====================================
Step 1 (Characterization Baseline) — PHASE3B4_DESIGN_SPEC §7.

مبدأ التنفيذ: لا اختراع سيناريوهات جديدة. هذا السكربت **لا يبني بيانات
اختبار جديدة** — ينفّذ ملفات tests/ الموجودة فعلياً *كما هي تماماً*
(بلا أي تعديل عليها) عبر runpy، ثم يفحص كل الجلسات (Session) التي
أنشأتها تلك السيناريوهات ويستخرج منها JournalEntry/JournalLine الفعلية
الناتجة عن التنفيذ الحقيقي للكود الحالي — لا محاكاة، لا قيم مفترَضة.

الملفات المصدر (السبب موثَّق بتقرير Step 1 — Read-Only Inventory):
  - test_e2e_scenario.py            → Manual Journal, Opening Inventory,
                                        Purchase/Sales Invoice (+returns،
                                        تُستبعَد لاحقاً من التجميع لأن
                                        returns.py خارج نطاق 3B-4)
  - test_opening_account_balances.py → Opening Balances (+reverse)
  - test_opening_inventory.py        → Opening Inventory (+reverse)
  - test_cancel_invoice.py           → Invoice Cancellation
  - test_settlement_fx.py            → Settlements (receipt/payment)
  - test_phase3b3_settlement_allocation.py → Settlements (allocated) +
                                        Opening Party (RECEIVABLE فقط —
                                        PAYABLE غير مغطّى، موثَّق كفجوة)

لا تعديل على أي من هذه الملفات. لا Migration. لا Schema change.
"""
from __future__ import annotations

import json
import os
import runpy
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sqlalchemy.orm import Session as SASession  # noqa: E402
from app.models import JournalEntry  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = PROJECT_ROOT / "tests"

# source_type القيم المستبعَدة صراحة من 3B-4 (returns.py خارج النطاق المُقفَل)
EXCLUDED_SOURCE_TYPES = {"sales_return", "purchase_return"}

SOURCE_FILES = [
    "test_e2e_scenario.py",
    "test_opening_account_balances.py",
    "test_opening_inventory.py",
    "test_cancel_invoice.py",
    "test_settlement_fx.py",
    "test_phase3b3_settlement_allocation.py",
]


def _serialize_line(line) -> dict:
    return {
        "account_id": line.account_id,
        "debit": str(line.debit),
        "credit": str(line.credit),
        "debit_base": str(line.debit_base),
        "credit_base": str(line.credit_base),
        "line_currency_code": getattr(line, "line_currency_code", None),
        "line_exchange_rate": str(getattr(line, "line_exchange_rate", "")) or None,
    }


def _serialize_entry(entry: JournalEntry) -> dict:
    return {
        "id": entry.id,
        "ref_no": entry.ref_no,
        "entry_date": str(entry.entry_date),
        "status": entry.status.value if hasattr(entry.status, "value") else str(entry.status),
        "currency_code": entry.currency_code,
        "exchange_rate": str(entry.exchange_rate),
        "source_type": entry.source_type,
        "source_id": entry.source_id,
        "description": entry.description,
        "lines": [_serialize_line(l) for l in sorted(entry.lines, key=lambda l: l.id)],
    }


def extract_sessions(module_globals: dict) -> list[SASession]:
    seen_ids = set()
    sessions = []
    for value in module_globals.values():
        if isinstance(value, SASession) and id(value) not in seen_ids:
            seen_ids.add(id(value))
            sessions.append(value)
    return sessions


def run_file(filename: str) -> dict:
    path = TESTS_DIR / filename
    result = {"file": filename, "status": "ok", "error": None, "entries": [], "excluded_entries": []}
    try:
        g = runpy.run_path(str(path), run_name="__baseline_import__")
    except Exception as exc:  # noqa: BLE001 — نعزل فشل ملف واحد، لا نوقف بقية الاستخراج
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    for session in extract_sessions(g):
        try:
            entries = session.query(JournalEntry).order_by(JournalEntry.id).all()
        except Exception as exc:  # noqa: BLE001 — جلسة مغلقة/تالفة لا توقف البقية
            continue
        for e in entries:
            record = _serialize_entry(e)
            if e.source_type in EXCLUDED_SOURCE_TYPES:
                result["excluded_entries"].append(record)
            else:
                result["entries"].append(record)
    return result


def main() -> None:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "كل قيمة هنا مستخرَجة من تنفيذ فعلي لملفات tests/ الموجودة، بلا أي تعديل عليها ولا سيناريوهات مخترَعة.",
        "excluded_source_types": sorted(EXCLUDED_SOURCE_TYPES),
        "files": [],
    }
    for filename in SOURCE_FILES:
        report["files"].append(run_file(filename))

    out_path = PROJECT_ROOT / "reports_out" / "characterization_baseline.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    total = sum(len(f["entries"]) for f in report["files"])
    excluded = sum(len(f["excluded_entries"]) for f in report["files"])
    errors = [f["file"] for f in report["files"] if f["status"] == "error"]
    print(f"إجمالي القيود المُلتقَطة ضمن نطاق 3B-4: {total}")
    print(f"إجمالي القيود المستبعَدة (returns.py، خارج النطاق): {excluded}")
    print(f"ملفات فشل تنفيذها: {errors or 'لا يوجد'}")
    print(f"الملف: {out_path}")


if __name__ == "__main__":
    main()
