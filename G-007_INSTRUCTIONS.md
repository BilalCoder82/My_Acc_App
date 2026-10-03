# G-007 — Implementation Design / Candidate Engine

**الهدف**: تصميم خدمة تاريخية تبني `CandidateState` و`DeltaRecord` من
`ImpactScope` وفق DL-002 وDL-003 وDL-004، مع الحفاظ على ثوابت G-005 و
`00_PROJECT_METHODOLOGY.md`. **لا تنفيذ لـ`AccountingCorrection` في
هذه المرحلة.**

## التصميم

### 1. مصدر الـCandidate
- يبدأ الـCandidate من الحركات الموجودة في `ImpactScope` فقط.
- تُجمَع الحركات ضمن `TieGroup` كما عرَّفها G-005.
- داخل كل `TieGroup` يُستخدَم `OrderingAssumption` صريح لتحديد ترتيب
  المرشَّح — **لا** يُخترَع ترتيب يعتمد على `id` أو ترتيب الإدراج إن لم
  يكن جزءاً من ordering assumption معتمَد.
- كل `StockTransfer` يبقى صالحاً كهوية واحدة: Transfer OUT وTransfer IN
  مرتبطان بنفس عملية النقل — لا يجوز للـCandidate فصل هويتهما أو تغيير
  الكميات/المستودعات.
- لا تتغيّر هوية الحركة أو كميتها أو اتجاهها أو مصدرها؛ الـCandidate
  يغيّر التقييم التاريخي الناتج عن الترتيب/التصحيح فقط.

### 2. Historical Cost Calculation
لكل (item, warehouse) يُعاد بناء الحالة بالتسلسل:
```
Q_k = Q_{k-1} + σq
V_k = V_{k-1} + σq·c_k
```
وعند حركة OUT: `c(OUT) = A_{k-1}` وفق **DL-002**، حيث
`A_{k-1} = V_{k-1}/Q_{k-1}` **عندما يكون `Q_{k-1} > 0`** فقط. لا
يُستخدَم `accumulate_average_cost()` كما هي كمحرك تاريخي، ولا يجوز أن
يؤدي منطق العرض الحالي الذي يُصفِّر القيمة عند `Q≤0` إلى إخفاء حالة
تاريخية.

### 3. `Q≤0 → OUT`
إن وصلت حركة OUT وكان `Q_{k-1} ≤ 0`: **لا تُختار تكلفة بديلة**. يُنشَأ/
يُسجَّل على مستوى الـCandidate ما يكفي لتحديد **Historical Costing
Exception** وربطها بالحركة المعنية؛ الحالة **غير صالحة** لإنتاج
`AccountingCorrection` نهائي مبني على تكلفة مخترَعة.
**ممنوع**: last known average · next purchase cost · zero cost · أي
fallback آخر غير معتمَد. **لا** يعني هذا إيقاف بناء الـCandidate
بالكامل — المطلوب فقط اكتشاف الحالة، وسمها، ومنع مرورها بصمت إلى
الترحيل النهائي.

### 4. DeltaRecord
لكل Candidate: `delta_inventory_value(m) = σq·c_candidate − σq·c_baseline`،
مع الحفاظ على تعريف G-005: External Purchase IN → Inputs · External
Sale OUT → COGS · Transfer OUT/IN → حركة اقتصادية بين مستودعين، ليست
COGS ولا External Input، **لا** `JournalEntry` لها. `delta_quantity`
يبقى وفق semantics G-004: قيمة regime-level عند الصف الحامل، وNULL في
الصفوف المعتمِدة على forward-fill — **ليست** دليل قيمة مجهولة.

### 5. سلامة DeltaRecord
يُضاف `UniqueConstraint(candidate_state_id, movement_id)` كـR2
migration واحدة وفق **DL-004**. لا يجوز أن يؤدي أي تجميع لاحق إلى
مضاعفة أثر الحركة؛ إن احتاج التصميم تمثيل أكثر من أثر لنفس الحركة، يكون
ذلك خارج `DeltaRecord` أو وفق نموذج صريح آخر — **لا تُكسَر الـconstraint
لتجاوز المشكلة**.

### 6. Atomicity
التصميم يحدِّد حدود transaction بوضوح: بناء Candidate/Delta وإدخال
نتائجها المرتبطة بالـCorrectionEvent **قابل للـcommit أو rollback
كوحدة واحدة**. نقطة الالتزام النهائية بعد اكتمال كل الكتابات. أي فشل
قبلها ⟹ rollback كامل، لا Candidate/Delta جزئي صامت. **لا** تنفيذ فعلي
لـ`AccountingCorrection` أو `post_immediate()` هنا — آلية الـtransaction
تصميم تقني فقط، لا تُستخدَم لحسم سياسة الحساب المحاسبي.

## خارج نطاق G-007
اختيار حساب التصحيح A/B/C/D · سياسة Overlap · Period/closed-period
semantics · إنشاء `AccountingCorrection` فعلي · إنشاء `JournalEntry`
للتصحيح · أي fallback لـ`Q≤0` · أي إعادة Discovery لإثبات G-001 إلى
G-006 أو HCC-001.

## معيار الإنهاء
التصميم مكتمل عندما يمكن تنفيذ Candidate Engine منه دون اختراع سياسة
محاسبية جديدة، وواضح فيه: مصدر وترتيب Candidate · إعادة بناء Q/V/A ·
تطبيق DL-002 · اكتشاف DL-003 ومنع التكلفة المخترَعة · Delta semantics ·
Transfer semantics · DL-004 constraint · حدود transaction والـrollback.

**القرارات المرجعية**: DL-002، DL-003، DL-004 (`01_PROGRAMMER_COLLABORATION_PROTOCOL.md`).
