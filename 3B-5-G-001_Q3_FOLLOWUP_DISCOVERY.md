# 3B-5-G-001_Q3_FOLLOWUP_DISCOVERY.md

Discovery فقط. لا WAL، لا transaction isolation، لا index، لا locking، لا
QThread، لا chunk size، لا scope limitation، لا pruning، لا overlap
policy جديدة — أي من هذه لم يُقرَّر في هذا المستند.

## Q3-A — Revalidation / Acceptance Race

**بحثت مباشرة**: لا توجد أي دالة `accept`/`approve`/`commit_candidate`/
`finalize_candidate` في كامل الكود (بحث صريح، صفر نتائج). `PENDING` و
`APPROVED` موجودتان في `CorrectionEventStatus` enum **كقيم فقط**، بلا أي
منطق انتقال بينهما في أي مكان.

**الإجابة الدقيقة على السؤال المطروح**: لا يمكن تحديد نقاط (1) بداية
التحليل، (2) نهاية الحساب، (3) revalidation، (4) persistence النهائية —
**لأن هذا التدفق غير موجود بعد بالكامل**. السؤال "هل يمكن لكتابة مؤثرة
أن تقع بين (3) و(4)؟" **لا ينطبق حالياً** — ليس لأن الإجابة "لا" (آمن)،
بل لأنه لا يوجد كود بعد لفحصه إطلاقاً.

**التصنيف المصحَّح (أدق من "NOT PROVEN" وحدها)** — تفكيك لأربع طبقات
منفصلة بدل تصنيف واحد:
- المبدأ العام (revalidation-before-commit + append-only stale
  analysis): **DECIDED** (من C).
- التطبيق الحالي: **NOT IMPLEMENTED** (صفر كود، مؤكَّد بالبحث).
- الـcorrectness invariant المطلوب عند البناء: **MUST BE PROVEN** حين
  يُبنى التدفق فعلياً — ليس افتراضاً تلقائياً بأن المبدأ العام يكفي وحده.
- نافذة السباق نفسها (هل كتابة مؤثرة يمكن أن تقع بين آخر revalidation
  ولحظة persistence؟): **OPEN DESIGN DETAIL** — هذا الجزء تحديداً غير
  محسوم حتى كمبدأ، لا فقط غير مُنفَّذ.

## Q3-B — Modification / Reversal داخل Scope

**بحثت في الكود مباشرة**: بحثت عن أي تعديل حقلي (`.quantity =`,
`.unit_cost =`, `.movement_date =`) على `InventoryMovement` بعد إنشائه
في كل خدمات التطبيق — **صفر نتائج**. فحصت `invoice_cancel.py` تحديداً
(المسار الوحيد لعكس فاتورة مرحَّلة): **الحركات الأصلية "لا تُحذف ولا
تُعدَّل — فقط status→CANCELLED، وأثر عكسي منفصل"** (تعليق صريح في الكود
نفسه). العكس يُبنى بحركة **جديدة** (اتجاه معاكس، نفس الكمية ونفس
`unit_cost` الأصلي حرفياً، بتاريخ `cancel_date`) — لا تعديل على السطر
القديم بأي شكل. نفس القاعدة مؤكَّدة في `journal_edit.py` صراحة: "قيد
POSTED لا يُعدَّل ولا تُحذف أسطره مباشرة".

**تأكيد إضافي من C نفسها (Q3.1)**، غير معتمِد على قراءتي وحدها: *"a
correction to an existing (wrong, not merely forgotten) historical
document will take the shape of a linked reversal + re-entry pair...
per the system's existing invariant that a POSTED document is never
edited in place"* — مع إشارة صريحة لمصدر هذا التأكيد الأصلي:
`invoice_edit.py::ensure_editable()`. تحققت أن هذه الدالة موجودة فعلاً
بالكود (ليست استشهاداً وهمياً).

