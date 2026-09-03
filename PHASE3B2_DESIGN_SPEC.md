# Phase 3B-2 — مواصفة تصميم: الأرصدة الافتتاحية للمخزون
**الحالة: مُعتمَدة من حيث المبدأ (Bilal) — القرار المعماري الوحيد (§2،
عكس رصيد المخزون) محسوم. لا كود بعد؛ بانتظار المراجعة النهائية قبل
بدء `post_opening_inventory()`.**

---

## 0. ملاحظة أولى مهمة: دالة موجودة مسبقاً تحتاج استبدالاً، لا توسيعاً

`app/services/opening_balances.py::set_item_opening_balance()` دالة
**موجودة فعلاً بالمستودع من قبل هذه المراجعة كلها** (قديمة، سابقة
لمواصفة Phase 3 بأكملها). فحصتها بالتفصيل قبل كتابة هذه المواصفة —
**لا تفي بمعظم القيود الستة عشر** التي طلبتها:

| الفجوة بالدالة الحالية | القيد المطلوب |
|---|---|
| `warehouse_id` اختياري، يسقط للمستودع الافتراضي بصمت | #1 إلزامي، لا سقوط صامت |
| لا تحقق من `item.is_active` | #2 |
| لا تحقق `quantity > 0` ولا `unit_cost >= 0` (قد تقبل قيماً سالبة أو صفراً) | #3, #4 |
| `unit_cost` يُخزَّن كما يُمرَّر مباشرة، لا تحويل عملة موثَّق | #5 |
| **لا قيد محاسبي يُنشَأ إطلاقاً** — `InventoryMovement` فقط، بلا `JournalEntry` مقابل | #7 (جزئي)، #8، #13، #14 |
| فحص التكرار **لكل مادة فقط** (`item_id`)، لا لكل (مادة+مستودع) — هذا **خطأ فعلي** يمنع نفس المادة من الحصول على رصيد افتتاحي بمستودعين مختلفين، عكس المطلوب تماماً | #10 |
| لا Idempotency على مستوى الدفعة الكاملة (Setting)، فقط لكل مادة بمعزل | #11 |
| لا batch — استدعاء واحد لكل مادة، فلا معنى لـ"rollback ذري لكل الدفعة" | #12 |

**القرار المقترَح**: `set_item_opening_balance()` **تُستبدَل بالكامل**
بدالة جديدة `post_opening_inventory()` بنفس فلسفة `post_opening_account_balances()`
(دفعة واحدة، قيد محاسبي واحد، Idempotency على مستوى الدفعة). الدالة
القديمة تُحذَف عند التنفيذ — لا تُبقى كمسار بديل (لا نريد طريقتين
لعمل نفس الشيء).

---

## 1. القيود الستة عشر — قاعدة تنفيذية لكل واحد

### #1 warehouse إلزامي لكل رصيد افتتاحي
`OpeningInventoryLineInput.warehouse_id: int` (لا `| None`، لا قيمة
افتراضية، لا `get_default_warehouse()` — نفس انضباط `_average_cost()`
الحالية بالضبط التي **لا تقبل قيمة افتراضية أصلاً لنفس السبب**).

### #2 item إلزامي وفعّال + مستودع موجود وفعّال
تحقق: `session.get(Item, item_id)` موجود، و`item.is_active == True`.
كذلك `session.get(Warehouse, warehouse_id)` موجود، و`warehouse.is_active
== True` (كلا التحققين معاً — الحقل موجود فعلاً على `Warehouse`).
مرفوض غير ذلك.

