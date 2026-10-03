# 3B-5-G-003 — Accounting Policy Discovery

Status: DISCOVERY / DESIGN
Production Changes: NONE · Schema Changes: NONE · Migration: NONE ·
Tests Changed: NONE — كل ما يلي بحث مباشر في `app/` عبر `grep`/`view`
فقط، بلا أي `str_replace`/`create_file`/كتابة على المشروع نفسه.

## 1. Executive Summary

**تصحيح تسمية أول**: النموذج المذكور في التعليمات باسم `AccountingCorrection`
اسمه الفعلي في الكود `CorrectionEventAccountingCorrection` — تحقَّق بالبحث
المباشر (صفر نتيجة للاسم الحرفي المطلوب). هذا ليس تفصيلاً — بنية النموذج
نفسها **حاسمة لكل هذا التقرير**: مرجع خفيف (3 أعمدة فقط: `id`,
`correction_event_id`, `journal_entry_id` UNIQUE) لـ`JournalEntry` حقيقي
يُنشَأ عبر `post_immediate()` الموجودة فعلاً — **ليس دفتراً محاسبياً
موازياً**، ولا يخزّن حسابات ولا مبالغ ولا delta references. هذا يعني:
اختيار "أي حساب" **ليس قراراً على مستوى الـschema إطلاقاً** — الـschema
محايد تماماً تجاه أي Option من A إلى D؛ القرار بالكامل يقع في الخدمة التي
ستُنشئ `AccountingIntent`/`LineIntent` لاحقاً (لم تُبنَ بعد).

**أهم اكتشاف جديد لم يُذكَر في أي مستند سابق**: يوجد **مساران مختلفان
فعلياً** في الكود الحالي لـ"عكس" أثر مالي، بسلوكين متناقضين تجاه
recoverability الحساب الأصلي:
- **المسار القديم/الإلغاء** (`invoice_cancel.py` → `journal_edit.py::reverse()`):
  يقرأ `account_id` **حرفياً من `original_entry.lines`** — الحساب الأصلي
  **مضمون الاسترجاع بنيوياً**، بصرف النظر عن أي تغيير لاحق بإعدادات الصنف.
- **المسار الجديد للمرتجعات** (`returns.py::post_sales_return`/
  `post_purchase_return`، الذي **استبدل** `post_return()` القديم صراحة):
  يُعيد اشتقاق الحساب من **إعداد الصنف الحالي وقت المرتجع**
  (`item.sales_account_id or default_sales_acc`, `item.cogs_account_id`,
  `item.inventory_account_id`) — **لا** من القيد الأصلي. لو تغيّر إعداد
  حساب الصنف بين تاريخ البيع الأصلي وتاريخ المرتجع، الحسابان **يختلفان**.

هذا التباين الفعلي داخل نفس قاعدة الكود هو أهم مدخل لتقييم Option A
أدناه — الجواب على "هل الحساب الأصلي قابل للاسترجاع؟" هو **نعم، لكن فقط
إن قرأته بالطريقة التي يستخدمها `reverse()`، لا بالطريقة التي يستخدمها
`returns.py` الأحدث**.

## 2. Existing Accounting Model

بحث مباشر شامل في كل مسار (`posting.py`، `returns.py`، `invoice_cancel.py`،
`journal_edit.py`):

