# PHASE3B4_DESIGN_SPEC
**مواصفة تصميم تنفيذية — تترجم PHASE3B4_ARCHITECTURE_REPORT (مغلقة رسمياً) لتفاصيل قابلة للتنفيذ، بلا إعادة فتح أي قرار معماري. لا كود يُنفَّذ بهذا التسليم، لا Migration تُطبَّق فعلياً — تصميمها فقط.**

---

## 0. مرجعية حاكمة

كل قرار بـ`PHASE3B4_ARCHITECTURE_REPORT.md` (§0-§13) **نهائي وغير قابل لإعادة الفتح هنا** — هذا المستند يحوّل "ماذا قررنا" إلى "كيف يُبنى بالضبط"، لا أكثر. أي تفصيل هنا يتعارض ظاهرياً مع قرار معماري سابق = خطأ بهذا المستند يجب تصحيحه، لا سبباً لإعادة فتح ذلك القرار.

---

## 1. Sequence Table — الشكل النهائي

```
جدول: journal_number_sequences

id                  INTEGER PRIMARY KEY
namespace           TEXT NOT NULL UNIQUE   -- مثال: "JV"، "JV-REV"، "JE-SAL"، "JE-PUR"، "JE-RCV"، "JE-PAY"، "JE-CREF"، "JE-SREF"، "JV-OPEN"، "JV-OPNINV"، "JV-OPNPTY"، "INV-CXL"
last_value          INTEGER NOT NULL DEFAULT 0
```

**قواعد**:
- **صف واحد لكل namespace** — لا `LIKE`/`NOT LIKE` بأي استعلام لاحق؛ الفصل بنيوي بمفتاح `namespace` نفسه (يحل تداخل `JV-%`/`JV-REV-%` نهائياً بالبنية، لا بشرط استعلام).
- **الزيادة الذرّية**: عملية واحدة (`UPDATE journal_number_sequences SET last_value = last_value + 1 WHERE namespace = ? RETURNING last_value` أو ما يعادلها حسب دعم SQLite للإصدار المُستهدَف؛ البديل الآمن عالمياً: `UPDATE ... SET last_value = last_value + 1 WHERE namespace=?` ثم `SELECT last_value` **بنفس الـtransaction** قبل أي `commit` — كلا الشكلين مقبولان تصميمياً، التفصيل التنفيذي الدقيق يُحسَم عند الكتابة الفعلية لاحقاً، لا يُلزَم هنا).
- **`namespace` جديد غير موجود** → يُنشَأ صف جديد بـ`last_value=0` تلقائياً عند أول طلب (لا حاجة لبذر (seed) يدوي مسبق لكل namespace).
- **تنسيق الرقم النهائي المعروض للمستخدم** (`ref_no` الفعلي المُخزَّن على `JournalEntry`): `f"{namespace}-{last_value:06d}"` (يطابق التنسيق الحالي بالضبط — لا تغيير على الشكل المرئي للمستخدم، فقط على آلية التوليد الداخلية).
- **لا حذف صفوف من هذا الجدول أبداً** — Append-only بالروح نفسها المُتَّبعة بكل جداول الترقيم بالمشروع.

**أثر Migration**: جدول جديد واحد فقط. **لا تعديل على `JournalEntry`/`JournalLine` نفسيهما بهذه الخطوة** (العمود `ref_no` موجود أصلاً، `UNIQUE` موجودة أصلاً — لا تغيير Schema عليه).

---

## 2. Accounting Intent — التمثيل العملي (بلا Abstraction زائد)

**قرار تصميم صريح**: لا Class/Model جديد بقاعدة البيانات لـ"Accounting Intent" — هو **تمثيل عابر بالذاكرة فقط** (Python dataclass، لا جدول)، يموت فور استهلاكه بالـBoundary. هذا يمنع تحويله لطبقة تخزين موازية (God Table بدل God Service).

```python
@dataclass
class LineIntent:
    account_id: int
    debit_raw: Decimal = Decimal("0")
    credit_raw: Decimal = Decimal("0")
    debit_base: Decimal = Decimal("0")
    credit_base: Decimal = Decimal("0")
    line_currency_code: str | None = None   # None = يرث من القيد
    line_exchange_rate: Decimal | None = None  # None = يرث؛ إن وُجد يجب أن يكون >0 (§5-ج بالمعمارية)

@dataclass
class AccountingIntent:
    entry_date: date
    currency_code: str
    exchange_rate: Decimal
    source_type: str
    description: str
    lines: list[LineIntent]   # فارغة مسموحة فقط بنمط DRAFT الابتدائي؛ ممنوعة عند POSTED
    source_id: int | None = None
```

