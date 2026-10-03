# 3B-5-G-002_C_CONFORMANCE_VERIFICATION.md

**Status: DISCOVERY / VERIFICATION**
**Production Changes: NONE**
**Schema changes: NONE** · **Migration: NONE** · لا تنفيذ Candidate
execution، لا recalculation، لا accounting correction، لا UI/background
execution، لا WAL/concurrency، **لا إصلاح `.first()`**. لا إعادة فتح
G-001. لا إعادة تصميم أي قرار في C.

---

## 1. Executive Summary

معظم قرارات C **قابلة للتنفيذ كما صُمِّمت** — لا تناقض حقيقي واحد وُجِد.
الأثر الأهم لهذه الجولة: نقطة **CONFORMING WITH CONDITION** واحدة جديدة
لم تُذكَر من قبل في أي مستند سابق (G-002-C أدناه)، تخص إعادة استخدام
`accumulate_average_cost()` في الحساب التاريخي لاحقاً. لا CONTRADICTED
واحدة. عدة بنود **NOT IMPLEMENTED YET** متوقَّعة (طبيعة مرحلة G قبل
البناء). بندان يبقيان **OPEN IN C** كما هما، بلا حسم.

## 2. C Decision Inventory (المرجع لكل صف في المصفوفة)

Q3.3.1 (Baseline)، Q3.3.2/3.3.3/3.3.7 (CandidateState/provenance/global
consistency)، Q3.3.4 (Delta primitives + regime-constancy)، Q2.4
(Transfer conduit)، Q3.1 (Root Cause components + overlap "undecided")،
WORKFLOW.md §39/§46 (per-warehouse costing، تقييم الخروج بتكلفته
الخاصة)، §Q1 staleness principle، تصنيف Returns كـ"out of scope"
(مُستشهَد سابقاً في G-001).

## 3. Conformance Matrix