| المسار | مصدر JournalEntry | تحديد الحسابات | Debit/Credit |
|---|---|---|---|
| **Sales** (`post_sales_invoice`) | جديد عبر `post_immediate` | Cash/AR: `Setting['default_cash_account_id']` أو `get_or_create_party_account`. Sales: `item.sales_account_id or default_sales_acc` (**صنف أولاً، إعداد عام احتياطي**). COGS: `item.cogs_account_id` (**صنف، بلا احتياطي**). Inventory: `item.inventory_account_id` | Cash/AR مدين، Sales دائن، Tax دائن (إن وُجد)، COGS مدين/Inventory دائن (غير صفري فقط) |
| **Purchase** (`post_purchase_invoice`) | جديد | Cash/AP: Setting أو party account. Inventory: `item.inventory_account_id`. **لا COGS إطلاقاً** ("الشراء لا يولّد قيد COGS") | Cash/AP دائن، Inventory مدين، Tax مدين |
| **Sales Return** (`post_sales_return`، جديد يستبدل `post_return`) | جديد مستقل | **من إعداد الصنف الحالي وقت المرتجع**، ليس من الفاتورة الأصلية — راجع التحذير أعلاه. تكلفة الوحدة: من حركة المخزون الأصلية إن وُجد ربط، وإلا المتوسط الحالي (`_return_unit_cost`) | عكس اتجاه البيع |
| **Purchase Return** (`post_purchase_return`) | جديد مستقل | نفس نمط Sales Return — إعداد الصنف الحالي | عكس اتجاه الشراء |
| **Invoice Cancellation** (`cancel_invoice`) | عبر `journal_edit.py::reverse()` | **حرفياً من `original_entry.lines` — `account_id=l.account_id`** | مدين/دائن معكوسان تماماً |
| **Manual Journal Voucher** | `journal_edit.py` (DRAFT ثم ترحيل، أو `post_immediate` مباشرة) | يدوي بالكامل من المستخدم | يدوي |
| **Opening Inventory / Opening Party Balance** | `opening_balances.py`/`opening_party_balances.py` (لم تُفحَص بعمق هذه الجولة — خارج التركيز المباشر المطلوب) | — | — |
| **Stock Transfer** | **لا JournalEntry إطلاقاً** — تحقَّقت مباشرة: صفر `JournalEntry(` في `inventory_transfer.py` | — | — |
| **أي correction/reversal حالي آخر** | `reverse()` العامة (نفس namespace `JV-REV` لكل العكوس، بما فيها `reverse_opening_account_balances`/`reverse_opening_inventory`) | من القيد الأصلي حرفياً (نفس نمط الإلغاء) | معكوس |

**قيد تاريخي مكتشَف الآن، مهم لاحقاً (§14)**: `reverse()` تفرض صراحة
`reversal_date >= original_entry.entry_date` — لا يجوز تاريخ عكس أسبق من
الأصل. هذا سيصطدم مباشرة بفكرة "تصحيح بتاريخ الحركة الأصلية" إن استُخدِمت
نفس الدالة لاحقاً — **EVIDENCE**، لا قرار.

## 3. AccountingCorrection Evidence (الاسم الفعلي: `CorrectionEventAccountingCorrection`)

**كل الحقول** (النموذج كاملاً، `app/models.py`): `id` (PK)،
`correction_event_id` (FK → `correction_events.id`, **indexed, NOT
unique**)، `journal_entry_id` (FK → `journal_entries.id`, **UNIQUE**).
لا علاقات أخرى غير `correction_event` (back_populates). **لا** amounts،
**لا** account references، **لا** delta references، **لا** timestamps،
**لا** status/state field.

**التصنيف المطلوب**:
- Schema only: **لا** — معرَّف ومُختبَر بنيوياً (`test_correction_event_schema.py`
  بند 8).
- Created in production code: **لا** — صفر استخدام إنتاجي.
- Read in production code: **لا**.
- Used by tests only: **نعم** — `test_correction_event_schema.py` فقط،
  اختبار شكل schema (يُنشئ صفاً، يتحقق من الفريدة، يتحقق من غياب حقول
  amount/account).
- Unused (إنتاجياً): **نعم**.

**استنتاج حاسم مؤكَّد بالنص المرجعي (C/D) وبقراءة الحقول مباشرة**: وجود
`AccountingCorrection` في الـschema **لا يعني إطلاقاً** أن سياسة
الحساب محسومة — هذا بالضبط ما حذَّرت منه التعليمات، ومؤكَّد هنا فعلياً:
لا حقل واحد يشير لأي حساب.

**قيد `UNIQUE` على `journal_entry_id` بلا `UNIQUE` على `correction_event_id`
يعني بنيوياً**: قيد واحد لا يمكن ربطه بأكثر من تصحيح واحد، **لكن**
CorrectionEvent واحد **يمكنه** أن يُربَط بعدة صفوف (عدة JournalEntry
منفصلة) — أي `one CorrectionEvent → multiple AccountingCorrections`
**مدعوم بنيوياً بالفعل** (يجيب على Q9 لاحقاً).

**فجوة موثَّقة مسبقاً في D (لا اكتشاف جديد، توكيد فقط)**: لا رابط صريح
من `CorrectionEventAccountingCorrection` إلى أي `DeltaRecord` محدد —
الربط على مستوى الحدث بأكمله فقط، لا على مستوى كل delta. `3B-5-D` نفسها
تسجّل هذا كفجوة حقيقية تحتاج جدول ربط إضافي لاحقاً (many-to-many)، لا
كإعادة فتح لقرار سابق.

## 4. CorrectionEventDeltaRecord Evidence

حقلان فقط (مؤكَّد سابقاً بـG-001/G-002): `delta_quantity`،
`delta_inventory_value`. لا حقل COGS منفصل، لا حقل "نوع أثر" (Inventory
adjustment/COGS adjustment).

