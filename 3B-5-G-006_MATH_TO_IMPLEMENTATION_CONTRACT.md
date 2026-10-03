# 3B-5-G-006 — Mathematical-to-Implementation Contract

STATUS: CONTRACT BRIDGE — لا تصميم تنفيذ، لا كود
Production Changes: NONE · Schema Changes: NONE · Migration: NONE ·
Production Tests: NONE

**الغرض الوحيد**: تحويل كل نتيجة من G-005 (وما يتصل بها من G-001 إلى
G-004) إلى واحدة من خمس فئات، بلا أي قرار تنفيذي جديد يتسرَّب خلسة.
`ما أثبته G-005 ≠ ما اختاره G-006 ≠ ما سينفذه الكود` — هذا الفصل
مُلزِم طوال المستند.

**غير مُصرَّح به في G-006 (كما اشتُرِط، بلا استثناء)**: إنشاء
`CandidateState` فعلي · تعديل `correction_scope.py` · تعديل
`DeltaRecord` (لا إضافة، لا حذف، لا تعديل) · إنشاء `AccountingCorrection`
· اختيار حساب تصحيح · استخدام `post_immediate()` · أي Migration · أي
Production Test.

---

## MUST — نتائج رياضية لا يجوز مخالفتها في أي تنفيذ لاحق

1. **B.1**: `Inputs + TransferIn = COGS + TransferOut + EndingInventoryValue`
   لكل مستودع — أي تنفيذ يُخرِج نتيجة تخالف هذه الهوية له **خطأ حسابي
   مؤكَّد**، لا نقاش تصميمي. (PROVEN CONDITIONALLY على صحة تصنيف الحركة
   — التصنيف نفسه MUST أيضاً: كل حركة يجب تصنيفها بدقة إلى IN خارجي/
   OUT بيع/Transfer IN/Transfer OUT قبل أي حساب).
2. **B.2**: صيغة Δ لكل مستودع — نفس الإلزام.
3. **B.3**: على مستوى الشركة، `ΔInputs_total = ΔCOGS_total +
   ΔEndingInventoryValue_total` **بلا أي حد Transfer** — أي تنفيذ يُبقي
   حد Transfer ظاهراً في المعادلة النهائية على مستوى الشركة (بدل أن
   يتلاشى جبرياً) لديه خطأ.
4. **Transfer-OUT ≠ COGS، Transfer-IN ≠ external input** — إلزام
   تصنيفي مباشر، لا استثناء.
5. **تعميم تلاشي Transfer لأي عدد من المستودعات** (E.3) — أي تنفيذ
   يفترض أن هذا التلاشي يعمل فقط لمستودعين اثنين خطأ في الفهم، لا في
   الحساب فقط.
6. **إعادة الترتيب البحتة ⟹ `ΔInputs=0` و`ΔCOGS=−ΔEndingInventoryValue`**
   (E.4، مُشتَقة من B.2) — **بشرط** أن يكون الـCandidate صحيحاً وفق
   القيد التالي:
7. **Candidate Validity Constraints** (من §A في G-005): أي مولِّد
   Candidate مستقبلي **MUST** يحافظ على: هوية `Transfer OUT ↔ Transfer
   IN` (لا إعادة ترتيب طرف بمعزل عن الآخر)، هوية كل حركة، الكميات،
   دلالات المصدر/الوجهة، وقواعد ترتيب tie-group (Q2.5) — خرق أيٍّ منها
   يُبطِل صحة MUST رقم 6 أعلاه تلقائياً.

## MUST NOT — ثبت خطؤها، ممنوعة بلا نقاش

1. **`ΔCOGS = ΔPurchaseCost`** — DISPROVEN (B1، فرق فعلي 40 مقابل 30).
2. **`ΔCOGS = stored unit_cost difference`** مباشرة كقاعدة عامة —
   DISPROVEN (نفس المثال).
3. **إعادة استخدام تصفير `accumulate_average_cost()` عند `Q≤0`
   للحساب التاريخي** — DISPROVEN بمثال عددي فعلي (B3، فرق 30 وحدة نقدية
   محسوب فعلياً، لا نظرياً).
4. **اعتبار `total_qty≤0` نقطة إيقاف لانتشار الأثر** — مغلقة أصلاً منذ
   FD-001 REV1، مُعاد تأكيدها هنا رياضياً من زاوية القيمة لا الـScope
   فقط.
5. **اعتبار تصفير القيمة عند Q سالبة "محايداً اقتصادياً"** — خطأ حسابي
   مباشر، لا مجرد اختيار تصميم أسوأ.

## CONTRACT DEPENDENCY — تحتاج اعتماداً رسمياً صريحاً قبل أي استخدام

1. **`c(OUT) := A_{k-1}`** (قاعدة moving-average لتقييم البيع
   التاريخي) — **CONTRACT CANDIDATE، ليست PROVEN**. الأمثلة تُثبِت
   نتائجها إن اعتُمِدَت، لا إلزاميتها. **يجب اعتمادها صراحة كـ"Historical
   Costing Contract" رسمي بقرار منفصل قبل أن يبني عليها أي كود** — هذا
   الاعتماد نفسه **لم يحدث بعد في G-006**، هو خطوة تالية موثَّقة هنا
   كتبعية معلَّقة، لا قراراً متخذاً.
2. **آلية reconciliation الفعلية** (`Σ posted = Σ analyzed`، من G-004
   §4/§10) — الهوية الرياضية MUST، لكن **كيفية تنفيذ الفحص فعلياً**
   (استعلام؟ دالة؟ متى تُشغَّل؟) قرار تصميم منفصل يعتمد على قرارات G-006
   لاحقة، لا يُحسَم هنا.

