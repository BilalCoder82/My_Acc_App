# 3B-5-G — Discovery Scope

**قبل أي تصميم**: هذا المستند يفحص ما يحمله الكود والوثائق الحالية فعلاً
(بحث مباشر في `app/models.py`، `3B-5-C_DISCOVERY_DESIGN_QUESTIONS.md`،
`3B-5-C_SCHEMA_IMPLEMENTATION_REPORT.md`، `app/services/returns.py`،
`app/services/correction_detection.py`، والاختبارات ذات الصلة)، ثم يصنّف
كل بند من G-001 إلى G-010 المقترحة إلى واحدة من أربع حالات:

- **DECIDED IN C** — محسوم فعلاً بإثبات في مرحلة C، ليس سؤال G.
- **SCHEMA EXISTS, UNUSED** — الجدول/الحقل موجود ومُختبَر بنيوياً، لكن لا
  خدمة تملؤه بعد — هذا عمل G التنفيذي، لا تصميم من الصفر.
- **OUT OF SCOPE BY PRIOR DECISION** — استُبعِد صراحة من هذا المسار في C،
  إدخاله الآن قرار توسيع نطاق، لا اكتشافاً.
- **GENUINELY OPEN** — لم يُبحَث في C إطلاقاً، هو فعلاً سؤال G جديد.

هذا الفصل ضروري: البدء بـG-002..G-008 كأسئلة مفتوحة من الصفر كما اقتُرح
يعيد فتح قرارات مُثبَتة رياضياً بالفعل — بالضبط ما رفضتم فعله مع F.

---

## أولاً: ما وجدته من "prior art" لم يذكره أحد بعد في نقاش G

### الجدول موجود فعلاً في `app/models.py`
`CorrectionEventCandidateState`، `CorrectionEventDeltaRecord`،
`CorrectionEventTieGroup`، `CorrectionEventOrderingAssumption` —
معرَّفة بالكامل، ومُختبَرة بنيوياً في `tests/test_correction_event_schema.py`،
**لكن لا توجد أي خدمة في `app/services/` تملؤها بعد.** أي: G ليس تصميم
جداول جديدة من الصفر لهذه الأجزاء تحديداً — هذه الجداول ناتجة عن مرحلة C
وتحمل تعليقات تشير مباشرة لقرارات Q3.3.x. عمل G هنا هو **تنفيذ الخدمة
التي تكتب فيها**، وفق ما قرّرته C، لا إعادة تصميم شكلها.

### `CorrectionEventStatus` يحوي `WITHDRAWN` فعلاً
السبع حالات (`CANDIDATE..ACCOUNTING_CORRECTION, STALE, WITHDRAWN`) موجودة
في enum فعلياً ومُختبَرة (`test_correction_event_schema.py`). **لكن**
`correction_detection.py` نفسه يعلّق صراحة: *"لا WITHDRAWN (يحتاج امتداد
منفصل لنفس آلية..."* — أي أن منطق *متى* يحدث الانتقال لـWITHDRAWN تلقائياً
(مثال C: الحركة المُسبِّبة تُعكَس قبل أن يطلب المحاسب تحليلاً كاملاً) **غير
منفَّذ بعد**. هذا سؤال G حقيقي (أدرجته تحت G-009 أدناه)، لكنه ليس سؤال
schema — الـschema جاهز.

---

## ثانياً: تصنيف G-001 إلى G-010 المقترحة

### G-001 — Execution Model & Scalability
**GENUINELY OPEN.** لا شيء في وثائق C يناقش batching/streaming/chunking/
resumability — الموضوع لم يُطرَح إطلاقاً هناك. صحيح أنه يجب أن يأتي أولاً.

**إضافة مهمة غير مطروحة بالصياغة الحالية لـG-001**: أبعاد الحجم في هذا
النظام **بُعدان مستقلان**، ليس بُعداً واحداً. `Q3.3.7` في وثيقة C يُثبِت
بُعداً ثانياً منفصلاً تماماً عن طول traversal: **عدد الـCandidates نفسه
ينمو بضرب تراكمي (cross-product) لا بالجمع** — مجموعتا تعادل (tie-groups)
بثلاثة حلول لكل منهما تُنتِجان حتى 9 candidates متسقين عالمياً، لا 6. سجل
Scope كبير الحجم بالحركات، وسجل Scope كبير بعدد الـcandidates المتفرعة
عنه، مشكلتان مختلفتان حسب نص C نفسه — قد يكون Scope صغيراً بالحركات لكن
هائلاً بعدد التوافيق المتسقة لو تضمّن عدة tie-groups مستقلة. **G-001 يجب
أن يغطي كلا البُعدين صراحة، لا "طول التاريخ" فقط كما صيغ حالياً.**

### G-002 — Historical Baseline
**DECIDED IN C (Q3.3.1, Q3.3.2).** Baseline = مرجع مباشر إلى
`InventoryMovement`/`JournalEntry` الحية POSTED فعلاً — **بالإشارة لا
بالنسخ**، وهذا ليس تفضيل تخزين اعتباطي بل امتداد مباشر لمبدأ Q3.2 نفسه
("خزّن ما يمثّل الحقيقة/المُدخَل، احسب العلاقات المشتقة عند الحاجة").
عمل G هنا: التحقق من أن التنفيذ الفعلي (عند كتابته) يلتزم بهذا — لا فتح
سؤال "ما هو Baseline" من جديد.

### G-003 — Candidate vs Baseline State
**DECIDED IN C (Q3.3.2, Q3.3.3, Q3.3.7).** `CandidateState = reference-
to-baseline + delta-vector + provenance`. كل Candidate يجب أن يكون حلاً
متسقاً عالمياً لكل tie-groups دفعة واحدة (Q3.3.7)، لا حلاً محلياً لمجموعة
تعادل واحدة. provenance محدد بدقة (Q3.3.3): مرجع لـtie-group + الترتيب
المفترَض بصيغته المختزلة. هذا كله محسوم؛ الجدول (`CorrectionEventCandidateOrderingLink`
مع قيد DB `uq_candidate_one_assumption_per_tiegroup`) موجود فعلاً ينفّذه.

### G-004 — Historical Recalculation
**DECIDED IN C جزئياً + يحتاج تحققاً من التنفيذ.** خوارزمية
`accumulate_average_cost()` نفسها (Q2.1/3B-5-B) هي الأساس الرياضي
المُعتمَد بالفعل — Q3.3.4 تُعيد تحليلها لتحديد ما هو بدائي مقابل مشتق (انظر
G-005). ما تبقى فعلياً لـG هنا هو **تنفيذي بحت**: كيف تُشغَّل هذه
الخوارزمية عبر traversal الفعلي (نتاج F) لكل candidate — لا تصميم صيغة
حساب جديدة.

### G-005 — Numerical Delta
**DECIDED IN C (Q3.3.4) — بإثبات حسابي فعلي، ليس مجرد قرار.** فقط
`Δquantity` و`Δinventory_value` بدائيان يستحقان تخزيناً مستقلاً؛ كل الباقي
(`average_cost`, `movement_unit_cost`, `cogs_consequence`) مشتق يُحسَب عند
الطلب. والأهم: `Δquantity` **ثابت على مستوى "regime segment"** (لا يتغير
إلا عند عبور Transfer) — **مُثبَت حسابياً** عبر سيناريو متعدد القفزات (9
خطوات، `ΔQ(W1)=40` ثابتة رغم إضافات مستقلة لاحقة). `Δinventory_value` ليس
كذلك ويحتاج تخزيناً لكل حركة. الجدول (`CorrectionEventDeltaRecord`
بحقلي `delta_quantity`/`delta_inventory_value` فقط) ينفّذ هذا بالضبط. **لا
سؤال تصميم مفتوح هنا** — عمل G هو التنفيذ وفق هذا، لا إعادة النقاش.

### G-006 — Cross-Warehouse Propagation
**DECIDED IN C (Q2.4، ممتد في Q3.3.4).** Transfer **conduit** لا حاجز:
"A Transfer is a boundary for `Δquantity` distribution; it is not a
boundary for the total economic effect" — نص صريح. اختُبِر دورة كاملة
W1→W2→W1 وأثبتت أن تصحيحاً في W1 يعود ليغيّر متوسط تكلفة W1 نفسه عبر دورة
تحويل (9.47 مقابل 13.60). هذا **يتسق تماماً** مع ما أثبتناه في إغلاق F
(عزل التكلفة بالمستودع + عبور فقط عبر StockTransfer صريح، §46
WORKFLOW.md) — لا تناقض بين قراري F وC، بل الثاني يوسّع الأول رياضياً.
لا سؤال تصميم مفتوح.

### G-007 — Returns
**⚠️ OUT OF SCOPE BY PRIOR DECISION — ليس مجرد "غير مُحسوم بعد".**
وثيقة C تنص صراحة أكثر من مرة: *"Return movements were not separately
tested — per the existing scope decision, Finding C/Returns stays out of
this design track entirely"*، و*"per the existing scope exclusion"*. أي
أن استبعاد Returns لم يكن إغفالاً، بل **قراراً واعياً** بإبقائها كمسار
مستقل تماماً عن كل تحليل التعادل الزمني (Q2.5) ونموذج الـCandidate كله.
مشكلة `_return_unit_cost()` (`app/services/returns.py`) حقيقية وموجودة
فعلاً بالكود (تعليق صريح فيه يوثّق إصلاحاً سابقاً لتضارب مصدرين لنفس
الرقم)، **لكن إدخال Returns إلى مسار G الحالي الآن قرار توسيع نطاق يحتاج
موافقة صريحة منكم**، وليس بنداً يُدرَج تلقائياً برقم G-007 كأنه استمرار
طبيعي لما سبق. أقترح تأجيله كمسار مستقل صراحة (كما قرّرت C أصلاً) إلى أن
يُتَّخَذ قرار واعٍ بضمه.