**الإجابة الدقيقة المطلوبة**: النموذج الحالي **لا** يمثّل Inventory
Value Increase/Decrease أو COGS Increase/Decrease كحقول مستقلة —
هذه **يجب أن تُشتَق لاحقاً** من قيمة `delta_inventory_value` (موجب=زيادة،
سالب=نقصان) ومن معادلة الهوية المحاسبية (§5) لاستنتاج أثر COGS. هذا
يطابق قرار Q3.3.4 حرفياً: "فقط البدائيان يستحقان تخزيناً مستقلاً، كل
الباقي مشتق عند الطلب" — **CONFORMING**، لا فجوة.

## 5. Accounting Identity

`Δ(corrected inputs) = Δ(COGS) + Δ(ending inventory value across
warehouses)` — **هوية رقمية** (Numerical Identity) تحدد **حجم** الأثر
الكلي فقط. **لا تُجيب وحدها** عن "أي حساب يحمل الطرف المقابل" — هذا
مؤكَّد بالفصل: لا نص واحد في C يربط هذه المعادلة بأي `account_id`
محدد. الفصل بين الطبقتين (رقمي ↔ محاسبي) **قائم فعلاً في التصميم**، لا
يحتاج قراراً جديداً هنا — **EVIDENCE + DERIVED**، ليس Design Decision.

## 6. Correction Effect Classes

| الحالة | الوصف | ما يتطلبه محاسبياً (تحليل دلالي، لا قرار) |
|---|---|---|
| **A — Inventory-only** | تصحيح يغيّر `ending inventory value` دون COGS | طرف واحد فقط لحساب المخزون + طرف مقابل (أياً كان المُختار لاحقاً) |
| **B — COGS effect** | تصحيح يغيّر تكلفة حركة بيع/خروج مُرحَّلة سابقاً | طرف COGS + طرف مقابل |
| **C — Inventory + COGS** | تصحيح ينتقل عبر سلسلة شراء→بيع | طرفان معاً، قد يحتاجان حسابين مختلفين أو حساباً موحَّداً — **DESIGN OPTION**، غير محسوم |
| **D — Cross-warehouse (Transfer)** | تصحيح يعبر `W1→W2` أو `W1→W2→W1` | **لا `JournalEntry` لأي Transfer بحد ذاته** — مؤكَّد مباشرة (صفر كود). التصحيح المحاسبي نفسه (إن وُجد) يقع بالكامل على طرفي التصحيح الاقتصادي (Inventory/COGS)، لا على "حساب Transfer" — Transfer ليس عقدة اقتصادية مستقلة (C/G-002 مؤكَّد) |

## 7. Original Account Recoverability

**PARTIALLY PROVEN** — تصنيف مركَّب دقيق، لا واحد عام:
- عبر مسار **الإلغاء/العكس العام** (`reverse()`): **PROVEN** — قراءة
  مباشرة من `JournalLine.account_id` للقيد الأصلي، مضمونة بنيوياً طالما
  `JournalLine`/`JournalEntry` غير قابلين للتعديل بعد الترحيل (مؤكَّد
  سابقاً في Q3-B).
- عبر مسار **المرتجعات الحالي** (`returns.py`): **NOT PROVEN** — يُعاد
  اشتقاق الحساب من إعداد الصنف الحالي، لا القيد الأصلي؛ لا ضمان تطابق
  إن تغيّر إعداد الصنف بين التاريخين.
- **لا** بُنِيت أي سياسة على مجرد وجود `account_id` في مكان ما — التمييز
  أعلاه مبنيّ على تتبُّع كل مسار سطراً بسطر، لا افتراض.

**⚠️ تحذير صريح يجب عدم إغفاله (لا يُستنتَج تلقائياً من الفقرة أعلاه)**:
`Original account recoverability ≠ correctness of using reverse() for G`.
كون `reverse()` تحافظ على الحساب الأصلي **لا يعني أنها العملية
الاقتصادية الصحيحة** لـHistorical Cost Correction. `reverse()` تعني
حرفياً "اعكس هذا القيد بالكامل" (كل الأسطر، بكامل قيمتها)؛ أما تصحيح G
فقد يعني "أنشئ أثراً يمثّل **فرقاً تاريخياً (Δ)** فقط"، لا عكساً كاملاً
للقيد الأصلي — هاتان عمليتان اقتصاديتان مختلفتان قد تتشابهان صدفة في
بعض الحالات (تصحيح يُلغي كامل الحركة) ولا تتطابقان في الحالة العامة
(تصحيح يغيّر جزءاً من القيمة فقط). هذا لا يُقصي Option A، لكنه يمنع اعتباره
"محسوماً تقنياً" لمجرد إثبات الاسترجاع وحده.