| الحالة | التصنيف |
|---|---|
| تعديل quantity على حركة POSTED قائمة | **DECIDED/PROVEN — مستحيل بنيوياً**، لا مسار كود يفعل هذا |
| تعديل unit_cost على حركة POSTED قائمة | **DECIDED/PROVEN — نفس السبب** |
| تعديل movement_date على حركة POSTED قائمة | **DECIDED/PROVEN — نفس السبب** |
| reversal/cancellation | **DECIDED/PROVEN — لكنها ليست "تعديلاً"؛ تُختزَل بنيوياً لحالة "إضافة حركة جديدة"** المغطاة أصلاً بمبدأ الـstaleness المحسوم سابقاً |
| حذف فعلي لحركة POSTED | **DECIDED/PROVEN — مستحيل بنيوياً**، نفس القاعدة |

**النتيجة**: هذه الفجوة التي بدت "غير مغطاة بمثال صريح" في التقرير
السابق **تُحَل بالكامل** — ليس لأن نص staleness العام يغطيها لغوياً، بل
لأن **الحالات الأربع الأخرى (تعديل/حذف) غير ممكنة الحدوث أصلاً في هذا
النظام**، فلا تحتاج تغطية منفصلة. الحالة الوحيدة الممكنة فعلياً
(reversal) هي بالضبط الحالة المغطاة أصلاً. **لا فجوة متبقية هنا.**

## Q3-C — Overlapping Correction Events

**بحثت مباشرة**: صفر كود لأي intersection/overlap detection في
`correction_scope.py` أو `correction_detection.py`.

- هل يمكن أن تتداخل Scopes؟ **DECIDED (بنيوياً، لا آلية بعد)**: نعم —
  Q3.2 بند 9 تنص صراحة أن بنية الـImpact Scope مصمَّمة بحيث
  `Scope(CE-A) ∩ Scope(CE-B)` **قابل للحساب عند الطلب** — متطلب بنيوي
  مقصود، لا احتمال عرضي.
- هل تبقى الأحداث مستقلة؟ **لا إجابة محسومة** — البنية تسمح بالتقاطع دون
  افتراض استقلالية.
- هل توجد آلية حالية لاكتشاف أن Event-A تأثّر بعمل Event-B؟ **DISPROVEN
  — لا يوجد، صفر تطبيق فعلي** (تحقَّق بالبحث المباشر، لا افتراضاً).
- هل policy التعامل مع overlap موجودة في C أم OPEN؟ **OPEN صراحة في C
  نفسها** — نص حرفي: *"the policy for handling a detected overlap
  remains undecided, per Q3.1"*. هذا ليس اكتشافاً جديداً منا؛ نؤكده فقط.
- **توضيح صريح**: هذا **لا يمنع** الاستمرار في دراسة Execution Model.
  `Overlap policy = OPEN` ≠ `Execution model cannot be investigated`.
  الصياغة الصحيحة: سياسة التعامل مع التقاطع خارج نطاق قرار Execution
  Model حتى تُحسَم دلالياً؛ لكن البنية التحتية للتنفيذ يجب ألا تفترض أن
  CorrectionEvents منفصلة بالضرورة (`Event A ∩ Event B = ∅`) — لا حل
  الآن، لكن لا افتراض ضمني بعدم التقاطع أيضاً.

## Q3-D — Pending Approval / Staleness Churn

| الفئة | المحتوى |
|---|---|
| **Evidence** | C تحتوي فقرة "flagged concern, not resolved" تربط صراحة بين نمو Scope غير المحدود واحتمال دخول staleness متكرر. قياساتنا (`measure_actual_scope_candidates.py`) أثبتت رقمياً أن نفس القاعدة تنتج Scope من 4 إلى 1394 حركة حسب موضع الجذر فقط — تباين حقيقي مقاس، لا افتراضي. |
| **Derived concern** | إن كانت مساحة الـstaleness (كل ما هو "قابل للوصول داخل Scope مسجَّل") كبيرة، فاحتمال دخول حركة جديدة ضمنها بين لحظة التحليل ولحظة مراجعة محاسب بشري يرتفع تبعاً لذلك — استنتاج منطقي من حجم Scope، لا قياس مباشر لمعدل حدوثه. |
| **Unknown** | لا قياس إنتاجي حقيقي لمعدل دخول STALE فعلياً — لأن `STALE` غير مُفعَّلة في أي منطق أصلاً (نفس نمط WITHDRAWN). لا توجد بيانات لإثبات "كم مرة" هذا سيحدث لعميل حقيقي. |
| **Product question (لك أنت تحديداً، لا سؤال كود)** | هل يجب أن يتمكن المستخدم من اعتماد تصحيح معلَّق حتى بعد فترة طويلة (يتطلب حلاً للـSTALE-churn)، أم أن دخول STALE وإعادة التحليل تلقائياً أمر مقبول تشغيلياً؟ لا يُحوَّل هذا لقرار تقني هنا. |