| C Decision | Evidence (من C) | G-001/F Evidence | Classification | Conditions / Gap |
|---|---|---|---|---|
| **A. Baseline by reference** | Q3.3.1: مرجع مباشر لـ`InventoryMovement`/`JournalEntry` POSTED، لا نسخ | Q3-A/B G-001: مؤكَّد أن POSTED لا يُعدَّل/يُحذَف أبداً (بحث شامل) — هذا **يُقوّي** لا يناقض قرار المرجعية | **CONFORMING** | لا شيء |
| **B. CandidateState (ref+delta+provenance, tie-group, ordering, global consistency)** | Q3.3.2/3/7 | جداول `CorrectionEventCandidateState`/`TieGroup`/`OrderingAssumption` موجودة schema، مُختبَرة بنيوياً (`test_correction_event_schema.py`)، **صفر خدمة تملؤها** | **NOT IMPLEMENTED YET** | البنية لا تتعارض مع F؛ لا خدمة بعد تبنيها |
| **C. Historical calc basis — accumulate_average_cost()** | Q3.3.4 (الأساس الرياضي) | تحقَّقت من الكود مباشرة الآن: الدالة تُعالِج **كل** الحركات الممرَّرة بلا توقف مبكر في الحلقة نفسها (متوافق مع FD-001 REV1) | **CONFORMING WITH CONDITION** ⚠️ | انظر تفصيل أدناه — شرط جديد لم يُسجَّل سابقاً |
| **current-state vs historical query separation** | ضمني في Q3.3.1/معماري | `get_item_stock_summary()` موثَّقة صراحة في الكود نفسها بأنها استعلام **حالة حالية**؛ الحساب التاريخي لـG **غير موجود بعد** فلا يمكن مقارنته مباشرة | **CONFORMING** | الفصل موجود فعلاً في التوثيق الحالي، يجب الحفاظ عليه عند بناء G |
| **D. CorrectionEventDeltaRecord (delta_quantity, delta_inventory_value)** | Q3.3.4 | Schema موجود بحقلين بالضبط كما قرَّرت C، **صفر مسار إنشاء/استهلاك** | **NOT IMPLEMENTED YET** | لا إنشاء مسارات الآن |
| **E. Transfer = conduit لا حاجز اقتصادي، عزل مستودع مستقل** | Q2.4 + §46 | مؤكَّد من F (اختبار 1.5b: W2 مستقل بلا Transfer = خارج النطاق) + تحققت الآن مباشرة: `inventory_transfer.py` **لا يُنشئ JournalEntry إطلاقاً** لأي تحويل | **CONFORMING** | لا شيء — تطابق كامل بين C وF والكود الفعلي |
| **F. لا `total_qty<=0` كقاعدة إيقاف عامة لـpropagation** | — (C لا تفترض هذا في نص Scope-traversal) | FD-001 REV1/F: `total_qty` أُزيلت من `correction_scope.py` بالكامل، مؤكَّد بـ28/28 اختبار | **CONFORMING** — **لكن ⚠️ انظر التحذير في G-002-C أعلاه**: نفس التعبير الحرفي `total_qty <= 0` **موجود فعلاً** داخل `accumulate_average_cost()` نفسها (سطر 65) — في سياق مختلف تماماً (حساب متوسط تكلفة الحالة الحالية، لا إيقاف traversal) | لا تناقض مع F (طبقات مختلفة تماماً)، لكن هذا يزيد أهمية شرط G-002-C |
| **G. Returns خارج نطاق G الحالي** | Q2.5 (مُستشهَد سابقاً) | لا استخدام لـ`_return_unit_cost()` في أي بحث G-001 | **CONFORMING** | لا توسيع نطاق |
| **H. Δ(inputs) = Δ(COGS) + Δ(ending inventory value)، لا JournalEntry لـTransfer** | Q3.4/Q3.5 | تحققت الآن: صفر `JournalEntry(` في `inventory_transfer.py` — مؤكَّد مباشرة، لا افتراضاً | **CONFORMING** | اختيار الحساب المحاسبي نفسه (لا هذه الهوية) هو موضوع G-003، لم يُمَس هنا |
| **I. Overlap ممكن بنيوياً، لا افتراض `A∩B=∅`، لا علاقة أب/ابن تلقائية** | Q3.1/Q3.2 بند 9 | Q3-C في G-001: صفر كود تطبيقي للتقاطع، لكن البنية (schema) **تسمح** به دون تناقض — مؤكَّد | **CONFORMING (بنيوياً) / OPEN IN C (سياسياً)** | البنية تسمح؛ **سياسة** التعامل مع التقاطع **OPEN IN C نفسها** — لا تُحسَم هنا |
| **J. Candidate ordering / provenance / global consistency** | Q3.3.3/3.3.7 | اكتشاف G-001 الجديد: `.first()` بلا `order_by` على `CorrectionEventRootCause` — **DETERMINISTIC UNDER CURRENT SINGLE-ROOT INVARIANT فقط** | **CONFORMING WITH CONDITION** | الشرط: يبقى صحيحاً فقط طالما لم يُفعَّل `REPLACEMENT` مرتبط بحدث موجود — موثَّق سلفاً في G-001، **لا يُصلَح هنا** |
| **K. Staleness principle / analysis_as_of / revalidation-before-commit** | Q1 (staleness) | مؤكَّد بالكامل في G-001 Q3 — مبدأ محسوم، تطبيق غير موجود | **CONFORMING (مبدأ) / NOT IMPLEMENTED YET (تطبيق)** | لا revalidation، لا index، لا WAL هنا |
| **L. Resumability لا تفترضها C آلية محدَّدة تتعارض مع G-001 Q5** | — (C لا تقرر آلية resumability محدَّدة) | Q5 G-001: حتمية Candidate-level **OPEN**، الطبقة غير منفَّذة | **NOT IMPLEMENTED YET** | لا اختراع آلية checkpoint هنا |