## 8. Inventory Account Policy

الحساب مُعرَّف على مستوى **الصنف** (`Item.inventory_account_id`)، ليس
مستودعاً ولا فئة ولا شركة. **`Warehouse` نفسها لا تملك أي عمود حساب
محاسبي إطلاقاً** (تحقَّقت بقراءة النموذج كاملاً: `id`, `name_ar`,
`is_active` فقط). **تمييز صريح كما طلبته التعليمات**: "التكلفة منفصلة لكل
مستودع" (§46، مؤكَّد ومحسوم) **مفهوم مختلف تماماً** عن "الحساب المحاسبي
منفصل لكل مستودع" (**غير موجود إطلاقاً في هذا النظام** — حقيقة، لا
افتراضاً).

## 9. COGS Account Policy

`item.cogs_account_id` — **مستوى الصنف حصراً، بلا احتياطي عام** (خلافاً
لـSales التي تملك `default_sales_acc` احتياطياً). الشراء لا يستخدم COGS
إطلاقاً. **قابلية الاسترجاع الموثوق**: نفس الفصل في §7 — عبر `reverse()`
نعم، عبر إعادة اشتقاق من `item.cogs_account_id` الحالي لا، إلا لو ثبت
عدم تغيّر إعداد الصنف بين التاريخين (غير مضمون من الـschema).

## 10. Candidate Accounting Policies

- **Option A — Original Account**: الـschema **لا يمنعه** (JournalLine
  التاريخية محفوظة بلا تعديل). **قابل للتطبيق حرفياً فقط عبر نمط
  `reverse()`** (قراءة مباشرة من القيد الأصلي)، **غير** قابل للتطبيق
  بأمان عبر نمط `returns.py` الحالي (إعادة اشتقاق من إعداد حالي). هذا
  فرق دقيق يجب تسجيله كشرط، لا كرفض للخيار كله.
- **Option B — Dedicated Correction Account**: الـschema **يسمح** به
  (Chart of Accounts عام، لا قيد بنيوي يمنع حساباً جديداً من أي نوع) —
  **ممكن ضمن البنية الحالية**، بلا اسم/نوع مفترَض.
  Chart-of-Accounts template لم تُفحَص تفصيلاً هذه الجولة (خارج التركيز
  الأساسي المطلوب صراحة).
- **Option C — Policy by Effect**: يتطلب على الأقل **حسابين منفصلين**
  (Inventory-adjustment، COGS-adjustment) إن اعتُمِد — عدد الحسابات
  الفعلي (واحد لكل نوع أم أكثر تفصيلاً) **غير محسوم**، يحتاج قراراً.
- **Option D — Hybrid**: مجرد خيار تصميمي يُقيَّم لاحقاً، غير مرفوض ولا
  مقبول هنا.

## 11. Period / Date Semantics

**بحثت صراحة**: **صفر** كود لأي `fiscal period`/`closed period`/`locked
period`/`posting lock` في كامل المشروع. **لا سياسة قائمة إطلاقاً.**
القيد الوحيد الموجود فعلياً (مختلف تماماً، من `reverse()`):
`reversal_date >= original_entry.entry_date` — قيد على **ترتيب
التواريخ بين عكس وأصله**، لا علاقة له بمفهوم "فترة مغلقة".

**تصنيف مصحَّح**: **OPEN DESIGN DEPENDENCY — NOT CURRENT BLOCKER**، لا
"OPEN DESIGN" مجردة كما وردت سابقاً. الفرق مهم: غياب آلية period-locking
اليوم **لا يحسم** أن المسألة غير مؤثرة على تصميم G مستقبلاً — Historical
Cost Correction سيحتاج حتماً الإجابة على: ما `entry_date` الصحيح للأثر
المحاسبي؟ تاريخ الحركة الأصلية أم تاريخ اكتشاف التصحيح؟ هل يمكن التصحيح
ضمن فترة سيُغلَق تعديلها مستقبلاً حتى لو لا آلية إغلاق فعلية اليوم؟ غياب
الآلية الحالية لا يعني غياب الحاجة للسياسة لاحقاً — لا افتراض بأن
correction يجب أن "يعيد فتح" شيئاً غير موجود أصلاً، لكن أيضاً لا افتراض
بأن غياب المشكلة اليوم يعني عدم حاجتها لقرار عند التنفيذ.

## 12. Multi-Currency Semantics

