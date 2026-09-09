"""
tools/characterization_baseline_group1.py
=============================================
Baseline مخصَّص فقط لأول مجموعة هجرة حسب PHASE3B4_DESIGN_SPEC.md §6:
  opening_balances.py / opening_party_balances.py

عمداً **لا** يشمل journal_voucher_form.py أو posting.py أو settlements.py —
هذه ستُميَّز بـBaseline خاص بها لاحقاً عند وصول دورها بالترتيب المُقفَل.

مصادر البيانات: 3 ملفات اختبار مخصَّصة حصراً لهذه الدومينات (لا ملفات
متعددة الدومينات مثل test_e2e_scenario.py، لتفادي الحاجة لفلترة يدوية):
  - test_opening_account_balances.py  → Opening Balances (+ reverse)
  - test_opening_inventory.py         → Opening Inventory (+ reverse)
  - test_phase3b3_settlement_allocation.py → Opening Party (RECEIVABLE فقط،
    Known Gaps: PAYABLE، عملة أجنبية، Reverse-after-Allocation — موثَّقة
    بتقرير Step1 السابق، لا تُختلَق هنا)

ملاحظة منهجية مهمة (يجب أن تظهر بالتقرير، لا تُخفى):
reverse_opening_account_balances() تُعيد استخدام reverse_manual_entry()
حرفياً ولا تضع source_type مختلفاً خاصاً بها — القيد الناتج يحمل نفس
source_type لقيد عكس يدوي عام. لذلك لا يُعتمَد على source_type وحده
لتحديد "هل هذا القيد يخص Opening Balances" — الاعتماد هنا على *الملف
المصدر نفسه* (كل ملف من الثلاثة مخصَّص لدومين واحد فعلياً)، لا على
تصنيف تلقائي بالـsource_type.

لا تعديل على أي ملف موجود. لا Migration. لا Schema change.
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

DOMAIN_FILES = {
    "opening_balances": "test_opening_account_balances.py",
    "opening_inventory": "test_opening_inventory.py",
    "opening_party": "test_phase3b3_settlement_allocation.py",
}

# اكتُشف فعلياً عند أول تشغيل (لا افتراض مسبق): هذه الملفات ليست
# مخصَّصة حصراً لدومين واحد كما افتُرض ابتداءً — test_opening_inventory.py
# يُنشئ فواتير بيع/شراء لاختبار استهلاك المخزون، وtest_phase3b3_
# settlement_allocation.py غالبيته فعلياً اختبار Settlement Allocation
# (Group 4 لاحقاً)، وOpeningPartyEntry فيه إعداد جانبي فقط. لذلك التصفية
# هنا إلزامية بـsource_type، لا بمجرد "أي قيد بهذا الملف".
PRIMARY_SOURCE_TYPES = {
    "opening_balances": {"opening_balance"},
    "opening_inventory": {"opening_inventory", "opening_inventory_reverse"},
    "opening_party": {"opening_party_entry"},
}


def _serialize_line(line) -> dict:
    return {
        "account_id": line.account_id,
        "debit": str(line.debit),
        "credit": str(line.credit),
        "debit_base": str(line.debit_base),
        "credit_base": str(line.credit_base),
        "line_currency_code": getattr(line, "line_currency_code", None),
        "line_exchange_rate": (
            str(getattr(line, "line_exchange_rate")) if getattr(line, "line_exchange_rate", None) is not None else None
        ),
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
        "is_reversal_of": entry.is_reversal_of,
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


def run_domain(domain: str, filename: str) -> dict:
    path = TESTS_DIR / filename
    result = {
        "domain": domain, "file": filename, "status": "ok", "error": None,
        "entries": [], "excluded_other_domain_entries_count": 0,
    }
    try:
        g = runpy.run_path(str(path), run_name="__baseline_group1__")
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    primary_types = PRIMARY_SOURCE_TYPES[domain]
    for session in extract_sessions(g):
        try:
            entries = session.query(JournalEntry).order_by(JournalEntry.id).all()
        except Exception:  # noqa: BLE001
            continue
        primary_ids = {e.id for e in entries if e.source_type in primary_types}
        for e in entries:
            if e.source_type in primary_types:
                result["entries"].append(_serialize_entry(e))
            elif e.source_type == "manual_reversal" and e.is_reversal_of in primary_ids:
                # عكس لقيد ينتمي لهذا الدومين، لكن reverse_opening_account_balances()
                # يُعيد استخدام reverse_manual_entry() فلا يضع source_type مميّزاً — راجع note_reversal_source_type
                result["entries"].append(_serialize_entry(e))
            else:
                result["excluded_other_domain_entries_count"] += 1
    return result


def main() -> None:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "PHASE3B4_DESIGN_SPEC.md §6 — أول مجموعة هجرة فقط (opening_balances.py / opening_party_balances.py)",
        "note_reversal_source_type": (
            "reverse_opening_account_balances() تُعيد استخدام reverse_manual_entry() حرفياً — "
            "القيد الناتج لا يحمل source_type مميّزاً لـOpening Balances؛ التصنيف هنا اعتمد على "
            "الملف المصدر لا على source_type."
        ),
        "known_gaps_opening_party": [
            "PAYABLE غير ممثل بسيناريو نجاح حالي",
            "عملة أجنبية غير ممثلة (كل السيناريوهات بعملة الشركة الأساسية، rate=1)",
            "Reverse بعد وجود SettlementAllocation غير ممثل بالـBaseline",
        ],
        "domains": [],
    }
    for domain, filename in DOMAIN_FILES.items():
        report["domains"].append(run_domain(domain, filename))

    out_path = PROJECT_ROOT / "reports_out" / "characterization_baseline_group1.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for d in report["domains"]:
        status = d["status"] if d["status"] == "ok" else f"ERROR: {d['error']}"
        print(f"{d['domain']} ({d['file']}): {len(d['entries'])} entries — {status}")
    print(f"\nالملف: {out_path}")


if __name__ == "__main__":
    main()
