"""
tools/audit_zero_lines.py
=============================
سكربت مؤقت ومستقل لـ Step 0 (PHASE3B4_DESIGN_SPEC §9/§10) — Read-Only فقط.

تصحيح مهم مسجَّل هنا صراحة: الرسائل السابقة افترضت أن "منطق تعداد
العملاء" موجود جاهزاً بـ tools/migration_manager.py. بالفحص الفعلي:
migration_manager.py لا يحوي أي منطق اكتشاف لقواعد العملاء إطلاقاً —
upgrade_all_clients() تستقبل `clients: dict[str, Path]` جاهزاً من
المستدعي، ولا تبنيه بنفسها. ما تحتويه فعلاً وأُعيد استخدامه هنا هو فقط
*نمط* المعالجة المعزولة لكل عميل (فشل عميل واحد لا يوقف الباقين).

مصدر التعداد الفعلي للعملاء هو registry.db (app/db.py:
CompanyRecord.db_filename بجدول companies)، ومسار كل قاعدة عميل الفعلي
هو DATA_DIR/companies/<db_filename> (مطابق لـ open_company_db في
app/db.py، لا DATA_DIR مباشرة).

لا تعديل على أي ملف موجود. لا Migration. لا تعديل بيانات أو Schema.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
REGISTRY_PATH = os.path.join(DATA_DIR, "registry.db")

QUERY_ZERO = "SELECT COUNT(*) FROM journal_lines WHERE debit = 0 AND credit = 0;"
QUERY_ZERO_RAW_BASE_MISMATCH = (
    "SELECT COUNT(*) FROM journal_lines "
    "WHERE debit = 0 AND credit = 0 AND (debit_base != 0 OR credit_base != 0);"
)


@dataclass
class ClientAuditResult:
    client_id: str
    db_path: str
    zero_lines: int | None = None
    zero_with_base_mismatch: int | None = None
    error: str | None = None


@dataclass
class AuditReport:
    generated_at: str
    source: str  # "registry" or "dev_fallback"
    results: list[ClientAuditResult] = field(default_factory=list)

    @property
    def total_zero_lines(self) -> int:
        return sum(r.zero_lines for r in self.results if r.zero_lines is not None)

    @property
    def total_mismatch(self) -> int:
        return sum(r.zero_with_base_mismatch for r in self.results if r.zero_with_base_mismatch is not None)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "source": self.source,
            "queries": {"zero_lines": QUERY_ZERO, "zero_with_base_mismatch": QUERY_ZERO_RAW_BASE_MISMATCH},
            "results": [r.__dict__ for r in self.results],
            "total_zero_lines": self.total_zero_lines,
            "total_zero_with_base_mismatch": self.total_mismatch,
        }


def audit_one_db(client_id: str, db_path: Path) -> ClientAuditResult:
    result = ClientAuditResult(client_id=client_id, db_path=str(db_path))
    if not db_path.exists():
        result.error = f"الملف غير موجود: {db_path}"
        return result
    try:
        # read-only فعلياً عبر URI mode=ro — يمنع أي كتابة عرَضية حتى لو كان
        # هناك خطأ برمجي لاحق بالسكربت نفسه.
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            result.zero_lines = conn.execute(QUERY_ZERO).fetchone()[0]
            result.zero_with_base_mismatch = conn.execute(QUERY_ZERO_RAW_BASE_MISMATCH).fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — عزل الفشل عمداً، لا نوقف بقية العملاء
        result.error = str(exc)
    return result


def enumerate_clients_from_registry() -> list[tuple[str, Path]]:
    """يقرأ registry.db (جدول companies) فقط — لا كتابة. يرجّع [] لو الملف غير موجود."""
    if not os.path.exists(REGISTRY_PATH):
        return []
    conn = sqlite3.connect(f"file:{REGISTRY_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, name, db_filename FROM companies;").fetchall()
    finally:
        conn.close()
    clients = []
    for cid, name, db_filename in rows:
        path = Path(DATA_DIR) / "companies" / db_filename
        clients.append((f"{cid}:{name}", path))
    return clients


def run_audit() -> AuditReport:
    clients = enumerate_clients_from_registry()
    source = "registry"
    if not clients:
        source = "dev_fallback"
    report = AuditReport(generated_at=datetime.now(timezone.utc).isoformat(), source=source)
    for client_id, db_path in clients:
        report.results.append(audit_one_db(client_id, db_path))
    return report


if __name__ == "__main__":
    report = run_audit()
    out_path = Path(__file__).resolve().parent.parent / "reports_out" / "step0_audit_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n(تم الحفظ أيضاً في: {out_path})")