- `CorrectionEventAccountingCorrection` **لا** يحمل عملة (3 أعمدة فقط،
  مؤكَّد §3).
- `delta_quantity`/`delta_inventory_value` (المصدر الوحيد لقيم G) — لا
  دليل على عملتها من الـschema نفسه؛ بالقياس على `InventoryMovement.unit_cost`
  (مخزَّنة دائماً بالعملة الأساسية، مؤكَّد بتعليقات صريحة بالكود -
  `_jline_base`/WORKFLOW.md §23/§30) **الافتراض الأقرب** هو نفس الشيء.
  **تصنيف مصحَّح**: **UNVERIFIED ASSUMPTION / OPEN VERIFICATION**، لا
  `ASSUMPTION` عادية قد تنزلق ضمنياً إلى تصميم. القياس على
  `InventoryMovement.unit_cost` **لا يكفي وحده لإثبات** دلالة
  `CorrectionEventDeltaRecord.delta_inventory_value` تحديداً — طبقة G
  للـdelta غير منفَّذة بعد، فلا يوجد كود فعلي ليُفحَص لتأكيد هذا الافتراض؛
  يبقى قياساً معقولاً غير مُتحقَّق منه، لا حقيقة مثبتة.
- `JournalEntry` **يحتاج** `exchange_rate` دوماً (عمود إلزامي، افتراضي
  1) — أي `AccountingIntent` لتصحيح مستقبلي سيحتاج تمرير هذا مثل أي قيد
  آخر عبر `post_immediate()` القائمة.
- **لا آلية FX difference تلقائية** موجودة إطلاقاً — مؤكَّد من نص
  السؤال نفسه ومن عدم وجود أي كود لهذا الغرض.
- **OPEN** — هل تصحيح تاريخي قد يتطلب أثر فرق عملة؟ لا آلية حالية،
  ولا افتراض هنا.

## 13. Traceability

السلسلة المطلوبة: `CorrectionEvent → ImpactScopeElement → Historical
Candidate → Delta → AccountingCorrection → JournalEntry → JournalLine`.

- `CorrectionEvent → ImpactScopeElement`: **موجود، مبني فعلاً** (F).
- `→ Historical Candidate`: **NOT IMPLEMENTED YET** (مؤكَّد G-001/G-002).
- `→ Delta`: schema موجود، **NOT IMPLEMENTED YET** استهلاكاً/إنشاءً.
- `→ AccountingCorrection`: schema موجود (§3)، **NOT IMPLEMENTED YET**.
- `AccountingCorrection → JournalEntry → JournalLine`: **الرابط الوحيد
  المفقود فعلياً حتى على مستوى الـschema**: لا ربط بمستوى `Delta`
  المحدَّد (فجوة D الموثَّقة سابقاً، §3). **لا تُضاف هنا** — توثيق فقط.

**تأجيل صريح لـG-004 (لا يُحسَم هنا)**: هذه الفجوة تُترجَم لاحقاً إلى
سؤال يحتاج إثباتاً لا افتراضاً — هل تكفي السلسلة البسيطة
`CorrectionEvent → AccountingCorrection → JournalEntry`، أم يلزم فعلياً
`CorrectionEvent → Delta(s) → AccountingCorrection(s) → JournalEntry(s)`
بربط صريح على مستوى كل Delta؟ هذا مرتبط مباشرة بسؤال Correction
Completeness في G-004 (هل كل الأثر العددي المحلَّل تُرجم فعلاً لأثر
محاسبي، أم قد يبقى جزء محلَّلاً بلا ترحيل) — **لا** يُضاف جدول
many-to-many الآن لمجرد ظهور الفجوة؛ الإثبات يسبق أي تعديل schema.

## 14. Overlapping Events

- هل `AccountingCorrection` يربط تصحيحاً بحدث محدد؟ **نعم** —
  `correction_event_id` FK صريح.
- هل يمكن لنفس `JournalEntry` أن يمثّل أكثر من Event؟ **لا** — `UNIQUE`
  على `journal_entry_id` يمنع ذلك بنيوياً (كل JournalEntry مربوط بحدث
  واحد فقط كحد أقصى، أي `one JournalEntry → one CorrectionEvent`).

**⚠️ تمييز ثلاثي يجب عدم خلطه (لم يكن واضحاً بما يكفي سابقاً)**: هذا
المستند يُثبِت ثلاث طبقات عدّ **مختلفة تماماً**، لا يجوز افتراض تطابقها:
1. **عدد صفوف `AccountingCorrection`** — مُثبَت: يمكن أن يتعدَّد لكل
   Event (`one CorrectionEvent → multiple AccountingCorrections`).