### #3 quantity > 0
`quantity <= 0` مرفوض صراحة (لا فرق بين "رصيد صفري لا داعي له" و"رصيد
سالب لا معنى محاسبياً له" — كلاهما مرفوض بنفس الرسالة الواضحة).

### #4 unit_cost >= 0
`unit_cost < 0` مرفوض. **`unit_cost == 0` مسموح صراحة** (حالة واقعية:
مواد مُستلَمة مجاناً أو هدايا موردين تاريخية — رفضها لا مبرر محاسبياً
له، طالما الكمية موجودة فعلياً).

### #5 تخزين `unit_cost` بالعملة الأساسية — يطابق القاعدة الموجودة فعلاً
**ليس قراراً جديداً — توثيق لقاعدة راسخة بالفعل**: `post_purchase_invoice()`
الحالية تحوّل التكلفة للعملة الأساسية **قبل التخزين** بالضبط
(`net_in_base = net_after_discounts × invoice.exchange_rate`، ثم
`unit_cost_after_discount = net_in_base / qty`، راجع WORKFLOW.md §29).
الرصيد الافتتاحي يتبع **نفس القاعدة حرفياً**:
```
unit_cost_base = unit_cost_foreign × exchange_rate
```
`InventoryMovement.unit_cost` المُخزَّن = `unit_cost_base` دائماً — لا
عملة أجنبية خام أبداً بهذا العمود (يطابق كل حركة IN/OUT أخرى بالنظام،
لا استثناء لحركة الافتتاح).

### #6 `quantity × unit_cost_base` = القيمة الأساسية للرصيد الافتتاحي
قيمة السطر بالقيد المحاسبي = `quantity × unit_cost_base` — لا حساب
منفصل، نفس الرقم المُخزَّن بـ`InventoryMovement` مضروباً بالكمية.

### #7-8 حركة مخزون حقيقية + ربطها بالقيد المحاسبي
```python
InventoryMovement(
    item_id=..., warehouse_id=..., direction=MovementDirection.IN,
    quantity=..., unit_cost=unit_cost_base,
    movement_date=opening_date,
    source_type="opening_balance", source_id=journal_entry.id,  # ربط صريح
)
```
`source_id` يشير لـ`JournalEntry.id` مباشرة — يطابق نمط `source_id`
الحالي تماماً (بالفواتير: `source_id=invoice.id`؛ هنا لا "فاتورة"
وسيطة، فالمرجع الطبيعي هو القيد نفسه). هذا **نفس عمود موجود فعلاً**،
لا حقل جديد على `InventoryMovement`.

### #9 عدم استخدام current average cost — القيمة المُدخَلة تاريخياً فقط
لا استدعاء لـ`_average_cost()`/`get_item_stock_summary()` أثناء
**الإدخال** إطلاقاً — القيمة تأتي حصراً مما أدخله المستخدم
(`unit_cost_foreign`/`exchange_rate`). الدالتان أعلاه تُستخدَمان فقط
لاحقاً، بعد الترحيل، عند حساب COGS لبيع تالٍ — وعندها ستجدان حركة
الافتتاح ضمن السجل التاريخي تلقائياً (بلا أي كود إضافي، لأنهما تحسبان
من `InventoryMovement` مباشرة أصلاً).

### #10 نفس الصنف بمستودعين = رصيدان مستقلان
فحص التكرار (منع #11 أدناه) يكون بمفتاح **(item_id, warehouse_id)**
معاً، لا `item_id` وحده (هذا بالضبط الخطأ بالدالة القديمة، راجع §0).
`Item X` بمستودع A ومستودع B = سطران منفصلان تماماً بنفس الدفعة،
كلاهما مقبول.

### #11 منع تكرار العملية بالكامل
**نفس آلية 3B-1 حرفياً**: `Setting['opening_inventory_posted_at']` —
دفعة واحدة لكل الشركة، رفض صريح لا upsert صامت عند محاولة ثانية، بغض
النظر عن المواد/المستودعات المطلوبة بالمحاولة الثانية (حتى لو مادة
جديدة كلياً لم تُذكَر بالمحاولة الأولى — نطاق "الأرصدة الافتتاحية
للمخزون" نطاق واحد لكل الشركة، يُقفَل دفعة واحدة، **وليس** قفلاً لكل
(item, warehouse) بمعزل — التصحيح دائماً عبر العكس الكامل ثم إعادة
إدخال الدفعة الصحيحة بالكامل، لا إضافة سطر ناقص لاحقاً بمعزل).

### #12 rollback ذرّي لكل الدفعة
**نفس نمط 3B-1 حرفياً**: لا `session.commit()`/`rollback()` داخل
الخدمة نفسها — فشل أي سطر واحد (مادة غير موجودة/غير فعّالة، مستودع
غير موجود، كمية/تكلفة غير صالحة) يوقف الدالة بـ`OpeningBalanceError`
فوراً؛ المُستدعي يستدعي `session.rollback()` فيُمحى كل أثر جزئي (يُثبَت
بنفس اختبار transaction/rollback المطابق لبند 11 بـ3B-1).

### #13 لا Revenue ولا COGS
القيد الناتج طرفان فقط: `Dr <item.inventory_account_id> / Cr
<opening_balance_clearing_account_id>` — **لا COGS يُنشَأ إطلاقاً**
(الرصيد الافتتاحي ليس بيعاً، لا تكلفة بضاعة مباعة له). يطابق مبدأ
§53 الحاكم (حسابات Revenue/Expense تُرفَض من أي رصيد افتتاحي) — هنا
لا حتى COGS (وهو Expense) يُلمَس.

### #14 نفس Clearing Account المُعتمَد بـ3B-1
**قرار محسوم، لا سبب محاسبي يستوجب خلافه**: نفس
`Settings['opening_balance_clearing_account_id']` (EQUITY، §6
بمواصفة Phase 3 الأصلية). لا حساب "مخزون افتتاحي" منفصل — القيمة
تدخل مباشرة لحساب المخزون الحقيقي (`item.inventory_account_id`، كأي
حركة شراء عادية) والطرف المقابل هو نفس Clearing، تماماً كما نصّت
المواصفة الأصلية §6 مسبقاً (لم يتغيّر شيء هنا، فقط تأكيد).

### #15 بيع لاحق من المستودع يستخدم تكلفة الافتتاح
**لا كود إضافي مطلوب لهذا — نتيجة طبيعية لإعادة استخدام المحرك
الموجود**: بما أن حركة الافتتاح `InventoryMovement` حقيقية بنفس
الجدول، فإن `get_item_stock_summary(session, item_id, warehouse_id)`
و`_average_cost()` ستجدانها تلقائياً كأول حركة IN بتاريخها — بيع لاحق
من نفس (item, warehouse) يحسب COGS من متوسط يشمل رصيد الافتتاح
تلقائياً، بلا لمس أي من الدالتين. هذا **الدليل العملي** على "عدم
تكرار آلية حساب المخزون" الذي طلبتَه صراحة.

### #16 اختبار migration وإعادة فتح الشركة
لا عمود جديد على `Item`/`Warehouse`/`InventoryMovement` — الحركة
تستخدم أعمدة موجودة فعلاً بالكامل. الهجرة الوحيدة المطلوبة: جدول
تفصيلي جديد `opening_inventory_entries` (نفس دور `OpeningBalanceEntry`
بـ3B-1 تماماً — سجل تفصيلي، لا Audit Log كامل، لا يُستبدَل به
`InventoryMovement`/`JournalEntry` كمصدر حقيقة). يُختبَر migration +
إغلاق/إعادة فتح فعلي (اتصال جديد تماماً) بنفس منهج اختبار 3B-1 رقم 10.

---

## 2. الخدمة المقترَحة — التوقيع فقط (لا تنفيذ)

```python
@dataclass
class OpeningInventoryLineInput:
    item_id: int
    warehouse_id: int                    # إلزامي — لا افتراضي
    quantity: Decimal
    unit_cost_foreign: Decimal
    currency_code: str | None = None     # None = العملة الأساسية للشركة
    exchange_rate: Decimal = Decimal("1")

def post_opening_inventory(
    session: Session, entries: list[OpeningInventoryLineInput], opening_date: date,
) -> JournalEntry:
    """
    Idempotency: Setting['opening_inventory_posted_at'] — دفعة واحدة
    لكل الشركة، نفس نمط post_opening_account_balances() حرفياً.
    لا استدعاء ترحيل جديد: نفس add_manual_line()/post_manual_entry()
    لبناء القيد، مضافاً إليها InventoryMovement فعلية لكل سطر (لا Item
    Movement منفصلة عن القيد — كلاهما بنفس الاستدعاء الذري).
    """

def reverse_opening_inventory(session: Session, journal_entry: JournalEntry, reversal_date: date) -> JournalEntry:
    """
    §2 — قرار معتمَد (Bilal): يعكس القيد (reverse_manual_entry، نفس
    آلية 3B-1) + يُنشئ حركات InventoryMovement عكسية (direction=OUT)
    لكل حركة IN أصلية بنفس الكمية والتكلفة التاريخية بالضبط (يطابق
    نمط cancel_invoice §44 — عكس حرفي، لا إعادة حساب) + يمسح Setting
    القفل — **لكن فقط بعد اجتياز الفحص التالي لكل (item, warehouse)
    بالدفعة**:

    يُرفَض العكس بـOpeningBalanceError صراحة لو وُجدت أي حركة OUT
    منشورة (POSTED) لاحقة لنفس (item_id, warehouse_id) اعتمدت فعلياً
    على متوسط يشمل حركة الافتتاح هذه — أي: أي بيع/تحويل خارج تم
    *بعد* تاريخ الافتتاح لنفس المادة بنفس المستودع. يُسمح بالعكس فقط
    لو لم توجد أي حركة OUT لاحقة إطلاقاً لذلك (item, warehouse) تحديداً
    (لا يعني وجود بيع من مستودع آخر لنفس المادة أي منع — الفحص لكل
    (item, warehouse) بمعزل تماماً، يطابق مبدأ عزل التكلفة لكل مستودع
    §46).

    السبب (Bilal، ليس فقط الاتساق مع cancel_invoice): عكس حركة
    الافتتاح بعد استخدامها فعلياً بحساب COGS لبيع لاحق يكسر السلسلة
    التاريخية Opening→Movement→Average Cost→COGS→Journal بأثر رجعي —
    يخالف "History is never re-priced retroactively" (WORKFLOW.md §39)
    تماماً كما لو أُعيد حساب فاتورة بيع قديمة بمتوسط جديد. لا فرق هنا
    بين "لا رصيد متبقٍّ من الافتتاح" (بيع كل الكمية) و"رصيد جزئي متبقٍّ"
    (بيع جزء فقط) — كلاهما يعني أن Average Cost اللاحق **اعتمد فعلياً**
    على قيمة الافتتاح، فكلاهما يُرفَض بنفس الحسم.
    """
```

---

## 3. جدول جديد: `opening_inventory_entries`

| العمود | النوع |
|---|---|
| `id` | PK |
| `journal_entry_id` | FK→journal_entries |
| `item_id` | FK→items |
| `warehouse_id` | FK→warehouses |
| `inventory_movement_id` | FK→inventory_movements (ربط مباشر بالحركة الفعلية، إضافة عن نمط 3B-1 — يفيد هنا تحديداً لأن كل سطر يُنتِج حركة مخزون منفصلة، بخلاف 3B-1 حيث القيد وحده كافٍ) |
| `quantity` | numeric |
| `unit_cost_foreign` | numeric |
| `currency_code` | str(3) |
| `exchange_rate` | numeric |
| `unit_cost_base` | numeric |
| `opening_date` | date |

---

## 4. Acceptance Gate المُعتمَد — 12 مجموعة (مطابقة لطلبك حرفياً)

1. **Opening Qty × Unit Cost**: Item X, Warehouse A, Qty=100, Cost=5
   USD → `InventoryMovement.unit_cost=5`, القيد = `Dr Inventory 500 /
   Cr Clearing 500` بالضبط.
2. **Warehouse isolation**: نفس Item بمستودعين مختلفين، تكلفتان
   مختلفتان تماماً — لا تداخل، بيع من كل منهما يستخدم متوسط مستودعه
   فقط (نفس نمط اختبار §55/§57 السابق، بنقطة بداية افتتاحية بدل شراء).
3. **Historical cost after opening**: Opening Qty=100@5 → Sale Qty=20
   → `COGS = 20×5 = 100 USD` بالضبط (Oracle مستقل، لا "current
   purchase price" لو وُجد شراء لاحق بسعر مختلف).
4. **Multi-item**: عدة مواد بنفس الدفعة، كل مادة بقيمتها الصحيحة
   منفصلة، القيد الإجمالي متوازن رغم تعدد الأسطر.
5. **Multi-warehouse**: (يتقاطع مع #2) — دفعة واحدة تغطي عدة مستودعات
   معاً بعملية ترحيل واحدة.
6. **Zero/negative validation**: `quantity<=0` مرفوض، `unit_cost<0`
   مرفوض، `unit_cost=0` **مقبول** صراحة (راجع #4).
7. **Inactive item/warehouse**: مادة `is_active=False` مرفوضة؛ مستودع
   `is_active=False` مرفوض أيضاً (الحقل موجود فعلاً على `Warehouse`)؛
   مستودع غير موجود إطلاقاً مرفوض كذلك.
8. **Duplicate opening**: محاولة ثانية للدفعة كاملة تُرفَض (Setting)،
   مع تحقق مباشر أن الكمية **لا تتضاعف** (100 تبقى 100، لا 200) —
   نفس منهج إثبات 3B-1، ليس فقط "الاستدعاء الثاني يرمي استثناء".
9. **Rollback**: فشل سطر لاحق بمنتصف دفعة متعددة الأسطر (بعد سطر أول
   صحيح) + `session.rollback()` من المُستدعي → صفر `InventoryMovement`
   وصفر `JournalEntry` وصفر `opening_inventory_entries` متبقٍّ، لا
   Setting مقفول خطأً، إعادة المحاولة تنجح نظيفة.
10. **Reopen database**: إغلاق وإعادة فتح فعلي (اتصال جديد تماماً) —
    الأرصدة والقيد وحركات المخزون كلها سليمة، `get_trial_balance()`
    متوازن، `get_item_stock_summary()` يعطي نفس النتيجة.
11. **GL ↔ Inventory reconciliation**: مجموع `quantity × unit_cost_base`
    لكل حركات الافتتاح = رصيد حساب المخزون الفعلي بميزان المراجعة
    بالضبط — لا فرق تقريب، تحقق مباشر مقارنةً لا افتراضاً.
12. **Sale after opening**: (يكرر #3 بصياغة أخرى، مُبقى منفصلاً لأنه
    الاختبار الأهم برأيك) دورة كاملة عبر UI فعلية لاحقاً بـ3B-6، وعبر
    الخدمة مباشرة الآن — بيع فعلي بعد الافتتاح يُنتِج COGS صحيحاً
    ويُحدِّث الكمية المتبقية بشكل صحيح (`100-20=80` متاحة للبيع التالي).

كل مجموعة ستكون ملف/قسم اختبار مستقل يُشغَّل ضمن `run_gate.py`، بنفس
انضباط 3B-1 (43 تحقّقاً موزَّعة على 11 قسماً هناك؛ توقُّع مشابه هنا).

---

## القرارات المعتمدة نهائياً (Bilal) — لا أسئلة مفتوحة متبقية

1. **§2 (عكس رصيد المخزون الافتتاحي) — معتمَد**: يُرفَض العكس إذا
   وُجدت حركة بيع لاحقة **منشورة (POSTED)** اعتمدت فعلياً على تكلفة
   ذلك الرصيد الافتتاحي (فحص لكل (item, warehouse) بمعزل — راجع النص
   الكامل بدالة `reverse_opening_inventory()` أعلاه). يُسمح بالعكس فقط
   حين لا توجد أي حركة لاحقة كهذه، ثم يُعاد إنشاء الرصيد بالقيمة
   الصحيحة. السبب: كسر السلسلة التاريخية Opening→Movement→Average
   Cost→COGS→Journal بأثر رجعي، لا فقط الاتساق مع `cancel_invoice()`.
2. **استبدال `set_item_opening_balance()` بالكامل**: معتمَد — تُحذَف
   عند التنفيذ، لا تُبقى كمسار بديل مواز.
3. **نطاق Idempotency**: معتمَد كما اقتُرح — دفعة واحدة لكل الشركة
   بالكامل (نفس نطاق 3B-1 حرفياً)، لا قفل منفصل لكل (item, warehouse).
4. **`Warehouse.is_active`**: مؤكَّد أنه عمود موجود فعلاً بالنموذج
   الحالي — التحقق المطلوب هو `warehouse exists + is_active` معاً، لا
   حاجة لأي عمود جديد أو هجرة لهذا الغرض تحديداً (الهجرة الوحيدة
   المطلوبة لـ3B-2 تبقى فقط جدول `opening_inventory_entries` الجديد).

**الخطوة التالية**: بانتظار المراجعة النهائية من Bilal قبل بدء تنفيذ
`post_opening_inventory()` — لا كود بعد رغم اعتماد المواصفة من حيث
المبدأ.
