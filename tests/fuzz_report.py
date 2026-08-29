"""
tests/fuzz_report.py
======================
بوابة قبول آلية لاختبار الـfuzz — العتبات هنا **ليست افتراضية اعتباطية**:
مأخوذة مباشرة من app/services/money.py (MONEY_QUANT = 0.01)، وهي سياسة
التقريب الرسمية الموثّقة فعلياً بالمشروع (ROUND_HALF_UP على منزلتين).
هذا يسدّ الفجوة التي أُثيرت سابقاً: "لا تجعلوا رقم الـthreshold هدفاً
لإنجاح الاختبار؛ اجعلوا السياسة المحاسبية هي التي تحدد الرقم".
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.services.money import MONEY_QUANT

# فرق مقبول لكل خطوة تقريب مستقلة = نصف وحدة MONEY_QUANT (±0.005) —
# مشتق من كون money() تقريب ROUND_HALF_UP على منزلتين، لا رقماً افتراضياً.
# كل سطر قيد شراء يمر بخطوتي تقريب مستقلتين فعلياً (تتبُّع مباشر بالكود):
#   1) invoice_calc.py: money(net_final) بعملة المستند
#   2) posting.py _jline: money(debit × rate) عند التحويل للعملة الأساسية
# لذلك العتبة الصحيحة تتناسب مع عدد عمليات الشراء بالسيناريو (كل عملية
# تضيف حتى ±0.01 كحد أقصى نظري)، وليست رقماً ثابتاً بصرف النظر عن حجم
# السيناريو — هذا كان الخطأ في النسخة السابقة (0.02 ثابتة).
ROUNDING_STEPS_PER_OPERATION = 2
MAX_ROUNDING_DIFF_PER_OPERATION = (MONEY_QUANT / 2) * ROUNDING_STEPS_PER_OPERATION  # = 0.01
MAX_ALLOWED_SANITY_ERRORS = 0                           # صفر تسامح


@dataclass
class ScenarioResult:
    seed: int
    num_operations: int
    currencies_used: list[str]
    exchange_rates: dict[str, Decimal]
    expected_balances: dict[str, Decimal]
    actual_balances: dict[str, Decimal]
    expected_avg_cost: dict[str, Decimal]
    actual_avg_cost: dict[str, Decimal]
    total_debit_base: Decimal
    total_credit_base: Decimal
    sanity_errors: int

    def max_allowed_diff(self) -> Decimal:
        """عتبة مشتقة من عدد العمليات، لا رقماً ثابتاً (راجع تعليق أعلى الملف)."""
        return MAX_ROUNDING_DIFF_PER_OPERATION * max(self.num_operations, 1)

    def rounding_diffs(self) -> dict[str, Decimal]:
        return {
            acct: abs(self.actual_balances.get(acct, Decimal("0")) - expected)
            for acct, expected in self.expected_balances.items()
        }

    def passes(self) -> bool:
        allowed = self.max_allowed_diff()
        if self.sanity_errors > MAX_ALLOWED_SANITY_ERRORS:
            return False
        if self.total_debit_base != self.total_credit_base:
            return False
        if any(diff > allowed for diff in self.rounding_diffs().values()):
            return False
        for item, expected_cost in self.expected_avg_cost.items():
            actual_cost = self.actual_avg_cost.get(item, Decimal("0"))
            if abs(actual_cost - expected_cost) > allowed:
                return False
        return True


@dataclass
class FuzzReport:
    scenarios: list[ScenarioResult] = field(default_factory=list)
    regression_suite_passed: bool | None = None
    # فشل معروف مسبقاً، غير ناتج عن هذه الجلسة، مُستبعَد من الـgate صراحة
    # بسبب موثَّق — لا يجوز أن "يختفي" بصمت (راجع WORKFLOW.md §31.5)
    known_failures: list[str] = field(default_factory=list)

    def gate(self) -> bool:
        if not self.scenarios:
            return False
        if self.regression_suite_passed is not True:
            return False
        return all(s.passes() for s in self.scenarios)

    def to_markdown(self) -> str:
        failed = [s for s in self.scenarios if not s.passes()]
        lines = [
            f"# تقرير Fuzz — {datetime.now().isoformat(timespec='seconds')}",
            "",
            f"- عدد السيناريوهات: {len(self.scenarios)}",
            f"- الفاشلة: {len(failed)}",
            f"- عتبة فروقات التقريب: {MAX_ROUNDING_DIFF_PER_OPERATION} × عدد العمليات بالسيناريو "
            f"(مشتقة من MONEY_QUANT الرسمية، لا رقماً ثابتاً)",
            f"- نتيجة Regression الكامل: {self.regression_suite_passed}",
            f"- **القرار النهائي (gate): {'PASS ✅' if self.gate() else 'FAIL ❌'}**",
            "",
        ]
        if self.known_failures:
            lines.append("## Known Failures — مُستبعَدة من الـgate بسبب موثَّق (لا تُحذف من هنا أبداً)")
            for kf in self.known_failures:
                lines.append(f"- ⚠️ {kf}")
            lines.append("")
        for s in failed:
            lines.append(
                f"- ❌ seed={s.seed} عمليات={s.num_operations} عملات={s.currencies_used} "
                f"أخطاء sanity={s.sanity_errors} فروقات={s.rounding_diffs()} "
                f"(العتبة المسموحة لهذا السيناريو: {s.max_allowed_diff()})"
            )
        return "\n".join(lines)

    def save(self, path: Path) -> None:
        path.write_text(self.to_markdown(), encoding="utf-8")
        path.with_suffix(".json").write_text(
            json.dumps(
                {
                    "gate_passed": self.gate(),
                    "regression_suite_passed": self.regression_suite_passed,
                    "num_scenarios": len(self.scenarios),
                    "num_failed": len([s for s in self.scenarios if not s.passes()]),
                    "rounding_tolerance_per_operation": str(MAX_ROUNDING_DIFF_PER_OPERATION),
                    "known_failures": self.known_failures,
                },
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )
