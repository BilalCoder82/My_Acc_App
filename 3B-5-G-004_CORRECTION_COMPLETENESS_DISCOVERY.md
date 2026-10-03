# 3B-5-G-004 — Correction Completeness Discovery

Status: DISCOVERY / DESIGN
Production Changes: NONE · Schema Changes: NONE · Migration: NONE ·
لا اختيار Option A/B/C/D · لا إنشاء حساب Correction Account · لا
إضافة many-to-many · لا تنفيذ `post_immediate()` · لا تعديل
`CorrectionEventAccountingCorrection` · لا افتراض أن `reverse()` تحل
المشكلة · لا افتراض تطابق عدد JournalEntries = Economic Effects =
Deltas.

**السؤال المركزي**: إذا أنتج التحليل التاريخي مجموعة Deltas، هل نستطيع
إثبات أن كل Delta قابلة للتتبع إلى أثر محاسبي، وأن مجموع الآثار
المحاسبية يساوي الأثر الاقتصادي المحلَّل دون فقد أو ازدواج؟

---

## 1. Delta Completeness

**EVIDENCE (فحص مباشر لـ`CorrectionEventDeltaRecord`)**: الصف مفتاحه
`(candidate_state_id, movement_id)` — سطر واحد **لكل حركة داخل الـScope،
لكل candidate محدَّد** (هذا الجزء بنيوي وثابت لكلا الحقلين). **تصحيح
دقيق للمعنى (كان غامضاً في المسودة السابقة، محسوم الآن بنص C الصريح،
سطر 512 حرفياً)**: `delta_inventory_value` **movement-level دائماً** —
قيمة خاصة بتلك الحركة بعينها، غير قابلة لـNULL. `delta_quantity`
**regime-segment-level** — قيمة **ثابتة لكامل الـregime** (لا تتغيّر
إلا عند الجذر أو عبور Transfer، مُثبَتة حسابياً في Q3.3.4 عبر سيناريو
9 خطوات)، ومخزَّنة **مرة واحدة فقط لكل regime** (على الأرجح الحركة
الحاملة/الأولى لذلك الـregime)، بينما بقية حركات نفس الـregime تحمل
`delta_quantity=NULL` **بالتصميم، لا كنقص بيانات** — دلالتها "نفس قيمة
آخر regime سابق غير NULL" (forward-fill)، لا "غير معروفة". **هذا يحسم
التناقض الظاهري المطروح**: الجدول movement-level بنيوياً (صف واحد لكل
حركة) **لكل الحقلين معاً**؛ الفرق هو أن أحد الحقلين (`quantity`) يحمل
قيمة *مكرَّرة عمداً بالتصميم* عبر عدة صفوف من نفس الـregime، بينما
الآخر (`inventory_value`) قيمة *فريدة لكل صف*. **DERIVED (invariant
مُصحَّح، مقصور على الحقل الصحيح فقط)**:
```
لكل (candidate_state_id, movement_id): صف DeltaRecord واحد بالضبط.
  delta_inventory_value: قيمة فريدة لتلك الحركة (NOT NULL دائماً).
  delta_quantity: قد تكون NULL شرعاً لأي حركة ليست حاملة regime جديد
  — هذا ليس عيب اكتمال، فقط delta_inventory_value يخضع لقلق الازدواج.
```

**⚠️ اكتشاف غياب `UniqueConstraint` — صياغة مصحَّحة (أضعف مما كُتِب
سابقاً، كما طُلِب)**: لا يوجد أي `UniqueConstraint` على
`(candidate_state_id, movement_id)` في `CorrectionEventDeltaRecord` —
خلافاً لـ`CorrectionEventImpactScopeElement` المجاورة التي تحمل
`uq_scope_element_no_duplicate` صراحة. **هذا يعني بنيوياً فقط أن
uniqueness لا يمكن افتراضها من الـschema وحده** — **لا** أن الأثر
"سيُحسَب مرتين" حتماً؛ ذلك **يعتمد كلياً على تنفيذ طبقة التجميع
المستقبلية**: *"يمكن أن يُحسب الأثر مرتين إذا قامت طبقة التجميع بافتراض
uniqueness ولم تتحقق منه صراحة"* — لا أكثر من هذا كتوصيف حالي.

**السؤال المطروح (هل توجد Delta محسوبة لا تجد طريقاً لـAccountingCorrection؟)**:
**غير قابل للاختبار اليوم** — لا خدمة تُنشئ `DeltaRecord` فعلياً بعد
(NOT IMPLEMENTED YET، مؤكَّد G-002/G-003). الجواب البنيوي الوحيد الممكن
الآن: **لا شيء في الـschema يربط `DeltaRecord` بأي `AccountingCorrection`
مباشرة** (لا FK بينهما إطلاقاً) — الربط الوحيد الموجود هو
`AccountingCorrection.correction_event_id`، على مستوى الحدث بأكمله، لا
الـDelta. **⚠️ دليل أقوى مما ذُكِر سابقاً، من C نفسها حرفياً (سطر 515،
لم يُستشهَد به في مسودة G-003/G-004 السابقة)**: C تصف `AccountingCorrection`
بأنها *"connecting a CorrectionEvent (**and the specific `DeltaRecord`s
it covers**) to the actual JournalEntry"* — أي أن **نية C الأصلية كانت
فعلاً ربطاً على مستوى الـDelta تحديداً**، والـschema الفعلي المُنفَّذ
(3 أعمدة فقط) **لم يُطبِّق هذا الجزء من النية بعد**. هذا **NOT
IMPLEMENTED YET أدق مما كان مصنَّفاً سابقاً في G-003** — ليس فجوة
افتراضية، بل نص C صريح لم يُترجَم بعد لعمود/جدول فعلي. إذن: **DeltaRecord
"يتيمة" ممكنة بنيوياً اليوم**، وهذا **يناقض نية C المذكورة حرفياً**، لا
مجرد احتمال نظري مفتوح.

## 2. Economic-Effect Completeness

**DERIVED من G-003 §4/§6 مباشرة**: `Inventory effect` = مشتق من
`delta_inventory_value` مباشرة (إشارة الرقم). `COGS effect` **غير مخزَّن
كحقل مستقل** — يُشتَق لاحقاً عبر معادلة الهوية المحاسبية (`Δinputs =
ΔCOGS + Δending inventory value`).

**⚠️ CRITICAL OPEN MATHEMATICAL QUESTION — تتبُّع مسار اشتقاق مُمكِن من
الأدلة الموجودة فعلاً (لا معادلة مخترَعة، كما طُلِب صراحة)**: فحصت
`accumulate_average_cost()` (المصدر الوحيد المُعتمَد للأساس الرياضي،
Q3.3.4) حرفياً:
```
لكل حركة IN:  total_qty += qty ;  total_cost += qty × unit_cost
لكل حركة OUT: total_qty -= qty ;  total_cost -= qty × unit_cost
```
هذا **يقترح مساراً معقولاً غير مؤكَّد** لاشتقاق `Δinputs`: تشغيل نفس
هذه الدالة (أو نمطها الحسابي) مرتين — مرة على تسلسل الحركات الأساسي
(Baseline) ومرة على تسلسل الـCandidate المصحَّح — ثم أخذ الفرق. عندها
**تصبح `Δinputs` = الفرق بين تراكمَي `total_cost` لحركات **IN** فقط**
(حصة "المُدخَلات" الفعلية للنظام)، بينما **`ΔCOGS`** يُشتَق من نفس
الفرق لكن على حركات **OUT** التي تمثّل بيعاً فعلياً — **باستثناء
صريح** لحركات OUT الناتجة عن Transfer (انظر التحذير أدناه، مرتبط
مباشرة بـ§6).

**هذا ليس إثباتاً — ثلاث فجوات حقيقية يجب حسمها قبل اعتماد هذا المسار**:
1. **الدالة نفسها لا تُميّز `source_type` عند طرح قيمة OUT** — تطرح
   `qty×unit_cost` لأي خروج بلا تمييز بين بيع فعلي (→ يجب أن يُحسَب
   COGS) وخروج Transfer (→ ليس مصروفاً، مجرد نقل موقع؛ لا يجوز اعتباره
   ΔCOGS). **استخدام الدالة كما هي حرفياً لهذا الغرض سيخلط الاثنين خطأً**
   — هذا اكتشاف جديد يربط §2 بـ§6 مباشرة، لم يُذكَر سابقاً.
2. **تفاعل مباشر مع تحذير G-002-C السابق**: الدالة تُصفِّر
   `average_cost`/`inventory_value` عند `total_qty<=0` — لو استُخدِم
   هذا المسار لحساب `Δinputs` عند نقطة تاريخية يمر فيها الرصيد التراكمي
   بالصفر/السالب (بالضبط الحالات التي أثبت F أنها **يجب ألا توقِف
   الانتشار**)، فقد تُصفَّر قيمة عند تلك النقطة تحديداً بأثر مشابه
   لمشكلة `total_qty` في طبقة أخرى تماماً. **لم يُحلّ بعد — يحتاج قراراً
   صريحاً منفصلاً عن مجرد "إعادة استخدام الدالة كما هي".**
3. **قيمة OUT تُستخدَم بتكلفتها **المخزَّنة على الحركة نفسها** (`m.unit_cost`)،
   لا بمتوسط مُعاد حسابه** (قاعدة §39 صريحة في الدالة نفسها) — أي أن
   تصحيح تاريخي يغيّر متوسط التكلفة عند نقطة سابقة **لا يُعيد تلقائياً
   حساب** تكلفة أي OUT لاحقة استخدمت متوسطاً قديماً؛ إعادة حساب ذلك (لو
   لزم) **تحتاج منطقاً إضافياً غير موجود في هذه الدالة حالياً** — سؤال
   مفتوح مستقل.

**ما لا يزال يحتاج حسماً (لا يُخترَع هنا)**: كيف يتعامل هذا المسار مع
transfers (فجوة 1 أعلاه)، مشتريات متعددة بنفس التاريخ (يُحلّ رياضياً
بذاته — الجمع تجميعي، لا يتأثر بالترتيب الداخلي لمجموعة IN-IN أو
OUT-OUT، متسق تماماً مع إثبات Q2.5 نفسه)، رصيد صفري/سالب (فجوة 2 أعلاه)،
ومجموعات نفس التاريخ (تُحلّ عبر نفس منطق Q2.5). **يبقى CRITICAL OPEN
MATHEMATICAL QUESTION بتصنيف صريح**، لا OPEN QUESTION عادية — يمسّ
مباشرة قدرتنا على إثبات completeness لأي حالة Purchase→Sale.

**سلسلة Purchase → Sale (فقدان أحد الأثرين)**: **غير قابل للاختبار
اليوم** لنفس السبب (لا خدمة حساب فعلية) — لكن الـschema **لا يوجد فيه
أي حقل "نوع الأثر"** يُميّز صف Delta كـ"Inventory-only" أم "COGS-only"
أم "كليهما" — الاشتقاق بالكامل مسؤولية طبقة لم تُبنَ. **RISK موثَّق، لا
مُثبَت**: غياب حقل تصنيف الأثر يعني أن أي خطأ مستقبلي في منطق الاشتقاق
لن يُكتشَف بفحص schema، فقط باختبار سلوكي لاحق.

**عبور Transfer**: راجع §6 أدناه (قسم مستقل مخصَّص كما طُلِب).

## 3. One-to-One / One-to-Many Mapping

**EVIDENCE من G-003 (مُعاد التوكيد هنا لصلته المباشرة)**: `AccountingCorrection`
تسمح بنيوياً بـ`one CorrectionEvent → multiple AccountingCorrections`
(لا UNIQUE على `correction_event_id`)، و`one JournalEntry → one
CorrectionEvent` (UNIQUE على `journal_entry_id`). **لا رابط FK واحد بين
`DeltaRecord` و`AccountingCorrection`** — أي سؤال cardinality بينهما
(Delta واحدة→JournalEntry واحد؟ Delta واحدة→عدة؟ عدة Deltas→JournalEntry
واحد؟) **غير قابل للإثبات من الـschema الحالي لأن لا علاقة موجودة
أصلاً بينهما ليُفحَص اتجاهها**. هذا **DESIGN CHOICE بالكامل، غير محسوم**
— لا الـschema يفرضه ولا أي مستند سابق يحسمه.

**ما يمكن إثباته فعلاً من البنية الحالية**: أن `JournalEntry` الواحد
يستطيع احتواء عدد غير محدود من `JournalLine` (مؤكَّد من كل مسارات
posting.py/returns.py الفعلية) — أي أن "عدة آثار اقتصادية في قيد واحد"
**ممكن تقنياً بلا أي تعديل schema**، بصرف النظر عن أي قرار cardinality
لاحق بين Delta وAccountingCorrection تحديداً.

## 4. Conservation / Reconciliation

**المطلوب**: invariant من الشكل `Σ accounting economic effects = Σ
analyzed economic deltas`.

**DERIVED (لا EVIDENCE — لا كود لفحصه)**: بما أن `AccountingCorrection`
لا تخزّن مبلغاً (§3 من G-003، مؤكَّد بقراءة مباشرة: 3 أعمدة فقط)، فإن
أي "مجموع" للآثار المحاسبية **يجب أن يُشتَق من `JournalLine.debit`/
`credit` للقيود المرتبطة**، لا من `AccountingCorrection` نفسها. هذا يعني
عملياً أن reconciliation المطلوبة تحتاج **join** عبر ثلاث جداول
(`DeltaRecord` → `CandidateState` → `CorrectionEvent` ← `AccountingCorrection`
← `JournalEntry` ← `JournalLine`) — **لا صف واحد يحمل الرقمين معاً
لمقارنة مباشرة اليوم**. هذا **ليس عيباً في G-003** (لم يكن مطلوباً
هناك) — هو **متطلب تصميم صريح لـG-004/Implementation**: يجب أن تُبنى
دالة/استعلام reconciliation صراحة، لا افتراض أنها "ستنجح تلقائياً" لمجرد
وجود القيود.

**القاعدة المقترَحة التي طرحتَها (`لا نعتبر وجود JournalEntry دليلاً
على الاكتمال`)**: **مؤكَّدة الآن بدليل بنيوي مباشر**، لا رأياً عاماً
فقط — لأن `AccountingCorrection` لا تحمل أي مبلغ يمكن مقارنته، فمجرد
وجود صف `AccountingCorrection` **لا يثبت شيئاً عن القيمة** المُرحَّلة
فعلياً مقابل المُحلَّلة. الإثبات يحتاج قراءة `JournalLine` صراحة في كل
مرة — لا اختصار.

## 5. Zero-Effect Deltas

**بحثت عن أي معالجة خاصة لحالة "كمية متغيرة لكن قيمة أثر صفرية" في
الكود القائم**: لا يوجد كود G فعلي لفحصه (غير منفَّذ). **لكن** يوجد
سابقة ذات صلة يمكن الاستدلال منها بحذر: `accumulate_average_cost()`
(الحالة الحالية) تُصفِّر `inventory_value`/`average_cost` عند
`total_qty<=0` — **مثال قائم فعلاً** على "كمية تغيّرت، قيمة مُبلَّغة قد
تكون صفراً" في سياق مختلف (الحالة الحالية، لا Delta التاريخي). **DERIVED
بحذر شديد، لا استنتاج مباشر**: هذا **لا يثبت** ماذا يجب أن يحدث لـDelta
تاريخي بقيمة صفرية — فقط يُظهر أن "قيمة صفرية رغم تغيّر الكمية" ليست
مفهوماً غريباً عن النظام. **يبقى OPEN QUESTION صريح كما طلبت التعليمات،
لا افتراضاً**: هل `delta_quantity≠0` مع `delta_inventory_value==0`
تحتاج `JournalEntry` (بقيمة صفرية، عديم الفائدة محاسبياً وربما مرفوض
أصلاً بواسطة `is_balanced()`/`_validate_lines()` التي تفرض "لازم مبلغ
مدين أو دائن — لا سطر فارغ")، أم تبقى نتيجة تحليلية موثَّقة (Delta) بلا
ترحيل؟ **ملاحظة بنيوية دقيقة**: `_validate_lines()` في `journal_edit.py`
(مؤكَّدة بقراءة مباشرة سابقاً) **ترفض صراحة سطراً بمبلغ صفر بالمدين
والدائن معاً** — أي لو حاول تصحيح مستقبلي ترحيل Delta بقيمة صفرية عبر
`post_immediate()` القائمة، **سيُرفَض بنيوياً بالفعل** بالتحقق الحالي.
هذا **EVIDENCE حقيقي** يميل الكفة نحو "لا تُرحَّل الـDeltas الصفرية
القيمة"، لكنه **لا يحسم** السؤال نهائياً (قد يُصمَّم مسار مختلف عمداً).
**الصياغة الدقيقة المعتمَدة الآن (بدل الحكم المباشر أعلاه)**: *Zero-value
economic effect may be analytically represented but is not currently
representable as a normal zero-amount JournalLine through the existing
posting validation.* يبقى القرار مفتوحاً: هل Delta صفرية القيمة تُعتبر
"complete analytical result with no accounting posting"، أم توجد حالة
اقتصادية أخرى يجب تمثيلها؟ — **OPEN POLICY / SEMANTIC QUESTION**، يُحسَم
قبل Implementation لا هنا.

## 6. Transfers

**EVIDENCE مؤكَّدة سابقاً (G-002/G-003)**: لا `JournalEntry` لـTransfer
نفسه إطلاقاً. `DeltaRecord` مرتبطة بـ`movement_id` مباشرة (FK)،
و`InventoryMovement` نفسها تحمل `warehouse_id` صريحاً.

**تصحيح تصنيف مهم (كان أقوى مما يثبته الدليل)**: الصياغة السابقة
("لن يضيع الأثر... CONFORMING") **تجاوزت ما تثبته الـschema فعلياً**.
**الدقيق**: الـschema يثبت فقط أن `DeltaRecord.movement_id` **يمكنها**
الإشارة لأي حركة، في W1 أو W2 على حد سواء — **هذا لا يثبت** أن كل حركة
متأثرة فعلياً في كلا المستودعين **ستحصل فعلياً** على `DeltaRecord` عند
التنفيذ. ذلك **يعتمد كلياً على الخدمة المستقبلية** (هل ستُنشئ فعلاً
Delta لكل حركة متأثرة في الطرفين، أم قد تُغفِل أحدهما سهواً؟) — وهذا
بالضبط سؤال الـcompleteness الذي جاء G-004 لحسمه، لا نتيجة يمكن استخلاصها
من الـschema وحده. **التصنيف المصحَّح: STRUCTURALLY CAPABLE — COMPLETENESS
NOT YET PROVEN** (لا `CONFORMING`).

## 7. Overlapping CorrectionEvents

**تطبيق مباشر لما أثبته G-002/G-003، لا سياسة جديدة**: بما أن
`AccountingCorrection` لا رابط لها بمستوى Delta (§1/§3)، وبما أن Overlap
Policy **تبقى OPEN IN C** (مؤكَّد مرتين سابقاً) — فإن **خطر الترحيل
المزدوج ممكن بنيوياً**: لو `Scope(A) ∩ Scope(B) ≠ ∅` وكلا الحدثين وصل
لمرحلة ترحيل مستقلة، لا قيد schema يمنع كليهما من إنتاج
`AccountingCorrection` منفصلة لنفس الحركة المتقاطعة.

**تصحيح صياغة دقيق**: وجود `Event A → AccountingCorrection A` و`Event
B → AccountingCorrection B` **لا يثبت** أن نفس الأثر سيُرحَّل مرتين
فعلياً — **يثبت فقط أن الـschema لا يمنع ذلك**. **التصنيف المصحَّح**:
**DOUBLE-POSTING IS STRUCTURALLY POSSIBLE; ACTUAL DOUBLE-POSTING IS NOT
PROVEN** (لا "خطر حقيقي" بصيغة الجزم). هذا امتداد مباشر لـ§14 في G-003،
لا اكتشاف منفصل، لكنه الآن مؤكَّد أيضاً من زاوية Delta-level لا
AccountingCorrection-level فقط.

## 8. Traceability

السلسلة الكاملة المطلوبة الآن (أطول من G-003 بخطوة `AccountingEffect`
الوسيطة):
`CorrectionEvent → ImpactScopeElement → HistoricalCandidate → Delta →
AccountingEffect → AccountingCorrection → JournalEntry → JournalLine`.

- `CorrectionEvent → ImpactScopeElement`: **موجود، مبني** (F).
- `→ HistoricalCandidate (CandidateState)`: schema موجود، **NOT
  IMPLEMENTED YET**.
- `→ Delta (DeltaRecord)`: schema موجود، مرتبط بـ`candidate_state_id`
  FK فعلي — **الرابط البنيوي موجود**، الإنشاء الفعلي **NOT IMPLEMENTED
  YET**.
- `Delta → AccountingEffect`: **لا وجود لهذا كمفهوم schema مستقل على
  الإطلاق** — `ImpactScopeElementKind.ACCOUNTING_EFFECT` موجودة كنوع
  عقدة في رسم Scope (Q2.6)، لكنها **ليست نفس الشيء** بالضرورة. **صياغة
  صريحة معتمَدة الآن**: *"Accounting Effect" is currently a conceptual
  term unless and until a concrete calculation/service/schema
  representation is discovered or designed.* اسم enum **لا يثبت** وجود
  طبقة اقتصادية مستقلة (domain object / persisted record / intermediate
  calculation) — يجب عدم الخلط.
- `AccountingEffect/Delta → AccountingCorrection`: **الفجوة الأهم،
  الآن مؤكَّدة بنص C صريح لا استنتاجاً فقط (§1 أعلاه، سطر 515)**: C
  تصف `AccountingCorrection` بأنها تربط الحدث **و"الـDeltaRecords
  المحدَّدة التي تغطّيها"** — والـschema الفعلي لم يُطبِّق هذا الجزء بعد.
  ليست فجوة افتراضية؛ نية C الموثَّقة نفسها لم تُترجَم لعمود/جدول بعد.
- `AccountingCorrection → JournalEntry → JournalLine`: **موجود بنيوياً
  بالكامل** (FK صريح + JournalLine مرتبطة بـJournalEntry أصلاً).

**السؤال الذي تركه G-003 عمداً (مُعاد طرحه هنا، بصياغة مصحَّحة غير
مطلقة كما طُلِب)**: **ليس** "الربط على مستوى Event غير كافٍ رياضياً"
بإطلاق. الأدق: **الربط على مستوى Event قد يكون كافياً لإثبات
event-level reconciliation** (`Σ posted لكل الحدث = Σ analyzed لكل
الحدث`، بشرط وجود آلية reconciliation حتمية تربط المجموعين بشكل صحيح)،
**لكنه غير كافٍ لإثبات per-Delta/per-effect traceability** (تتبُّع أي
جزء محدَّد من التحليل إلى القيد الذي مثَّله تحديداً). هذان مستويان
مختلفان من الإثبات، لا مستوى واحد — **وهذا الفرق يميل، بنص C الصريح في
§1 أعلاه، نحو أن الإجابة النهائية المطلوبة فعلياً هي per-Delta**، لا
event-level فقط، رغم أن هذا **لا يزال DESIGN CHOICE يُحسَم عند
Implementation، لا هنا.**

## 9. Partial Failure / Atomicity

**ACTION REQUIRED مُنجَز — أثبتُّ transaction ownership فعلياً من الكود،
لا من استنتاج G-001 وحده كما طُلِب صراحة**: فحصت `post_immediate()` و
`_reserve_ref_no()` (المستدعاة داخلها كأول خطوة) في `journal_edit.py`
مباشرة:

- **`_reserve_ref_no()` تلتزم بـ`commit` مستقل فوراً، دائماً، بالتصميم
  الصريح** — تعليق الكود نفسه: *"بـTransaction مستقلة تماماً عن جلسة
  المستند (§1/§4 — قرار تصميمي ملزم، لا تفصيل مؤجَّل)"*. تُستخدَم اتصال
  `sqlite3` خام منفصل (`BEGIN IMMEDIATE` → `commit` فوري → إغلاق) خاص
  بحجز الرقم التسلسلي وحده، **بمعزل تام** عن أي transaction خارجية يديرها
  المستدعي — هذا **دائماً كذلك، بلا استثناء**، حتى لو أراد المستدعي
  تجميع عدة `post_immediate()` ضمن transaction واحدة منطقياً.
- **أما القيد (`JournalEntry`+`JournalLine`) نفسه**: `post_immediate()`
  تنفّذ `session.flush()` متعددة لكن **لا تستدعي `session.commit()` على
  الإطلاق بنفسها** — الالتزام النهائي **مسؤولية المستدعي** (تماماً كما
  في `post_sales_invoice`، التي أيضاً لا تُنهي بـcommit خاص بها).

**النتيجة الدقيقة (الحالة A مقابل B التي طرحتَها، محسومة الآن بدليل لا
افتراض)**:
- **حجز الأرقام التسلسلية: لا تُبنى ذرّية معها إطلاقاً بأي حال** —
  مُنفصلة عمداً دوماً، حتى لو التُزِم بأفضل الممارسات في البقية. هذا
  "قيد مقبول بالتصميم" موثَّق سلفاً بمكان آخر من المشروع (فجوة رقم غير
  مُستَرجَع عند فشل لاحق).
- **القيد والأسطر نفسها: تحقيق الذرّية ممكن فعلاً** (الحالة A) — **بشرط**
  أن يمتنع المستدعي عمداً عن استدعاء `commit()` بين كل `post_immediate()`
  ويؤجّله لنهاية كل عمليات تصحيح الحدث الواحد دفعة واحدة. **لا شيء في
  البنية الحالية يفرض هذا الانضباط تلقائياً** — الحالة B (commit منفصل
  لكل قيد) ممكنة بنفس السهولة إن لم يُصمَّم الاستدعاء المستقبلي بعناية.

**التصنيف يبقى CONDITIONALLY SUPPORTED**، لكن الآن **بدليل تنفيذي دقيق
لموضع الشرط بالضبط** (تأجيل الـcommit للنهاية)، لا مجرد نمط عام مُستنتَج
من G-001.

## 10. Reconciliation بعد الترحيل

**تلخيص وربط لكل ما سبق**: لا يوجد اليوم أي استعلام/دالة/حقل يُثبِت
`Σ posted = Σ analyzed` مباشرة. الإثبات يتطلب (كما في §4) عبوراً صريحاً
عبر عدة جداول، **ولا يوجد ضمان بنيوي من الـschema وحده** أن هذا العبور
سيُعطي دائماً نتيجة متطابقة — ذلك يعتمد كلياً على انضباط طبقة الخدمة
المستقبلية (احترام قيد الذرّية §9، عدم ازدواج §1/§7، ربط كافٍ §8). **لا
"دليل وجود قيد" يكفي وحده أبداً** — هذا هو المبدأء المركزي الذي يجب أن
يحكم أي Implementation لاحق.

---

## ملخص التصنيفات (مصحَّح بالكامل وفق المراجعة)

| المحور | التصنيف |
|---|---|
| بنية DeltaRecord (movement-level للقيمة، regime-level للكمية — سطر C 512) | EVIDENCE |
| غياب UNIQUE على (candidate_state_id, movement_id) | EVIDENCE — ازدواج **ممكن إن لم تتحقق طبقة التجميع صراحة**، لا حتمي |
| اشتقاق Δinputs لحساب COGS effect | **CRITICAL OPEN MATHEMATICAL QUESTION** — مسار مُقتَرَح من `accumulate_average_cost()` نفسها، 3 فجوات محدَّدة (transfer-OUT، zero/negative clamping، إعادة تقييم OUT سابقة) |
| Cardinality Delta↔AccountingCorrection | DESIGN CHOICE — لكن نية C (سطر 515) تميل نحو ربط صريح على مستوى Delta |
| Σ posted = Σ analyzed يحتاج join متعدد الجداول | DERIVED |
| "وجود JournalEntry لا يثبت الاكتمال" | EVIDENCE |
| Zero-effect delta | EVIDENCE + **OPEN POLICY / SEMANTIC QUESTION** (صياغة مصحَّحة، §5) |
| Transfer وDeltaRecord | **STRUCTURALLY CAPABLE — COMPLETENESS NOT YET PROVEN** (لا CONFORMING) |
| Overlap → ازدواج ترحيل | **STRUCTURALLY POSSIBLE; ACTUAL DOUBLE-POSTING NOT PROVEN** (لا "خطر مؤكَّد") |
| Event-level linking كفاية | **يكفي لـevent-level reconciliation، لا يكفي لـper-Delta traceability** — تمييز، لا رفض مطلق |
| ACCOUNTING_EFFECT | مصطلح مفاهيمي فقط حتى تصميم/اكتشاف تمثيل فعلي — لا افتراض طبقة مستقلة |
| Atomicity | CONDITIONALLY SUPPORTED — **الشرط مُثبَت بدقة الآن**: تأجيل commit للنهاية؛ حجز الأرقام التسلسلية غير ذرّي دوماً بالتصميم |

## Exit Gate

- كل محور من العشرة المطلوبة فُحِص، وكل نقاط ACTION REQUIRED من المراجعة
  السابقة عولجت بدليل مباشر (§1 الدلالة، §2 مسار Δinputs، §6/§7/§8
  الصياغات المصحَّحة، §9 إثبات transaction ownership). ✅
- لا اختيار Option A/B/C/D. ✅
- لا تعديل schema (لا إضافة many-to-many رغم ظهور الحاجة لها في §1/§8 —
  موثَّقة كفجوة مدعومة الآن بنص C صريح، غير مُصلَحة). ✅
- لا production change. ✅
- غياب `UniqueConstraint` يبقى **Discovery finding مسجَّل، لا تعديل
  schema** — كما اشتُرِط صراحة، لأن (candidate_state_id, movement_id)
  لم يُثبَت بعد أنه المفتاح الدلالي الكامل الصحيح بمعزل عن مسألة
  regime-level للكمية (محسومة الآن في §1، لكن الحذر يبقى مبدأً عاماً).

**النتيجة: كل نقاط ACTION REQUIRED السبع عولجت بأدلة مباشرة من الكود
ومن نص C نفسه، لا افتراضات جديدة. جاهز لحكمك النهائي على الإغلاق.**

---

## G-004 Official Closure Block (بعد اعتماد المراجعة)

```
G-004 Correction Completeness Discovery
Status: CLOSED

Production Changes: NONE
Schema Changes: NONE
Migration: NONE

Discovery conclusion:
- DeltaRecord semantics are characterized (movement-level for value,
  regime-level for quantity — encoding, not a defect).
- Missing Delta-level AccountingCorrection linkage is confirmed
  against prior C design intent (C §515, verbatim).
- Δinputs / ΔCOGS derivation remains a CRITICAL OPEN MATHEMATICAL
  QUESTION — a critical mathematical dependency for G implementation,
  not a documentation gap.
- Transfer completeness remains unproven at service level
  (STRUCTURALLY CAPABLE — COMPLETENESS NOT PROVEN).
- Overlap double-posting is structurally possible but not proven.
- Event-level reconciliation and per-Delta traceability are
  explicitly distinguished as two different proof strengths.
- Accounting atomicity is conditionally supported, with the exact
  commit boundary identified in code (_reserve_ref_no always commits
  independently; entry/lines atomicity depends on the caller
  deferring commit to the end of one CorrectionEvent's full posting).
- No implementation or schema decision is authorized by G-004.
```

**البوابات الموروثة لأي عمل تالٍ (لا حل هنا، تسجيل فقط)**: Δinputs/
ΔCOGS mathematics (CRITICAL) · Delta→AccountingCorrection linkage
(DESIGN) · zero-effect policy (DESIGN) · overlap policy (موروث OPEN
من C) · آلية reconciliation (DESIGN) · حدود transaction الذرّية
(implementation constraint، الشرط مُثبَت بالكود لا مفترَض).

**ستة ممنوعات صريحة على أي عمل G تالٍ** (مُسجَّلة هنا حتى لا تُنسى):
لا اعتبار `total_qty<=0` نقطة توقف عامة (مغلق منذ FD-001 REV1) · لا
إعادة استخدام `accumulate_average_cost()` حرفياً لحساب ΔCOGS دون حل
الأسئلة الثلاثة (§2) · لا اعتبار وجود `AccountingCorrection`+`JournalEntry`
دليل اكتمال بذاته · لا اعتبار Transfer OUT = COGS · لا اعتبار FK على
مستوى Event كافياً تلقائياً لـper-Delta traceability · لا إضافة
many-to-many/UNIQUE/AccountingEffect model لمجرد ظهور الفجوة — كل واحدة
تحتاج Design Decision مستقلة.