### تفصيل شرط G-002-C (الأهم في هذه الجولة)
`accumulate_average_cost()` الحالية (تُستخدَم اليوم فقط من
`get_item_stock_summary()` لعرض **الحالة الحالية**) تحتوي فعلياً على
`if total_qty <= 0: return avg_cost=0, inventory_value=0`. هذا **صحيح
تماماً** للحالة الحالية (لا مخزون فعلي = لا متوسط تكلفة ذو معنى للعرض).
**لكن**: إن أعاد G استخدام هذه الدالة حرفياً لحساب "الحالة التاريخية عند
كل نقطة توقف على طول Candidate" (كما قد يوحي "الأساس الرياضي" في
Q3.3.4)، فإن أي نقطة تاريخية يصل عندها الرصيد التراكمي إلى صفر أو سالب
(بالضبط الحالات التي أثبت F أنها **يجب ألا تُوقِف الانتشار**) ستُصفِّر
متوسط التكلفة/قيمة المخزون **المُبلَّغة عند تلك النقطة تحديداً** — وهذا
قد يُعيد إدخال أثر شبيه بمشكلة `total_qty` التي حلَّتها FD-001 REV1، لكن
على **مستوى قيمة الحساب لا مستوى عضوية Scope**. **هذا ليس تناقضاً مع C**
(C لم تقرر صراحة إعادة استخدام هذه الدالة بعينها لهذا الغرض) — **لكنه
شرط صريح يجب تسجيله الآن قبل G-003/G-004**: أي استخدام مستقبلي لهذه
الدالة تحديداً في السياق التاريخي يحتاج مراجعة صريحة لسلوك
`total_qty<=0` أولاً، لا افتراض أنها "نفس المعادلة" الآمنة للسياقين.

## 4. Contradictions Found
**لا شيء.** لم يظهر أي دليل مباشر يناقض أي قرار C بالمعنى المطلوب
(دليل مباشر، لا قلق نظري). التعبير المشترك `total_qty<=0` بين
`accumulate_average_cost()` وذكريات FD-001 REV1 **تشابه لفظي بين طبقتين
مختلفتين تماماً**، لا تعارضاً فعلياً — مُسجَّل كشرط (القسم أعلاه)، لا
كتناقض.

## 5. Conditions Required for Implementation
1. G-002-C: مراجعة صريحة لسلوك `total_qty<=0` في `accumulate_average_cost()`
   قبل أي إعادة استخدام لها في حساب تاريخي — لا تُستخدَم "كما هي" دون
   هذه المراجعة.
2. G-002-J: `.first()` في `build_impact_scope()` يبقى صحيحاً **فقط**
   ضمن قيد "جذر واحد لكل حدث" — أي عمل مستقبلي يُفعِّل `REPLACEMENT`
   مرتبط يجب أن يُعالِج هذا أولاً (موثَّق سلفاً في G-001، لا جديد هنا،
   فقط مُعاد تأكيده كشرط تنفيذي رسمي).

## 6. Decisions Still OPEN IN C
- **Overlap handling policy** (Q3.1) — ماذا نفعل فعلياً عند اكتشاف
  تقاطع Scopes، لا مجرد إمكانية حدوثه بنيوياً.
- كل ما وُثِّق مسبقاً كـOPEN في G-001 (acceptance race window التفصيلي،
  Pending Approval churn كسؤال منتج) يبقى OPEN بنفس الحالة — لا تغيير.

## 7. Decisions NOT IMPLEMENTED YET
CandidateState/TieGroup/OrderingAssumption (B) ·
CorrectionEventDeltaRecord creation/consumption (D) ·
Revalidation/staleness enforcement (K) · Resumability mechanism (L).
كل هذه **متوقَّعة** في هذه المرحلة من G — لا تُصنَّف كـFAIL.

## 8. No-Change Confirmation
تحقَّقت: لا `str_replace`/`create_file`/`bash` كتابة واحدة طالت
`app/` أو `tests/` أو أي schema/migration طوال هذا المستند بأكمله —
قراءة وبحث فقط (`grep`/`view`)، كما اشتُرِط صراحة.

## 9. G-002 Exit Gate
- كل قرار C ذو صلة له تصنيف. ✅
- كل تعارض ظاهر مع F/G-001 حُسِم صراحة (CONFORMING/CONDITION/
  UNIMPLEMENTED/OPEN) — لا CONTRADICTED واحدة. ✅
- لا قرار تصميمي جديد أُدخِل خلسة (شرطا القسم 5 كلاهما **تسجيل قيد
  موجود سلفاً**، لا قراراً جديداً). ✅
- لا كود إنتاج تغيَّر، لا schema، لا migration. ✅

**النتيجة: G-002 → CLOSED. لا تناقض حقيقي يوقف الانتقال.**
التالي: **3B-5-G-003 — Accounting Policy** (أول قرار تصميمي جديد نسبياً
في هذه السلسلة: سياسة اختيار الحساب المحاسبي لتصحيح الفروقات).