**هذا التمثيل هو نفسه المُستخدَم لكلا نمطي الاستدعاء (أ يدوي / ب فوري)** — لا نوعان منفصلان. الفارق بين النمطين فقط بـ**كيف** يُبنى ومتى يُستهلَك (راجع §4).

---

## 3. عقود/API الـBoundary — الشكل النهائي

**اسم الوحدة (مسألة تسموية محسومة بالمعمارية §3، يُثبَّت هنا حرفياً)**: يبقى الملف `journal_edit.py` من حيث المسار، **لكن الدوال العامة المُصدَّرة تُعاد تسميتها** لإزالة كلمة "manual" من التوقيع العام حيث لم تعد دقيقة (التفاصيل الحرفية للأسماء تُحسَم عند الكتابة الفعلية، ليست قراراً معمارياً يستحق تجميداً هنا؛ المبدأ الملزم فقط: **لا اسم دالة عامة يحمل "manual" إن كانت ستُستدعى من مسار آلي**).

```python
# نمط أ — يدوي، DRAFT قابل للتعديل
def begin_entry(session, entry_date, currency_code, exchange_rate, source_type, description) -> JournalEntry
    # ينشئ JournalEntry(status=DRAFT)، يحجز ref_no فوراً من الـSequence (namespace يُحدَّد حسب source_type)، session.add + flush، يعيد الكائن.

def add_line(session, entry, account_id, debit_raw=0, credit_raw=0, debit_base=0, credit_base=0,
             line_currency_code=None, line_exchange_rate=None) -> JournalLine
    # ensure_editable(entry) أولاً، ثم validate_line(...) (§5)، ثم JournalLine(...)، session.add+flush، append لـentry.lines.

def remove_line(session, line) -> None
    # ensure_editable(line.entry) أولاً، ثم session.delete(line).

def post(session, entry) -> JournalEntry
    # ensure_editable(entry)، validate_entry(entry) (§5: وجود سطر واحد، توازن)، entry.status = POSTED، flush، يعيد الكائن.

# نمط ب — فوري
def post_immediate(session, intent: AccountingIntent) -> JournalEntry
    # يبني JournalEntry(status=POSTED مباشرة) + كل JournalLine من intent.lines
    # يُطبِّق validate_line لكل سطر ثم validate_entry على المجموع (**نفس الدالتين المُستخدَمتين بالنمط أ حرفياً — لا نسخة ثانية**)
    # يحجز ref_no من نفس الـSequence، namespace حسب source_type، session.add+flush، يعيد الكائن.

# عكس عام
def reverse(session, original_entry, reversal_date, description=None) -> JournalEntry
    # يتحقق: original.status==POSTED، original.is_reversal_of is None،
    #         لا يوجد قيد آخر بـis_reversal_of=original.id مسبقاً،
    #         reversal_date >= original.entry_date (§7 بالمعمارية)
    # يبني JournalEntry(status=POSTED, is_reversal_of=original.id) بأسطر معكوسة (raw وbase يتبادلان حرفياً، currency/rate يُنسَخان حرفياً)
    # يحجز ref_no من namespace "JV-REV" تحديداً (منفصل بنيوياً، §1)
```

**دالتا التحقق المشتركتان (لا نسخ، يُستدعيان من كل الأماكن أعلاه)**:
```python
def _validate_line(session, account_id, debit_raw, credit_raw, debit_base, credit_base,
                    line_currency_code, line_exchange_rate) -> None
    # يرفع JournalEditError عند: حساب غير موجود، غير نشط، Group،
    #   (debit_raw>0 and credit_raw>0)، (debit_raw==0 and credit_raw==0)،
    #   أي قيمة سالبة (raw أو base)، line_exchange_rate<=0 إن كان موجوداً (بصرف النظر عن استخدامه لاشتقاق base)

def _validate_entry_balance(entry) -> None
    # يرفع JournalEditError عند: لا أسطر إطلاقاً، أو Σdebit_base != Σcredit_base
```

**ملاحظة إلزامية تصميمية**: `_validate_line` **لا تتحقق أبداً** من `debit_base == debit_raw × rate` — تتحقق فقط من الخصائص المذكورة صراحة أعلاه، تطبيقاً حرفياً لقرار §4/§5-ج المعماري.

---

## 4. قواعد إنشاء DRAFT وحجز الترقيم — التطبيق الحرفي لـ§5-ب المعمارية