## OPEN POLICY — أسئلة سياسة صريحة غير محسومة، لا يُخترَع حل لها

1. **E.5 — تقييم OUT عند `Q_{k-1}=0` بلا تعريف بديل** — **يبقى مفتوحاً
   بالكامل**. لا "آخر متوسط معروف"، لا "تكلفة الشراء التالي"، لا "صفر"
   — كل هذه سياسات جديدة، لا يُقرَّر أي منها هنا ولا في أي مستند لاحق
   دون قرار محاسبي صريح مستقل.
2. **Delta → AccountingCorrection linkage granularity** (event-level
   أم per-Delta — G-004 §8/§13) — موروثة OPEN، نية C (سطر 515) تميل
   نحو per-Delta لكن لم تُحسَم.
3. **Zero-effect Delta posting policy** (G-004 §5) — هل تُرحَّل أم تبقى
   نتيجة تحليلية بلا قيد؟ موروثة OPEN.
4. **Overlap policy** (تداخل CorrectionEvents، مفتوحة أصلاً في C نفسها
   منذ Q3.1) — موروثة OPEN، **لا تُحسَم في أي مرحلة G حتى إشعار آخر**.
5. **اختيار الحساب المحاسبي** (Option A/B/C/D، G-003) — موروثة OPEN
   بالكامل.
6. **Period/date semantics للتصحيح** (G-003 §11) — موروثة OPEN DESIGN
   DEPENDENCY.
7. **قرار إضافة `UniqueConstraint` على `(candidate_state_id,
   movement_id)`** (G-004) — موروثة، **لا Migration الآن** كما اشتُرِط
   صراحة سابقاً ومُعاد تأكيده هنا.

## IMPLEMENTATION CHOICE — طرق تنفيذ حرة، لا تغيّر الرياضيات

1. الشكل البرمجي الفعلي لإعادة حساب `A_k`/`V_k`/`Q_k` (دالة جديدة،
   إعادة هيكلة، إلخ) — **بشرط الالتزام الكامل بـMUST 1-7 أعلاه**؛
   `accumulate_average_cost()` نفسها **لا تُستخدَم كما هي** (MUST NOT 3)
   لكن يمكن الاستئناس ببنيتها العامة (حلقة تراكم) إن عُدِّل سلوك التصفير.
2. نموذج التنفيذ (batch/streaming/chunked)، آلية Resumability، تفاصيل
   Background execution — **كل ما وثَّقه G-001** يبقى OPEN DESIGN هناك،
   حر الاختيار **بعد** حسم ما سبق، لا علاقة رياضية مباشرة به.
3. **Atomicity Requirement (صياغة مصحَّحة — فصل المتطلب عن آلية تحقيقه)**:
   يجب أن تحقق عملية تصحيح الـ`CorrectionEvent` **ذرّية كاملة** — لا
   يجوز أن يصبح جزء من تصحيحات الحدث POSTED بينما يفشل جزء آخر. **هذا
   هو المتطلب (Contract Requirement)**، مُثبَت من G-004 §9. **آلية
   تحقيقه** (أي استدعاء API بالضبط، أين يقع `commit` تحديداً، حدود
   transaction الدقيقة) **تبقى Implementation Design بالكامل**، بشرط
   الحفاظ على هذه الخاصية. الجملة السابقة ("تأجيل `commit` للنهاية")
   كانت تصف **إحدى الطرق الممكنة** لتحقيق هذا المتطلب، لا حداً معمارياً
   محسوماً هنا — الفرق بين "يجب أن تكون العملية atomic" (Contract) و
   "الـcommit يجب أن يقع في الموضع X" (Implementation Choice) فرق جوهري
   يجب ألا يختلط.

---

## الخلاصة والبوابة

لا شيء أعلاه يُصرِّح ببدء أي implementation. هذا المستند **خريطة
تصنيف فقط**.

**ترتيب المراحل المحدَّث (Historical Costing Contract خطوة مستقلة
صريحة، لا مدفونة داخل G-007)**:
```
G-005  Mathematical Contract                    → CLOSED
   ↓
G-006  Mathematical → Implementation Contract    → CLOSED (هذا المستند)
   ↓
[قرار Historical Costing Contract]               → OPEN — الخطوة التالية
   ↓
G-007  Implementation Design / Candidate Engine  → لم يبدأ
   ↓
Production Implementation                        → ممنوع حالياً
```

| المرحلة | الحالة |
|---|---|
| G-001 | CLOSED |
| G-002 | CLOSED |
| G-003 | CLOSED |
| G-004 | CLOSED |
| G-005 | CLOSED |
| G-006 | CLOSED |
| **Historical Costing Contract** | **OPEN — الخطوة التالية الوحيدة المنطقية الآن** |
| G-007 | لم يبدأ بعد |
| Production Implementation | ممنوع حالياً |

**الخطوة التالية الوحيدة المنطقية الآن**: حسم Historical Costing
Contract تحديداً — تقرير رسمي (وثيقة/قرار مستقل، لا جزء من G-007) هل
`c(OUT):=A_{k-1}` هي فعلاً القاعدة المعتمَدة لتقييم OUT التاريخي، **مع
معالجة صريحة لحالة `Q_{k-1}=0 → OUT` بدل اختراع fallback** — لا يُدفَن
هذا القرار داخل تصميم G-007؛ يسبقه كخطوة مستقلة بذاتها. **لا قرار
يُتَّخَذ بشأنه هنا في G-006.**

**ما يجب ألا يحدث الآن (بلا استثناء)**: لا `CandidateState`، لا تعديل
`DeltaRecord`، لا تعديل `correction_scope.py`، لا `AccountingCorrection`،
لا Migration، لا `post_immediate()`، لا Production Tests.
