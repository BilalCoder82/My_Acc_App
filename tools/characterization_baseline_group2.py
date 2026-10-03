"""
tools/characterization_baseline_group2.py
=============================================
Baseline لدومينات المجموعة الثانية والثالثة والرابعة حسب §6 (لم تُهاجَر أي
منها بعد فعلياً — Opening Balances فقط مُهاجَرة حتى الآن): Manual Journal/
Voucher، Invoices (Sales/Purchase)، Invoice Cancellation، Settlements.

نفس منهجية Group 1 حرفياً: تشغيل ملفات اختبار موجودة فعلياً بلا أي تعديل
عبر runpy، فلترة صارمة بـsource_type (+is_reversal_of لعكوس manual تحديداً،
لتفادي خلط عكوس Opening Balances التي تشارك نفس source_type "manual_reversal"
لكنها ليست هذا الدومين — Opening Balances مُهاجَرة أصلاً، اختصاصها منتهٍ).
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
    "manual_journal": [
        "test_e2e_scenario.py", "test_accounting_edge_cases.py",
        "test_full_inventory_lifecycle.py", "test_aggressive_currency_inventory.py",
        "test_app_path_after_alembic.py",
    ],
    "invoices": [
        "test_e2e_scenario.py", "test_accounting_edge_cases.py",
        "test_full_inventory_lifecycle.py", "test_aggressive_currency_inventory.py",
        "test_invoice_cycle_customer_supplier.py", "test_currency_lifecycle_final.py",
    ],
    "invoice_cancellation": [
        "test_cancel_invoice.py", "test_ui_full_sales_lifecycle.py", "test_ui_settlement_and_cancel.py",
    ],
    "settlements": [
        "test_settlement_fx.py", "test_phase3b3_settlement_allocation.py",
        "test_account_reconciliation_rules.py", "test_allow_reconciliation_enforcement.py",
        "test_comprehensive_review.py", "test_settlement_tamper_resistance.py",
        "test_invoice_cycle_customer_supplier.py", "test_cancel_invoice.py",
    ],
}

PRIMARY_SOURCE_TYPES = {
    "manual_journal": {"manual"},
    "invoices": {"sales_invoice", "purchase_invoice"},
    "invoice_cancellation": {"invoice_cancel"},
    "settlements": {"receipt", "payment", "customer_refund", "supplier_refund"},
}


def _serialize_line(line) -> dict:
    return {
        "account_id": line.account_id,
        "debit": str(line.debit), "credit": str(line.credit),
        "debit_base": str(line.debit_base), "credit_base": str(line.credit_base),
        "line_currency_code": getattr(line, "line_currency_code", None),
        "line_exchange_rate": (
            str(getattr(line, "line_exchange_rate")) if getattr(line, "line_exchange_rate", None) is not None else None
        ),
    }


def _serialize_entry(entry: JournalEntry) -> dict:
    return {
        "id": entry.id, "ref_no": entry.ref_no, "entry_date": str(entry.entry_date),
        "status": entry.status.value if hasattr(entry.status, "value") else str(entry.status),
        "currency_code": entry.currency_code, "exchange_rate": str(entry.exchange_rate),
        "source_type": entry.source_type, "source_id": entry.source_id,
        "description": entry.description, "is_reversal_of": entry.is_reversal_of,
        "lines": [_serialize_line(l) for l in sorted(entry.lines, key=lambda l: l.id)],
    }


def extract_sessions(module_globals: dict) -> list[SASession]:
    seen, out = set(), []
    for v in module_globals.values():
        if isinstance(v, SASession) and id(v) not in seen:
            seen.add(id(v)); out.append(v)
    return out


_module_cache: dict[str, dict] = {}


def _run_file_once(filename: str) -> dict | None:
    if filename in _module_cache:
        return _module_cache[filename]
    path = TESTS_DIR / filename
    try:
        g = runpy.run_path(str(path), run_name=f"__baseline_group2_{filename}__")
    except Exception as exc:  # noqa: BLE001
        _module_cache[filename] = {"__error__": f"{type(exc).__name__}: {exc}"}
        return _module_cache[filename]
    _module_cache[filename] = {"__globals__": g}
    return _module_cache[filename]


def run_domain(domain: str, filenames: list[str]) -> dict:
    result = {"domain": domain, "files": filenames, "entries": [], "file_errors": {},
              "excluded_other_domain_entries_count": 0}
    primary_types = PRIMARY_SOURCE_TYPES[domain]
    for filename in filenames:
        mod = _run_file_once(filename)
        if mod is None or "__error__" in mod:
            result["file_errors"][filename] = mod["__error__"] if mod else "unknown"
            continue
        for session in extract_sessions(mod["__globals__"]):
            try:
                entries = session.query(JournalEntry).order_by(JournalEntry.id).all()
            except Exception:  # noqa: BLE001
                continue
            primary_ids = {e.id for e in entries if e.source_type in primary_types}
            for e in entries:
                if e.source_type in primary_types:
                    rec = _serialize_entry(e); rec["_source_file"] = filename
                    result["entries"].append(rec)
                elif e.source_type == "manual_reversal" and domain == "manual_journal" and e.is_reversal_of in primary_ids:
                    rec = _serialize_entry(e); rec["_source_file"] = filename
                    result["entries"].append(rec)
                else:
                    result["excluded_other_domain_entries_count"] += 1
    return result


def main() -> None:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Group 2/3/4 حسب §6 — لا Domain منها مُهاجَر بعد فعلياً (Opening Balances فقط مُهاجَرة، خارج هذا الملف تماماً)",
        "domains": [],
    }
    for domain, files in DOMAIN_FILES.items():
        report["domains"].append(run_domain(domain, files))

    out_path = PROJECT_ROOT / "reports_out" / "characterization_baseline_group2.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for d in report["domains"]:
        print(f"{d['domain']}: {len(d['entries'])} entries — file_errors: {d['file_errors'] or 'none'}")
    print(f"\nالملف: {out_path}")


if __name__ == "__main__":
    main()