- `begin_entry()` **يحجز `ref_no` فوراً** كخطوة داخلية أولى (قبل أي شيء آخر بالدالة) — استدعاء واحد للـSequence بالـnamespace المناسب.
- **لا استرجاع (Rollback) للرقم المحجوز عند حذف DRAFT** — لو استُدعي `session.rollback()` بمستوى الـTransaction قبل `commit` أصلاً (لم يُثبَّت الرقم بعد)، فالرقم **يُفقَد فعلياً كفجوة** (السلوك المقبول صراحة بالقرار المعماري) — **لا آلية "تحرير رقم" أو "إعادة استخدام" تُبنى إطلاقاً**، بما يطابق "لا نعيد استخدام الأرقام المحجوزة" حرفياً.
- **`add_line`/`remove_line` لا يمسّان `ref_no` بأي شكل** — الرقم ثابت من لحظة `begin_entry()` حتى `post()`/العكس.

---

## 5. ترتيب التحقق (Validation Order) — دقيق، بلا غموض

**لكل سطر عند إضافته (نمط أ) أو ضمن الدفعة الكاملة (نمط ب)، بهذا الترتيب بالضبط**:
1. الحساب موجود (`session.get(Account, account_id) is not None`).
2. الحساب نشط (`account.is_active`).
3. الحساب ليس Group (`not account.is_group`).
4. حصرية مدين/دائن على `(debit_raw, credit_raw)` (ليس كلاهما موجباً معاً).
5. ليس كلاهما صفراً معاً (`(debit_raw, credit_raw) != (0, 0)`).
6. لا قيمة سالبة بأي من الأربعة (`debit_raw`، `credit_raw`، `debit_base`، `credit_base`).
7. `line_exchange_rate > 0` **إن كان غير `None`** (بصرف النظر عن استخدامه لاشتقاق `base` من عدمه).

**على مستوى القيد ككل، عند `post()`/`post_immediate()` (بعد نجاح كل الأسطر فردياً)**:
8. وجود سطر واحد على الأقل.
9. `Σdebit_base == Σcredit_base` (بدقة `money()` القياسية، ربع فلس/سنت كما بكل النظام).

**فشل أي خطوة يوقف العملية بالكامل فوراً** (`JournalEditError`)، **بلا Commit جزئي** — يتوافق مع §12 بتقرير Business Rules (Atomicity، لا حالة جزئية تُثبَّت).

---

## 6. خطة الهجرة دومين-تلو-دومين — تفصيل تنفيذي

**الترتيب (من الأقل خطورة للأعلى، كما بالمعمارية §11/§12)، مع خطوات كل دومين موحَّدة**:

### الخطوة الصفرية (تُنفَّذ مرة واحدة قبل أي هجرة دومين): Audit القيم (0,0)
استعلام قراءة فقط، بلا أي تعديل:
```sql
SELECT COUNT(*) FROM journal_lines WHERE debit = 0 AND credit = 0;
```
**النتيجة تحدد مباشرة**: صفر → خيار تعديل الـDB CHECK (§10 بالمعمارية) يبقى مفتوحاً بلا عائق بيانات. غير صفر → خيار الـDB CHECK يحتاج معالجة تلك الصفوف أولاً (Data Audit منفصل، خارج 3B-4)، والاعتماد المؤقت على Python-only (داخل الـBoundary، §5 أعلاه) يكفي عملياً لمنع أي (0,0) **جديد** بصرف النظر عن نتيجة هذا الـAudit. **هذا الـAudit يُنفَّذ فعلياً كأول خطوة تنفيذية عملية، لا يُؤجَّل — نتيجته مدخل مباشر لقرار لاحق، لا افتراض هنا.**

### لكل دومين (`opening_balances.py`/`opening_party_balances.py` → `journal_voucher_form.py` → `posting.py`+`invoice_cancel.py` → `settlements.py`):
1. **Characterization Baseline** (§12 بالمعمارية): تشغيل سيناريوهات تمثيلية على الكود **الحالي** (قبل أي تعديل)، تسجيل كل الحقول المذكورة (§12 هناك) بصيغة قابلة للمقارنة الآلية (JSON/CSV لكل سيناريو — التنسيق الدقيق تفصيل تنفيذي بسيط، لا قرار معماري).
2. **تحويل الاستدعاء الداخلي** لاستخدام `begin_entry`/`add_line`/`post` (إن كان الدومين DRAFT-first أصلاً، حالة `opening_*`/`journal_voucher_form.py`) أو `post_immediate` (حالة `posting.py`/`settlements.py`/`invoice_cancel.py`) بدل البناء المباشر لـ`entry.lines`/`JournalEntry(...)`.
3. **إعادة تشغيل نفس سيناريوهات الخطوة 1**، تسجيل النتائج **بعد** التعديل بنفس الصيغة.
4. **مقارنة آلية حرفية `BEFORE == AFTER`** — أي فرق واحد بأي حقل = توقف فوري، تشخيص السبب (خطأ بالتنفيذ الجديد، أو خطأ لم يُكتشَف بالـBaseline نفسه) قبل المتابعة.
5. **Full Regression (كل الملفات الحالية 30) + Fuzz (200) + اختبارات الدومين المخصَّصة** — يجب أن تبقى بنفس الأرقام تماماً.
6. **فقط بعد نجاح 1-5 معاً**: الدومين "مُهاجَر"، ينتقل التنفيذ للدومين التالي.