---

## جدول الإغلاق المطلوب

| Question | Status | Evidence | Remaining uncertainty |
|---|---|---|---|
| Revalidation race | **NOT PROVEN** (no implementation exists yet, not "proven safe") | صفر دالة accept/approve/commit في الكود؛ PENDING/APPROVED قيم enum بلا منطق | نافذة السباق نفسها متطلب تصميم يجب إثباته صراحة عند أول تطبيق للتدفق |
| Modification inside Scope | **DECIDED/PROVEN — مستحيل بنيوياً** | صفر تعديل حقلي على InventoryMovement في كل الكود؛ تعليق صريح "لا تُعدَّل" في invoice_cancel.py وjournal_edit.py؛ مؤكَّد أيضاً في Q3.1/C عبر ensure_editable() (تحقَّقت من وجودها فعلياً) | لا شيء متبقٍ |
| Reversal/cancellation | **DECIDED/PROVEN — يُختزَل لحالة "إضافة"، مغطاة أصلاً** | invoice_cancel.py: حركة جديدة معاكسة، لا تعديل على الأصل | لا شيء متبقٍ |
| Overlapping Events | **OPEN (مُسجَّل أصلاً في C، لا اكتشاف جديد)** | Q3.2 بند 9 حرفياً: التقاطع محسوب عند الطلب بنيوياً، السياسة "undecided per Q3.1"؛ صفر كود تطبيقي فعلي | متى/كيف نكتشف التقاطع فعلياً، وماذا نفعل عند اكتشافه — كلاهما غير موجود بعد حتى كسؤال تصميم مفصَّل |
| Pending Approval churn | **DERIVED CONCERN، ليس PROVEN ولا OPEN بمعنى قرار معلَّق — سؤال منتج** | فقرة C + قياسات G مباشرة (تباين حقيقي 4→1394 في scope size) | معدل الحدوث الفعلي غير مقاس؛ يحتاج إجابتك على السؤال المنتجي أعلاه |

## G-001-Q3 Status

- **موروث/محسوم من C**: تعريف Baseline، نطاق staleness (Scope-only، لا
  كل القاعدة)، مفهوم `analysis_as_of`، استراتيجية revalidation (لا
  snapshot طويل)، حالة "إضافة حركة"، وبنية overlap القابلة للحساب عند
  الطلب (بلا سياسة).
- **مُثبَت حديثاً في هذه الجولة (لا كان معروفاً من قبل)**: Modification/
  Reversal داخل Scope **محسومة بالكامل** — لا فجوة متبقية فيها إطلاقاً،
  لأن التعديل المباشر مستحيل بنيوياً في هذا النظام تحديداً.
- **يبقى OPEN فعلياً**: (1) نافذة سباق الاعتماد — لأنها لم تُبنَ بعد، لا
  لأنها غير محلولة، (2) سياسة تداخل الأحداث — مفتوحة أصلاً في C، (3)
  معدل STALE-churn الفعلي — سؤال منتج ينتظر إجابتك.
- **هل يمنع أي من هذا الانتقال لتصميم معماري؟** **لا لـModification/
  Reversal** (محسومة بالكامل، لا تحتاج شيئاً إضافياً). **نعم جزئياً
  لـRevalidation race وOverlap** — ليسا "يمنعان البدء"، لكن أي تصميم
  Execution Model (Q1/Q3 الأصليين) يتجاهلهما سيكون تصميماً غير مكتمل من
  اليوم الأول، لا نقصاً يُكتشَف لاحقاً.