### G-008 — CorrectionEventDeltaRecord / AccountingCorrection
**DECIDED IN C جزئياً (Q3.4، Q3.5) + نقطة مفتوحة حقيقية واحدة.** الهوية
الثلاثية محسومة: `Δ(مدخلات مصححة) = Δ(COGS) + Δ(قيمة مخزون آخر المدة عبر
كل مستودع)`. معالجة Transfer كـconduit بلا قيد محاسبي خاص به محسومة.
توازن القيد (`is_balanced()`) مضمون فعلاً بالبنية الحالية (`sanity_guard.py`
عبر `posting.py`) — ليس عملاً جديداً. **لكن**: اختيار الحساب المحاسبي
(حساب أصلي مقابل حساب تصحيح مخصص، Q3.5.1-3) نفسه موصوف بوضوح في C بأنه
*"decision-relevant information, not a decision"* — أي **سياسة محاسبية
مفتوحة فعلاً تنتظر قراركم أنتم**، لا مجرد تفصيل تنفيذ. كذلك **Correction
Completeness** (هل كل الـScope المحلَّل تم تحويله فعلياً لقيد، أم جزء
منه فقط مؤجَّل) مُسجَّلة في C صراحة كـ*"a genuine open design point... not
resolved here"* — بُعد رابع مستقل عن `scope_completeness`/`chronology_basis`/
`candidate_states`، لم يُحسَم بعد.

### G-009 — Multiple/Overlapping Events
**جزئياً DECIDED، وفيه الفجوة التنفيذية الحقيقية الوحيدة غير المتعلقة
بالأداء.** الاتساق العالمي لكل Candidate محسوم (Q3.3.7، أعلاه). لكن
انتقال `CANDIDATE → WITHDRAWN` — عندما تُعكَس الحركة المُسبِّبة قبل طلب
تحليل كامل — **schema جاهز، منطق الاكتشاف غير منفَّذ** (انظر القسم الأول
أعلاه). هذا عمل G فعلي، لا نقاش تصميمي من الصفر: الشكل معروف
(`CANDIDATE → WITHDRAWN`، طرفي ولا يُعاد تدويره مثل `PENDING → STALE`)،
الناقص هو "متى نكتشف الانسحاب ونطبّقه" في `correction_detection.py`.

### G-010 — Performance / Operational Safety
يتداخل مباشرة مع G-001 — أقترح دمجهما فعلياً في مرحلة الأسئلة (ليسا
مستقلين: قرار batching/streaming في G-001 يحدد ما هو قابل للقياس أصلاً في
G-010)، لا الإبقاء عليهما كبندين منفصلين بالترتيب النهائي.

---

## الخلاصة العملية

| البند | الحالة | الفعل المطلوب من G |
|---|---|---|
| G-001 | GENUINELY OPEN (+ بُعد ثانٍ مكتشَف: تفجّر عدد candidates) | تصميم كامل، يشمل كلا بُعدي الحجم |
| G-002 | DECIDED (Q3.3.1/3.3.2) | تحقق تنفيذي فقط |
| G-003 | DECIDED (Q3.3.3/3.3.7) | تحقق تنفيذي فقط |
| G-004 | DECIDED جزئياً | تنفيذ عبر traversal F |
| G-005 | DECIDED بإثبات حسابي (Q3.3.4) | تنفيذ فقط، لا نقاش |
| G-006 | DECIDED (Q2.4) — يتسق مع قرار F | تنفيذ فقط |
| G-007 | **OUT OF SCOPE BY PRIOR DECISION** | قرار توسيع نطاق صريح مطلوب أولاً، لا بند تصميم تلقائي |
| G-008 | DECIDED جزئياً + سياستان مفتوحتان فعلياً (اختيار الحساب، Correction Completeness) | قرار سياسة محاسبية، ثم تنفيذ |
| G-009 | DECIDED جزئياً + فجوة تنفيذ حقيقية (WITHDRAWN trigger) | تنفيذ منطق الاكتشاف فقط |
| G-010 | يُدمَج مع G-001 | — |

**التوصية**: تبدأ أسئلة G الفعلية من G-001 (بصيغته الموسَّعة ببُعدين) ثم
مباشرة إلى نقطتي G-008 المفتوحتين فعلاً (سياسة الحساب المحاسبي +
Correction Completeness) — هذه الثلاث هي الأسئلة الحقيقية غير المحسومة.
البقية عمل تنفيذ يتحقق من الالتزام بما قرّرته C، لا تصميم من جديد. G-007
يحتاج قراركم الصريح بالتوسع قبل أن يدخل النقاش إطلاقاً.
