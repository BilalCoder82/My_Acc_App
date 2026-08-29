"""
tests/run_gate.py
====================
نقطة التشغيل الفعلية للبوابة (§31 بـWORKFLOW.md) — يجمع:
  1) fuzz+oracle (200 سيناريو شراء عشوائي)
  2) Regression الكامل الحقيقي (تشغيل فعلي للملفات الثلاث كسكربتات،
     لا محاكاة) — كل ملف يُشغَّل كعملية منفصلة، exit code=0 يعني نجاح.
  3) استبعاد test_migration_safety.py صراحةً كـKnown Failure موثَّق،
     لا حذفاً صامتاً (راجع WORKFLOW.md §31.5).

الاستخدام: `python3 tests/run_gate.py`
النتيجة: reports_out/fuzz_report.md + .json، وexit code = 0 فقط إذا
         gate() == True.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.fuzz_report import FuzzReport
from tests.test_accounting_fuzz_oracle import (
    NUM_SCENARIOS, run_scenario,
)

REGRESSION_FILES = [
    "tests/test_accounting_edge_cases.py",
    "tests/test_e2e_scenario.py",
    "tests/test_per_item_account_posting.py",
]

KNOWN_FAILURES = [
    "tests/test_migration_safety.py: مكسور مسبقاً (بمعزل عن أي إصلاح بهذه "
    "الجلسة) — MIGRATIONS الفعلية وصلت v5 فتقفز apply_migrations() مباشرة "
    "v1→v5 وتتجاهل حقن migration v2 الوهمية التي يبنيها الاختبار. مؤجَّل "
    "لمرحلة استبدال PRAGMA runner بـAlembic (WORKFLOW.md §31.5) — ليس "
    "شرطاً لاجتياز هذه البوابة، لكنه شرط لقبول Alembic لاحقاً.",
]


def run_regression() -> bool:
    all_passed = True
    for f in REGRESSION_FILES:
        result = subprocess.run(
            [sys.executable, f], cwd=ROOT, capture_output=True, text=True,
        )
        ok = result.returncode == 0
        all_passed = all_passed and ok
        status = "✅" if ok else "❌"
        print(f"{status} {f} (exit={result.returncode})")
        if not ok:
            print(result.stdout[-2000:])
            print(result.stderr[-2000:])
    return all_passed


def main() -> int:
    print("== 1) Regression الكامل ==")
    regression_passed = run_regression()

    print("\n== 2) Fuzz + Oracle مستقل ==")
    report = FuzzReport()
    for seed in range(NUM_SCENARIOS):
        report.scenarios.append(run_scenario(seed))
    report.regression_suite_passed = regression_passed
    report.known_failures = KNOWN_FAILURES

    out_dir = ROOT / "reports_out"
    out_dir.mkdir(exist_ok=True)
    report.save(out_dir / "fuzz_report")

    print("\n" + report.to_markdown())

    return 0 if report.gate() else 1


if __name__ == "__main__":
    raise SystemExit(main())
