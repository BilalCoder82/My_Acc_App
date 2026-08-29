# Baseline — v2-accounting-engine-with-alembic-and-inventory-integrity

هذا الملف يوثّق ما يعنيه هذا الـBaseline تحديداً (النسخة الثانية —
`v1-accounting-engine-stable` أصبح مرجعاً تاريخياً فقط، يسبق دمج Alembic
بالكامل وإصلاح §39). لا يُعدَّل هذا الملف لاحقاً — أي تحديث يستحق
Baseline جديداً بتاريخه وtag خاص به.

## ما يضيفه v2 عن v1

1. **Alembic مدمج فعلياً بمسار التطبيق** (`app/db.py::open_company_db`)،
   مُختبَر بخمس حالات معزولة + مسار تطبيق حقيقي كامل (WORKFLOW.md §33).
2. **مجلد alembic أُعيد تسميته لـ`alembic_migrations/`** — يزيل تعارضاً
   حقيقياً وقع فعلياً عند التشغيل على جهاز حقيقي (§38).
3. **اختبارات عدوانية للعملات والمخزون** بـOracle مستقل، 23 تحققاً (§35).
4. **دورة مخزون كاملة** عبر مستندات منفصلة، بربط الكمية والتكلفة والقيد
   المحاسبي والتقارير معاً، 40 تحققاً (§37).
5. **إصلاح جوهري في `get_item_stock_summary`**: حركات الخروج تُقيَّم
   بتكلفتها التاريخية المخزَّنة، لا بمتوسط مُعاد حسابه — يُصحِّح تناقضاً
   حقيقياً كان موجوداً بين تقرير المخزون ودفتر الأستاذ بعد أي مرتجع شراء
   مرتبط بفاتورة أصلية (§39). قاعدة محاسبية موثَّقة رسمياً: "التاريخ لا
   يُعاد تسعيره بأثر رجعي".

## Known Failures / فجوات منتجية موروثة (غير محلولة هنا عمداً)
- `receipts/payments/settlement/fx_gain_loss` — غير موجودة بالمشروع
  إطلاقاً (§35.6). قرار منتجي متروك: تُبنى الآن أم تُؤجَّل.
- `app/migrations/runner.py` لم يُحذف بعد (يُستخدم لتحويل عملاء قدامى
  لأول مرة فقط — §34). حذفه قرار مستقبلي منفصل.

## كيف تستعيد هذا الـBaseline وتتحقق منه من بيئة نظيفة

```bash
git clone --branch v2-accounting-engine-with-alembic-and-inventory-integrity <repo-url> restore-check
cd restore-check
python3 -m venv .venv && source .venv/bin/activate   # أو .venv\Scripts\activate على ويندوز
pip install -r requirements.txt
python3 tests/run_gate.py   # يجب أن ينتهي بـ exit code = 0
```