**لا هجرة دومينين معاً بنفس الدفعة** — الترتيب أعلاه إلزامي، لا اختصار.

---

## 7. Characterization Fixtures/Snapshots — التصميم التفصيلي

- **مصدر السيناريوهات**: الحالات الحدّية المُختبَرة أصلاً بكل مرحلة مغلقة (`test_opening_account_balances.py`، `test_opening_inventory.py`، `test_phase3b3_settlement_allocation.py`، `test_e2e_scenario.py`، إلخ) — **لا سيناريوهات جديدة تُخترَع لهذا الغرض تحديداً**، إعادة استخدام ما هو موجود ومُختبَر أصلاً يكفي ويُفضَّل (يقلّل مخاطر اختلاف تفسير السيناريو نفسه بين "قبل" و"بعد").
- **الصيغة**: لكل سيناريو، دالة واحدة تبني الحالة (نفس دوال الإعداد الموجودة بالاختبارات الحالية)، ثم تستخرج قاموساً (`dict`) واحداً يحتوي كل حقول §12 بالمعمارية لكل `JournalEntry`/`JournalLine` ناتج، بترتيب ثابت (فرز حسب `id`).
- **التخزين المؤقت**: ملف JSON واحد لكل تشغيل (`characterization_before_<domain>.json` / `characterization_after_<domain>.json`) — تُقارَن آلياً (فرق نصي/بنيوي)، لا تُحفَظ بشكل دائم بالمستودع بعد اكتمال الهجرة (أداة تحقق مؤقتة لمرحلة الانتقال فقط، لا جزء من حزمة الاختبار الدائمة).

---

## 8. خطة الاختبارات التفصيلية لكل Migration

لكل دومين مُهاجَر: (أ) اختبار Characterization (§7) كخطوة منفصلة قابلة للتشغيل بمعزل · (ب) إعادة تشغيل ملفات الانحدار الحالية الخاصة بذلك الدومين تحديداً (بلا تعديل قيمها المتوقَّعة إلا بإثبات تغيّر محاسبي حقيقي مبرَّر، بنفس صرامة 3B-2/3B-3) · (ج) اختبار جديد مخصَّص للـBoundary نفسها (ملف واحد جديد، مثال: يغطي رفض Group/Inactive account صراحةً — فجوة تغطية مؤكَّدة سابقاً وغير مُختبَرة إطلاقاً اليوم، ورفض `rate<=0`، ورفض `(0,0)` على كل من نمط أ وب معاً) · (د) Full Regression + Fuzz الكاملين بعد كل دومين، لا فقط بنهاية 3B-4 كاملة.

---

## 9. معيار إغلاق 3B-4 الكامل (لا يُعلَن قبل تحقق الكل)

جدول الـSequence مبني ومُختبَر (Migration + Backfill إن احتيج ترحيل أرقام موجودة لأول مرة لجدول العدّاد — تفصيل يُحسَم عند التنفيذ الفعلي بناءً على القيمة الحالية الفعلية لآخر رقم بكل namespace) · كل الدوامينات الخمسة مُهاجَرة بنجاح الخطوات 1-6 بـ§6 · Characterization Diff نظيف للجميع · Full Regression (نفس 30 ملفاً أو أكثر) + Fuzz 200/200 خضراء بنفس الأرقام · اختبارات الـBoundary الجديدة (§8-ج) خضراء · نتيجة Audit (0,0) موثَّقة والقرار المترتب عليها (سواء تنفيذ CHECK جديد أو تأجيله) موثَّق بوضوح · `returns.py` لم يُلمَس (يبقى خارج 3B-4 كما تقرَّر) · `invoice_no` لم يُلمَس · لا Regression بنتائج 3B-1/2/3 بأي رقم واحد.

**لا "GREEN"/"COMPLETE" قبل تحقق كل ما سبق فعلياً ومُوثَّق برقم — بنفس صرامة تقارير إغلاق 3B-2/3B-3.**

---

**لا كود يُنفَّذ، لا Migration تُطبَّق فعلياً بهذا التسليم — تصميم فقط. بانتظار مراجعتك وإذن البدء بالتنفيذ الفعلي (بدءاً من Audit (0,0) وهجرة أول دومين).**