2. **عدد `JournalEntry`** — كل صف `AccountingCorrection` يشير لواحد
   فريد (`UNIQUE`)، لكن هذا لا يخبرنا كم `JournalEntry` "يحتاجها" تصحيح
   واحد اقتصادياً — ذاك قرار تصميم لاحق، لا حقيقة مُثبَتة هنا.
3. **عدد "الآثار الاقتصادية" (economic effects)** — **غير مُثبَت إطلاقاً**
   أنه يساوي أياً من الرقمين أعلاه: `JournalEntry` واحد يستطيع احتواء
   عدة `JournalLine` (مؤكَّد من `post_sales_invoice` نفسها — عدة أسطر
   لعدة حسابات ضمن قيد واحد)، فقد يمثّل قيد واحد عدة آثار اقتصادية معاً
   (مثال §6 Case C: أثر Inventory وCOGS معاً بنفس القيد المحتمل). **لا
   نخلط بين هذه الطبقات الثلاث** عند تصميم G-004/Implementation لاحقاً.
- هل schema يمنع Event واحد من امتلاك أكثر من AccountingCorrection؟
  **لا** — كما في §3، لا `UNIQUE` على `correction_event_id`.
- خطر double-posting عند تداخل حدثين؟ **لا آلية منع حالية** — لا فحص
  تقاطع، لا قفل، لا تحقق عبر الأحداث (مؤكَّد G-001 Q3-C: صفر كود
  overlap detection). **هذا خطر حقيقي غير معالَج**، لكن **لا يُحسَم هنا**
  — Overlap Policy **تبقى OPEN IN C** كما وثَّقها G-002 بالضبط، لا تُغلَق
  في G-003.

## 15. Existing Posting Infrastructure

| المكوِّن | التصنيف |
|---|---|
| `post_immediate()` (البناء الكامل بالذاكرة → validate → flush → balance check → POSTED) | **Reusable as-is** — هذا بالضبط ما تفترضه `CorrectionEventAccountingCorrection` في تعليقها الخاص أصلاً |
| `reverse()` (عكس عام لأي قيد POSTED، namespace `JV-REV` موحَّد) | **Reusable as-is** لحالات "عكس تام" فقط. **⚠️ قيد صريح**: `reverse()` تُنفِّذ دلالياً "عكس القيد بالكامل" (full journal reversal semantics) — **لا يثبت هذا** أنها مناسبة لـHistorical Delta Correction، حيث قد يكون المطلوب إنشاء `JournalEntry` يعكس **الفرق الاقتصادي Δ فقط**، لا كامل القيد الأصلي. هذا حد فاصل بين G-003 وG Implementation، لا يُقرَّر هنا |
| `_validate_line`/`_validate_entry_balance` (توازن، حساب محدَّد لكل سطر، لا مدين+دائن معاً) | **Reusable as-is** |
| `journal_edit.py::ensure_editable`/`_validate_lines` (لقيود DRAFT اليدوية) | غير ذي صلة مباشرة (correction ستكون POSTED مباشرة على الأغلب، كالفواتير) |
| Transaction boundaries (نمط flush متعدد + commit واحد، مؤكَّد سابقاً G-001) | **Reusable as-is** كنمط ذرّية |

**لم يُستخدَم أي منها فعلياً هنا — توصيف فقط، كما اشتُرِط.**

## 16. Minimal Semantic Examples (أمثلة صغيرة، سياسة فقط، لا correctness engine)

**Example 1** — Purchase +10 → تصحيح تاريخي +100 قيمة مخزون، لا أثر COGS:
طرف Inventory وحده يتأثر. أي Policy (A/B/C/D) تحتاج طرفاً مقابلاً واحداً
فقط هنا.

**Example 2** — Purchase → Sale → تصحيح يغيّر COGS بمقدار +50: طرف COGS
متأثر فقط (لا Inventory، لأن البضاعة خرجت أصلاً). Option A هنا يحتاج
الوصول لحساب COGS الأصلي لحركة البيع المتأثرة تحديداً — العودة لـ§7:
موثوق فقط لو اتُّبِع نمط `reverse()`.

**Example 3** — Purchase W1 → Transfer W1→W2 → Sale W2 → تصحيح يعبر
Transfer: **لا JournalEntry لخطوة Transfer نفسها** (§6-D)، فالأثر
المحاسبي بالكامل يقع على طرفي Inventory (W1 وW2 معاً، عزل تكلفة منفصل
لكل مستودع لكن **لا** عزل حساب GL منفصل — §8) وCOGS في W2. لا Transfer
account لتمثيله لأنه غير موجود أصلاً.

