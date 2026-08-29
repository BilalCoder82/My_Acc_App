"""
tools/migration_manager.py
=============================
**غير مفعّل بعد** — جاهز لمرحلة Alembic القادمة فقط (بعد اجتياز
fuzz_report.gate() + Regression الكامل + Baseline رسمي، حسب الترتيب
المتفق عليه في WORKFLOW.md). لا يُستدعى من main.py أو أي مسار تشغيل
حالي، ولا يفترض وجود Alembic مثبَّتاً — سيحتاج تعديلاً بسيطاً (مسار
alembic.ini الفعلي + بنية env.py) عند التفعيل الفعلي.

الفكرة (متفق عليها): نسخ احتياطي → فحص الإصدار الحالي → ترقية → تحقق
من الإصدار → PRAGMA integrity_check → التالي. فشل عميل واحد لا يوقف الباقين.
"""

import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class ClientMigrationResult:
    client_id: str
    db_path: Path
    success: bool
    revision_before: str | None = None
    revision_after: str | None = None
    error: str | None = None


@dataclass
class MigrationReport:
    results: list[ClientMigrationResult] = field(default_factory=list)

    @property
    def failed(self) -> list[ClientMigrationResult]:
        return [r for r in self.results if not r.success]

    def summary(self) -> str:
        ok = len(self.results) - len(self.failed)
        lines = [f"نجح: {ok} / {len(self.results)}"]
        for r in self.failed:
            lines.append(f"  ✗ {r.client_id}: {r.error}")
        return "\n".join(lines)


def backup_db(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"{db_path.stem}_{stamp}.db"
    shutil.copy2(db_path, dest)
    return dest


def integrity_check(db_path: Path) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    finally:
        conn.close()


def get_current_revision(db_path: Path) -> str | None:
    """
    يُرجع None أيضاً إذا كان الملف تالفاً تماماً (لا جدول alembic_version
    ولا حتى ملف SQLite صالح) — sqlite3.DatabaseError هو الصنف الأب لـ
    OperationalError، فالتقاطه يغطي الحالتين معاً. اكتُشفت هذه الفجوة
    فعلياً أثناء اختبار سيناريو ملف تالف (WORKFLOW.md §31.9) — كانت
    OperationalError وحدها غير كافية وتسبَّبت بانهيار الأداة كاملة بدل
    عزل الفشل لعميل واحد كما هو مقصود.
    """
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.DatabaseError:
        return None
    try:
        row = conn.execute("SELECT version_num FROM alembic_version;").fetchone()
        return row[0] if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()


def upgrade_one_client(
    client_id: str, db_path: Path, backup_dir: Path, alembic_ini: Path,
) -> ClientMigrationResult:
    # مُغلَّف بـtry أيضاً — لا يجوز لأي فشل هنا (حتى قبل النسخ الاحتياطي)
    # أن يفلت من عزل الفشل الخاص بهذا العميل تحديداً.
    try:
        revision_before = get_current_revision(db_path)
    except Exception:  # noqa: BLE001
        revision_before = None
    try:
        backup_db(db_path, backup_dir)
        subprocess.run(
            ["alembic", "-c", str(alembic_ini), "-x", f"db_path={db_path}", "upgrade", "head"],
            check=True, capture_output=True, text=True,
        )
        if not integrity_check(db_path):
            raise RuntimeError("integrity_check فشل بعد الترقية")
        return ClientMigrationResult(
            client_id=client_id, db_path=db_path, success=True,
            revision_before=revision_before, revision_after=get_current_revision(db_path),
        )
    except Exception as exc:  # noqa: BLE001 — عزل الفشل عمداً، لا نوقف بقية العملاء
        return ClientMigrationResult(
            client_id=client_id, db_path=db_path, success=False,
            revision_before=revision_before, error=str(exc),
        )


def upgrade_all_clients(
    clients: dict[str, Path], backup_dir: Path, alembic_ini: Path,
) -> MigrationReport:
    report = MigrationReport()
    for client_id, db_path in clients.items():
        report.results.append(upgrade_one_client(client_id, db_path, backup_dir, alembic_ini))
    return report


if __name__ == "__main__":
    raise SystemExit(
        "غير مفعّل بعد — راجع WORKFLOW.md قسم 'المرحلة 1' قبل تشغيله فعلياً على قواعد عملاء حقيقية."
    )
