# 3B-5-G-001_DISCOVERY_CLOSED.md

**Status: DISCOVERY CLOSED**
**Production Changes: NONE** — لا تعديل واحد على `app/` منذ بداية G-001؛
كل عمل هذا الملف كان قراءة كود + اختبارات characterization في بيئة
منفصلة (synthetic/schema-level)، لا لمسة على المشروع الفعلي.

## G-001 Closure Index

| # | السؤال | التصنيف | النوع |
|---|---|---|---|
| Q1 | Execution Granularity (batch/streaming/chunked) | **OPEN DESIGN** | DEFERRED — يحتاج إثبات تكافؤ رياضي لحدود التقسيم قبل أي اختيار |
| Q2 | Candidate Space equivalence أضيق من Q2.5 | **OPEN MATHEMATICAL** | DEFERRED — سؤال رياضي بحت، لم يتغيّر بأي بحث كود |
| Q3 | SQLite / Concurrency | **PARTIALLY OPEN** | مُفصَّل أدناه — أغلبه محسوم فعلاً |
| Q4 | UI / Cancellation | **PARTIALLY OPEN** | نمط الذرّية مُثبَت من سابقة قائمة؛ التنفيذ الفعلي غير مبني |
| Q5 | Resumability | **PARTIALLY OPEN** | مُفصَّل أدناه — تصنيف مركَّب دقيق، لا حتمية عامة |

### تفصيل Q3 (لا يُعاد فتحه — للسياق فقط)
- Baseline / نطاق staleness (Scope-only) / مفهوم `analysis_as_of` /
  استراتيجية revalidation-before-commit → **DECIDED** (موروث من C).
- Modification/Reversal داخل Scope → **PROVEN/CLOSED** (مستحيل بنيوياً؛
  أي "تعديل" يُختزَل لحالة "إضافة" مغطاة أصلاً).
- Acceptance race (نافذة السباق) → **OPEN** (لا تدفق مبني بعد أصلاً).
- Overlapping Events → **OPEN** (مُسجَّل مفتوحاً في C نفسها، لا يمنع
  الانتقال — بنية تحتية يجب ألا تفترض `Event A ∩ Event B = ∅`، بلا حل
  الآن).
- Pending Approval churn → **PRODUCT QUESTION** (ليس تقنياً، لا يُحسَم
  هنا).

### تفصيل Q5 (لا يُعاد فتحه — للسياق فقط)
- Scope-layer determinism → **MEASURED** (3 تشغيلات متطابقة على مدخل
  واحد — Evidence لا Proof عام).
- Single-root invariant اليوم → **PROVEN** (تتبُّع شامل لكل مسار إدراج:
  مستحيل تعدُّد الجذور عبر أي كود إنتاج قائم).
- Multi-root schema risk → **MEASURED + DEFERRED** (أُثبِت الأثر تجريبياً
  عند التفعيل الافتراضي؛ **لا يُصلَح الآن** — القرار المؤجَّل هو "ما
  الترتيب الدلالي الصحيح؟" لا "كيف نجعل `.first()` حتمياً؟"، وهذا فرق
  جوهري: لا يُضاف `order_by` تجميلياً قبل حسم ذلك السؤال).
- Candidate-level reconstruction determinism → **OPEN، غير قابل للاختبار
  حالياً** (طبقة بناء Candidate الاقتصادية غير منفَّذة بعد).

## قرار صريح: لا نُصلِح شيئاً الآن
`.first()` بلا `order_by` في `build_impact_scope()` **يبقى كما هو**. هذا
ليس bugاً يُرقَّع؛ السؤال الحقيقي المؤجَّل هو تصميمي: *"إذا تعدَّدت جذور
حدث واحد، ما الترتيب الدلالي الذي يجب أن يحكم بناء Scope؟"* — قرار
لمرحلة تصميم لاحقة، لا لهذا المستند.

## الخلاصة
G-001 Discovery **مكتملة بالمعنى المطلوب**: ليس لأن كل سؤال حُسِم، بل
لأن كل invariant ثابت الآن معروف ومُصنَّف (DECIDED/PROVEN/MEASURED)
منفصلاً بدقة عن كل قرار ما زال مفتوحاً (OPEN/DEFERRED) — ولا افتراض
واحد تحوَّل إلى قرار تصميمي مصطنع لمجرد الرغبة بالإغلاق.

**الخطوة التالية**: `3B-5-G-002 — C-Conformance Verification`. لا يُعاد
فتح C. الهدف الوحيد: هل ما سيُبنى في G قابل للتنفيذ كما صُمِّم في C
فعلاً، وهل توجد أي تناقضات بين قرارات C والأدلة التي ظهرت في F وG-001؟
لا production implementation في هذه المرحلة أيضاً.