**Example 4** — Event A وEvent B متقاطعان: schema **لا يمنع** كليهما من
إنتاج `AccountingCorrection` منفصلة (كل منهما `journal_entry_id` فريد
خاص به) — لكن **لا آلية تمنع ازدواج تسجيل نفس الأثر الاقتصادي مرتين**
إن تقاطع Scope الفعلي. خطر حقيقي، **OPEN** (§14).

## 17. Policy Comparison Matrix

| Policy | Evidence Support | Original Account Recoverability | Traceability | Multi-Effect Support | Period Handling | Double-Posting Risk | Complexity | Open Questions |
|---|---|---|---|---|---|---|---|---|
| A — Original Account | SUPPORTED (عبر نمط reverse فقط) | CONDITIONALLY SUPPORTED | SUPPORTED (نظرياً، عبر JournalLine) | UNKNOWN | UNKNOWN | REQUIRES DESIGN DECISION | LOW (بنية موجودة) | هل نعتمد returns.py الحالي أم نمط reverse()؟ |
| B — Dedicated Account | SUPPORTED (COA لا يمنعه) | NOT SUPPORTED (لا علاقة بالأصل) | SUPPORTED | SUPPORTED | UNKNOWN | REQUIRES DESIGN DECISION | LOW | أي حساب/كم حساباً؟ |
| C — Policy by Effect | CONDITIONALLY SUPPORTED (يحتاج ≥2 حسابات) | NOT SUPPORTED مباشرة | SUPPORTED | SUPPORTED جيداً | UNKNOWN | REQUIRES DESIGN DECISION | MEDIUM | كم حساباً بالضبط؟ |
| D — Hybrid | CONDITIONALLY SUPPORTED | CONDITIONALLY SUPPORTED | SUPPORTED | SUPPORTED | UNKNOWN | REQUIRES DESIGN DECISION | HIGH | متى نستخدم أيهما بالضبط؟ |

لا Ranking، لا Winner، لا Recommended — كما اشتُرِط.

## 18. Open Design Questions (إجابات صريحة على الأسئلة الخمسة عشر)

- **Q1** (الحساب الأصلي؟): OPEN — Design Option، ليس قراراً.
- **Q2** (استرجاع موثوق؟): **جزئياً** — نعم عبر `reverse()`، لا عبر
  `returns.py` الحالي (§7).
- **Q3** (حساب مخصص إن تعذّر؟): OPEN.
- **Q4** (حسابان منفصلان Inventory/COGS؟): OPEN — Case C (§6) تحتاج هذا
  تحديداً لو اختير Option C.
- **Q5** (مستوى السياسة؟): OPEN بالكامل.
- **Q6** (أثر موزَّع على أكثر من حساب؟): البنية (`AccountingCorrection`
  → `JournalEntry` عادي متعدد الأسطر) **تدعمه بنيوياً بلا مشكلة** — أي
  عدد من `JournalLine` ضمن نفس `JournalEntry` ممكن أصلاً.
- **Q7** (تاريخ الأصل أم تاريخ التصحيح؟): OPEN DESIGN — لا سياسة، ولا
  فحص "تاريخ تصحيح" أصلاً في الكود الحالي (القيد الوحيد: `reversal_date
  >= original.entry_date` في `reverse()` تحديداً، ليس مبدأ عاماً مُثبَتاً
  لكل تصحيح مستقبلي).
- **Q8** (فترات مغلقة؟): OPEN — لا مفهوم فترة موجود أصلاً (§11).
- **Q9** (واحد إلى واحد أم واحد إلى عدة؟): **محسوم بنيوياً بالفعل** —
  `one CorrectionEvent → multiple AccountingCorrections` **مدعوم**
  (لا UNIQUE على correction_event_id)، **لكن** لا ربط دون مستوى الحدث
  (لا delta-level) — راجع فجوة §3/§13.
- **Q10** (تقاطع → ازدواج ترحيل؟): **نعم، خطر حقيقي غير معالَج** —
  **OPEN**، لا حل هنا (§14/§16 مثال 4).

## 19. Design Blockers

**الصياغة الدقيقة (مصحَّحة)**: *No blocker prevents closing G-003
Discovery. Several policy dependencies remain OPEN and must be resolved
before G Accounting Implementation.* — الفرق مهم: هذا **لا يعني** أن
الأسئلة المفتوحة أدناه تختفي لمجرد عدم وجود blocker يمنع الانتقال.

