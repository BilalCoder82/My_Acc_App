# Baseline — v1-accounting-engine-stable

هذا الملف يوثّق ما يعنيه هذا الـBaseline تحديداً، وكيف يُستعاد ويُتحقق
من سلامته من بيئة نظيفة. لا يُعدَّل هذا الملف لاحقاً — أي تحديث يستحق
Baseline جديداً بتاريخه وtag خاص به.

## ما يشمله هذا الـBaseline

1. **محرك الترحيل مُصحَّح**: `_jline`/`_jline_base` (WORKFLOW.md §29, §30)
   + حارس اتساق مُدمَج فعلياً في `_jline` (`app/services/sanity_guard.py`).
2. **بوابة قبول آلية اجتازت فعلياً** (`reports_out/fuzz_report.md` +
   `.json` في هذا المجلد) — راجع WORKFLOW.md §31 لتفاصيل الاشتقاق.
3. **`schema_snapshot.sql`**: بنية قاعدة البيانات الكاملة كما ينتجها
   `Base.metadata.create_all()` في هذه اللحظة بالضبط — مرجع لما قبل Alembic.
4. **`environment.txt`**: نسخة Python والحزم الأساسية (SQLAlchemy, PySide6)
   المستخدمة فعلياً وقت هذا الـBaseline.
5. **`gate_report.json` / `gate_report.md`**: نسخة مجمَّدة من نتيجة
   `tests/run_gate.py` وقت هذا الـBaseline (منفصلة عن `reports_out/`
   الذي سيُستبدَل بتشغيلات لاحقة).

## Known Failures وقت هذا الـBaseline (موروثة، لم تُحل هنا)

- `tests/test_migration_safety.py` — راجع WORKFLOW.md §31.5. شرط لازم
  لقبول Alembic لاحقاً، وليس لهذا الـBaseline.

## كيف تستعيد هذا الـBaseline وتتحقق منه من بيئة نظيفة

```bash
git clone --branch v1-accounting-engine-stable <repo-url> restore-check
cd restore-check
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 tests/run_gate.py   # يجب أن ينتهي بـ exit code = 0
```

إذا فشل أي جزء من `run_gate.py` بعد استعادة الـBaseline في بيئة نظيفة،
فهذا يعني أن الـBaseline لم يكن قابلاً للاستعادة فعلياً رغم وجود الـtag،
ويجب معالجته قبل المتابعة إلى Alembic — لا يُفترض نجاحه لمجرد وجود الـtag.