**لا يوجد Design Blocker واحد** بالمعنى المطلوب (A–G في التعليمات):
- A (الحساب الأصلي غير قابل للاسترجاع reliably): **لا** — قابل للاسترجاع
  عبر نمط `reverse()` تحديداً؛ الشرط موثَّق لا الحظر.
- B (schema لا يمثّل الأثر): **لا** — Inventory/COGS effects مشتقة من
  الحقلين الموجودين، متسق مع Q3.3.4.
- C (يحتاج أكثر من JournalEntry لكن schema يفترض واحداً): **لا** —
  `AccountingCorrection` يسمح بعدة صفوف لكل حدث (§3/§14).
- D (Closed-period handling غير موجود): **حقيقة موثَّقة (§11)، لا
  blocker** — لأنه لا مفهوم فترة أصلاً بعد ليُفتقَد.
- E (Currency semantics غير واضحة): **جزئياً OPEN (§12)**، لا blocker
  حاد — البنية الأساسية (exchange_rate إلزامي) موجودة، الفجوة في FX
  difference فقط.
- F (Overlap → double-posting بلا منع): **خطر حقيقي موثَّق (§14)** —
  أقرب لـ**OPEN DESIGN QUESTION حرجة** منه لـblocker يمنع الاستمرار،
  لأن السياسة نفسها OPEN IN C أصلاً ولم تكن G-003 مسؤولة عن حسمها.
- G (البنية الحالية لا تحافظ على invariants): **لا** — `post_immediate`/
  `reverse` يحافظان على التوازن والتتبع فعلياً (§15).

## 20. Evidence / Derived / Option / Decision Classification (ملخص)

EVIDENCE: كل جدول §2/§3/§4/§8/§9/§11/§12/§15. DERIVED: §5 (الفصل
رقمي/محاسبي)، §7 (الاسترجاع الجزئي). EXISTING POLICY: item-level
accounts، لا JournalEntry لـTransfer، لا COGS للشراء. DESIGN OPTION:
كل Option A–D بكامله (§10/§17). OPEN QUESTION: §11، §12 (FX)، §14،
Q1/Q3/Q4/Q5/Q7/Q8/Q10 من §18. DESIGN DECISION: **لا شيء** — لم يُستخدَم
هذا التصنيف مرة واحدة في التقرير، كما اشتُرِط. ASSUMPTION: عملة
delta_* الأساسية (§12) فقط.

## 21. G-003 Exit Gate

- Existing accounting behavior موثَّق من الكود الفعلي مباشرة (§2). ✅
- `CorrectionEventAccountingCorrection` فُحِص بالكامل (§3). ✅
- `CorrectionEventDeltaRecord` فُحِص بالكامل (§4). ✅
- Original account recoverability أُثبِت/نُفي بالأدلة — **مركَّب دقيق**،
  لا حكم عام واحد؛ **ومحدود صراحة بعدم كفايته وحده لتفضيل Option A**
  (§7). ✅
- كل Policy Options الأربعة حُدِّدت (§10). ✅
- أثر كل Option على Inventory/COGS/Transfer/Currency/Periods حُلِّل
  (§6/§10/§11/§12). ✅
- Accounting invariants (الستة المذكورة بالتعليمات) اختُبِرت منطقياً —
  لم تُنتهَك واحدة منها بأي دليل وُجِد. ✅
- Traceability gaps حُدِّدت مع تأجيل صريح لسؤال الإثبات إلى G-004 (§13). ✅
- Overlap policy بقيت **OPEN IN C** كما هي (§14/§18-Q10). ✅
- Period semantics: **OPEN DESIGN DEPENDENCY — NOT CURRENT BLOCKER**
  (§11، مصحَّح). ✅
- Currency semantics: **UNVERIFIED ASSUMPTION / OPEN VERIFICATION**
  (§12، مصحَّح). ✅
- **لا production change واحد.** ✅
- **لا policy نهائية اختيرت** دون فصلها عن الدليل — القسم 17 بلا
  Ranking/Winner. ✅
- **لا blocker يمنع إغلاق G-003 Discovery؛ عدة تبعيات سياسة تبقى OPEN
  ويجب حسمها قبل G Accounting Implementation** (صياغة مصحَّحة، §19).

**النتيجة: G-003 → CLOSED** بعد التصحيحات التوثيقية أعلاه.
التالي: **3B-5-G-004 — Correction Completeness** — السؤال هناك مختلف
جوهرياً: ليس "إلى أي حساب نرحّل؟"، بل "هل كل الأثر العددي المحلَّل تُرجم
فعلاً لأثر محاسبي، أم قد تبقى Delta محلَّلة بلا ترحيل؟"
