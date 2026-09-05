# Phase 3B-3 — مواصفة تصميم: الأرصدة الافتتاحية للعملاء/الموردين + امتداد محرك التسوية

**الحالة: Phase 3B-3 — IMPLEMENTED / VERIFIED / CLOSED. التنفيذ الكامل
والتحقق (50/50 اختبار مخصص، 14/14 Migration، 30/30 Regression، 200/200
Fuzz) موثَّقان بـWORKFLOW.md §68. القرارات المعمارية بهذا المستند لم
تتغيّر بأثر رجعي لمجرد اختلاف تفاصيل الكود — أي تصحيح تقني اكتُشف أثناء
التنفيذ (انتقال استعلام `invoice_cancel.py`، فصل raw/base بـ
`_jline_party()`، إضافة Hardening constraints) موثَّق كتصحيح تنفيذي
بـWORKFLOW.md §68، لا كقرار معماري جديد هنا. هذا المستند يبقى وثيقة
القرار الأصلي كما اعتُمد.**

---

## §0 — ما هو موجود فعلاً بالكود اليوم (لا افتراضات)

فُحص الكود الفعلي مرتين منفصلتين (تقرير Code Archaeology + مراجعة إضافية
لـ`settlements.py`/`models.py` المنشورين). الخلاصة المهمة: **جزء من
البنية المطلوبة موجود فعلاً ولا يحتاج أي تعديل**:

- `AccountSubtype` (`GENERAL`/`CUSTOMER`/`SUPPLIER`) **موجود فعلاً**
  بـ`app/models.py` — لا حاجة لإضافته، القرار §1/§2 أدناه محقَّق مسبقاً.
- `Account.allow_reconciliation` **موجود فعلاً** — الحقل المستقل عن
  `subtype` الذي يُفعِّل التسوية صراحة.
- `app/services/settlements.py::_invoice_receivable_or_payable_account_id`
  **يتحقق فعلاً** من الشرطين معاً (`subtype in (CUSTOMER, SUPPLIER)`
  و`allow_reconciliation=True`) — هذا بالضبط نمط الإنفاذ المطلوب تعميمه
  على `OpeningPartyEntry` والتخصيصات الجديدة، لا نمط جديد يُخترَع.
- لا جدول `Customer`/`Supplier`/`Party` — العميل/المورد = حساب فرعي عبر
  `parties.py::get_or_create_party_account` (مطابقة نصية على `party_name`
  تحت نفس الأب). **يبقى كما هو تماماً — لا تعديل على `parties.py`.**
- `Settlement.invoice_id` **NOT NULL** اليوم، بلا عمود بديل — هذا هو
  القيد الذي تحلّه هذه المواصفة عبر `SettlementAllocation`.
- لا `remaining_amount`/`balance_due` مُخزَّن لأي فاتورة اليوم —
  `get_invoice_balance_due()` يحسبه ديناميكياً من `compute_invoice_totals()`
  ناقص `Σ(Settlement.amount_foreign)`. **نفس المبدأ يُطبَّق حرفياً على
  `OpeningPartyEntry`.**
- لا FIFO ولا allocation تلقائي لأي شيء اليوم — حتى فاتورة حقيقية
  واحدة، المستخدم يختار يدوياً عبر `post_receipt(invoice, ...)`.
- `test_settlement_tamper_resistance.py` يُثبت أن `Settlement` طبقة
  Append-Only مقصودة (لا `update_settlement`/`delete_settlement`
  بالكود إطلاقاً) — قيد يجب الحفاظ عليه حرفياً بـ`SettlementAllocation`
  الجديد أيضاً.
- `invoice_cancel.py` يرفض إلغاء أي فاتورة لها تسوية مرتبطة — **نفس
  القاعدة** تُطبَّق على عكس `OpeningPartyEntry` أدناه (§8).

---

## §1 — القرارات المعمارية غير القابلة للتفاوض

| # | القرار |
|---|---|
| 1 | ❌ لا `Customer`/`Supplier`/`Party` table |
| 2 | ✅ `AccountSubtype.GENERAL/CUSTOMER/SUPPLIER` (موجود فعلاً) + `allow_reconciliation` (موجود فعلاً) |
| 3 | ❌ لا فواتير وهمية للأرصدة الافتتاحية — `InvoiceLine.item_id` يبقى NOT NULL بلا تعديل |
| 4 | ✅ `OpeningPartyEntry` — جدول واحد، لا جدولين (Receivable/Payable) |
| 5 | ✅ `JournalEntry` **مستقل لكل** `OpeningPartyEntry` — لا قيد مُجمَّع لعدة أرصدة |
| 6 | ✅ نفس `opening_balance_clearing_account_id` المُعتمَد بـ3B-1/3B-2 — لا حساب Clearing جديد |
| 7 | ✅ `SettlementAllocation` — جدول جديد يربط تسوية واحدة بعدة أهداف |
| 8 | ✅ Exclusive Arc عبر عمودين NULL-able + `CHECK` — لا `target_type`/`target_id` |
| 9 | ✅ `Settlement.amount_foreign`/`settlement_rate` يبقيان — يمثلان إجمالي القبضة |
| 10 | ✅ `Settlement.invoice_id` **ينتقل** لـ`SettlementAllocation.invoice_id` — مع Migration/Backfill يحفظ التاريخ |
| 11 | ✅ Settlement = عملة تسوية واحدة؛ كل الأهداف المخصَّصة له بنفس العملة إلزامياً |
| 12 | ✅ FX يُحسَب لكل `SettlementAllocation` بمعدل الهدف الأصلي، والقيد يرحّل المجموع فقط |
| 13 | ✅ `JournalLine.debit_base`/`credit_base` هو مصدر الحقيقة المحاسبية دائماً — `SettlementAllocation.fx_amount` بيانات تشغيلية تُثبَت الاختبارات تطابقها معه، لا تحل محله |
| 14 | ❌ لا `remaining_amount` مُخزَّن — يُحسَب ديناميكياً دائماً |
| 15 | ✅ Partial + Multiple Allocation (فاتورة وحساب افتتاحي معاً بنفس القبضة) |
| 16 | ✅ الدفعة الزائدة **مسموحة** — تتحول لرصيد صريح (`Customer Credit`/`Supplier Debit`)، **لا** ترفض كما قرَّرنا سابقاً، **ولا** تبقى "Unapplied Cash" مجهولة |
| 17 | ✅ Refund workflow صريح ومنفصل عن Receipt/Payment |
| 18 | ❌ لا FIFO تلقائي — تخصيص صريح من المستخدم لحظة القبض/الدفع |
| 19 | ✅ `Settlement` و`SettlementAllocation` كلاهما Append-Only — لا تعديل، لا حذف |
| 20 | ❌ عكس `OpeningPartyEntry` مرفوض إن وُجد أي `SettlementAllocation` عليه |
| 21 | ❌ لا نلمس Invoice Engine / Inventory Engine / Journal Engine / `parties.py` إلا بالحد الأدنى الموصوف هنا |
| 22 | ✅ General Journal يبقى منفصلاً — اختيار حساب Customer/Supplier بقيد عام لا يحوّله تلقائياً لتسوية |
| 23 | ✅ Sales Order/Purchase Order — ميزات مستقبلية اختيارية، **خارج نطاق 3B-3 تنفيذياً** (مذكورة هنا فقط لعدم تعارض التسمية لاحقاً) |

---

## §2 — نموذج البيانات الكامل

### 2.1 — `Account` (models.py) — **لا تعديل**

`subtype`/`allow_reconciliation` موجودان فعلاً (§0). لا عمود جديد.

### 2.2 — `OpeningPartyEntry` (جديد)

```python
class OpeningPartyKind(str, enum.Enum):
    RECEIVABLE = "receivable"   # العميل مدين لنا
    PAYABLE = "payable"         # نحن مدينون للمورد

class OpeningPartyEntry(Base):
    __tablename__ = "opening_party_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"))
    party_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    kind: Mapped[OpeningPartyKind] = mapped_column(Enum(OpeningPartyKind))
    reference: Mapped[str] = mapped_column(String(100))       # "A" أو رقم فاتورة قديم من نظام سابق — نص حر، لا فريد
    original_amount_foreign: Mapped[float] = mapped_column(Numeric(14, 2))
    currency_code: Mapped[str] = mapped_column(String(3))
    exchange_rate: Mapped[float] = mapped_column(Numeric(14, 6), default=1)
    amount_base: Mapped[float] = mapped_column(Numeric(14, 2))  # original_amount_foreign × exchange_rate، محسوب مرة واحدة وقت الإدخال، لا يُعاد حسابه لاحقاً (نفس مبدأ InventoryMovement.unit_cost التاريخي)
    opening_date: Mapped[date] = mapped_column(Date)

    journal_entry: Mapped["JournalEntry"] = relationship()
    party_account: Mapped["Account"] = relationship()
```

**لا `remaining_amount`** (§1.14) — يُحسَب دائماً:
```
remaining = original_amount_foreign − Σ(SettlementAllocation.amount_foreign
                                        WHERE opening_party_entry_id = this.id)
```

**سبب استخدام `id` لا `reference` كمفتاح الربط بـ`SettlementAllocation`**:
`reference` نص حر غير فريد (المستخدم قد يكتب "فاتورة 100" لعميلين
مختلفين)، لا يصلح كمرجع علائقي — هو حقل عرض/تتبع بشري فقط، لا مفتاح.

### 2.3 — `Settlement` (تعديل)

```python
class Settlement(Base):
    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    # invoice_id يُحذَف من هنا — ينتقل لـSettlementAllocation (§2.4)
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"))
    kind: Mapped[str] = mapped_column(String(20))  # "receipt" | "payment" | "customer_refund" | "supplier_refund" (§4) — String(20) لا String(10): "customer_refund"/"supplier_refund" أطول من 10 أحرف (تصحيح Bilal — كانت ستُسبِّب فشل إدخال/truncation فعلي، لا مسألة تجميل)
    settlement_date: Mapped[date] = mapped_column(Date, default=date.today)
    currency_code: Mapped[str] = mapped_column(String(3))          # جديد — عملة القبضة/الدفعة كاملة (§1.11)
    amount_foreign: Mapped[float] = mapped_column(Numeric(14, 2))  # إجمالي القبضة — يبقى كما هو، معناه لم يتغيّر
    settlement_rate: Mapped[float] = mapped_column(Numeric(14, 6))
    fx_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)  # يبقى: صافي فرق الصرف المُجمَّع لكل القبضة (Σ كل allocation.fx_amount)
    party_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)  # **إلزامي — Invariant، لا توصية**: كل allocations بنفس settlement يجب أن تخص هذا الحساب بالضبط (§3/§4.2)

    journal_entry: Mapped["JournalEntry"] = relationship()
    allocations: Mapped[list["SettlementAllocation"]] = relationship(back_populates="settlement")
```

**لماذا `party_account_id` على `Settlement` لا فقط على كل `Allocation`؟**
قبضة واحدة تُوزَّع دائماً على أهداف **لنفس العميل/المورد** (لا معنى
لقبضة واحدة من "أحمد" تُطفئ فاتورة "خالد") — هذا Invariant محاسبي، لا
مجرد تحسين استعلام؛ تخزينه على `Settlement` يجعل فرضه بـ`CHECK`/تحقق
خدمة واحد، بدل التحقق من تطابق `party_account_id` عبر كل صف
`allocation` كل مرة. **تصحيح Bilal**: هذا العمود `NOT NULL` إلزامي
بالنموذج نفسه، وليس توصية تنفيذية — `Settlement` بلا `party_account_id`
معناه محاسبياً غير مُحدَّد. الخدمة تتحقق إضافياً (طبقة ثانية فوق NOT
NULL) أن `allocation.target.party_account_id == settlement.party_account_id`
لكل تخصيص قبل الترحيل — رفض فوري لو حاول أحدهم تمرير هدف يخص حساباً
مختلفاً.

### 2.4 — `SettlementAllocation` (جديد)

```python
class SettlementAllocation(Base):
    __tablename__ = "settlement_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    settlement_id: Mapped[int] = mapped_column(ForeignKey("settlements.id"))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))
    opening_party_entry_id: Mapped[int | None] = mapped_column(ForeignKey("opening_party_entries.id"))
    amount_foreign: Mapped[float] = mapped_column(Numeric(14, 2))  # الجزء المخصَّص من settlement.amount_foreign لهذا الهدف — بعملة settlement نفسها (== عملة الهدف، مفروض بـ§1.11)
    fx_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)  # فرق صرف هذا التخصيص تحديداً؛ بيانات تشغيلية (§1.13) — JournalLine هو الحقيقة

    __table_args__ = (
        CheckConstraint(
            "(invoice_id IS NOT NULL AND opening_party_entry_id IS NULL) OR "
            "(invoice_id IS NULL AND opening_party_entry_id IS NOT NULL)",
            name="ck_settlement_allocation_exclusive_target",
        ),
    )

    settlement: Mapped["Settlement"] = relationship(back_populates="allocations")
    invoice: Mapped["Invoice | None"] = relationship()
    opening_party_entry: Mapped["OpeningPartyEntry | None"] = relationship()
```

### 2.5 — رصيد الدفعة الزائدة (Customer Credit / Supplier Debit) — محسومة نهائياً، لا جدول جديد

**قرار Bilal النهائي**: لا `CustomerCredit`/`SupplierDebit` كجدول
مستقل — الرصيد الدائن للعميل (أو المدين للمورد) هو **ببساطة رصيد حساب
`party_account_id` نفسه بدفتر الأستاذ**، لا أكثر.

**تصحيح محاسبي مهم من Bilal**: النسخة السابقة كانت تُنشئ **سطرين
دائنين منفصلين على نفس الحساب** (سطر للفاتورة المُخصَّصة + سطر
"للرصيد الفائض") — غير خاطئ بالمجموع، لكنه غير نظيف: دفتر الأستاذ
يُظهر حركتين تمثلان أثراً واحداً بنفس الحساب. **الصحيح: سطر واحد صافٍ
على `party_account_id`** يمثل الأثر المحاسبي الكلي، بينما `SettlementAllocation`
(الجزء المُخصَّص فقط) هو ما يشرح "كيف طُبِّق القبض" — يطابق مبدأ
المواصفة نفسه حرفياً: **`JournalLine` هو مصدر الحقيقة المحاسبية،
`SettlementAllocation` بيانات تشغيلية تفسيرية فقط** (§1.13):

```
Receipt = 7,000 (Invoice = 5,000، سعرها الأصلي 1.00)
سعر التسوية اليوم = 1.02

allocated_booked_base   = 5,000 × 1.00 = 5,000.00   (سعر الفاتورة الأصلي — يُطفئ التزاماً مُسجَّلاً بذلك السعر)
unallocated_booked_base = 2,000 × 1.02 = 2,040.00   (لا تُطفئ التزاماً سابقاً — تُسجَّل بسعر اليوم مباشرة، فائض جديد لا تاريخ محاسبياً له)

Dr Cash                         7,140.00   (= 7,000 × 1.02، سعر التسوية)
    Cr أحمد — Customer AR       7,040.00   (سطر واحد صافٍ base فقط، عبر _jline_base — نفس نمط FX/COGS الحالي)
    Cr فرق صرف (ربح)               100.00   (فرق الجزء المُخصَّص فقط: 5,000×1.02 − 5,000×1.00 = 100 — الجزء غير المخصَّص لا فرق صرف له لأنه لم يكن مُسجَّلاً بسعر آخر أصلاً)

SettlementAllocation:
    Invoice A   → amount_foreign = 5,000   (allocation عادي، له target)
(الـ2,000 الباقية: لا صف SettlementAllocation لها إطلاقاً — هي ببساطة
 Settlement.amount_foreign − Σ(allocations) = فرق يُحسَب ديناميكياً،
 لا يُخزَّن كصف "بلا هدف" يخالف Exclusive Arc)
```

**القاعدة العامة المُصحَّحة**: الجزء **المُخصَّص** من أي `Settlement`
يُسجَّل على `party_account_id` بسعر **الهدف الأصلي** (يُطفئ التزاماً
مسجَّلاً بذلك السعر بالضبط)؛ الجزء **غير المُخصَّص** (الفائض) يُسجَّل
بسعر **التسوية نفسه** (لا يوجد سعر تاريخي أصلاً يُطفئه) — **ولا فرق
صرف له بالتعريف** (سُجِّل بنفس السعر الذي قُبض به تماماً). كل هذا يُجمَع
بسطر `party_account_id` **واحد** عبر `_jline_base` (المبلغ الأساسي
جاهزاً، لا تحويل مزدوج — نفس نمط تفادي الخطأ التاريخي الموثَّق بـ3B-2).

`Refund` (§4.3) يُطفئ هذا الرصيد بقيد عكسي مباشر: `Dr party_account_id / Cr Cash`
(للعميل) أو العكس (للمورد) — الرصيد يعود لصفره تلقائياً عبر دفتر
الأستاذ نفسه، لا حاجة لأي "إغلاق" يدوي لسجل منفصل.

**قاعدة صريحة إلزامية — لا تفاوض** (إضافة Bilal الأخيرة، أهم نقطة
بهذا القسم): **`OpeningPartyEntry` ورصيد الدفعة الزائدة مفهومان
مختلفان تماماً، ولا يجوز خلطهما بالنموذج أو بالكود بأي شكل**:

| | `OpeningPartyEntry` | رصيد الدفعة الزائدة |
|---|---|---|
| المصدر | بيانات تاريخية من قبل بدء استخدام النظام | حركة `Settlement` فعلية حدثت **بعد** بدء الاستخدام |
| التمثيل | سجل مستقل بجدول `opening_party_entries` | لا سجل مستقل — رصيد GL محسوب فقط |
| القابلية للتسوية | هدف (`target`) صريح عبر `SettlementAllocation` | **ليس هدفاً** — لا `SettlementAllocation` يشير إليه إطلاقاً في 3B-3 (مؤجَّل، انظر أدناه) |
| القيد المحاسبي | `JournalEntry` مستقل، `source_type="opening_party_entry"` | سطر ضمن قيد `Settlement` عادي (`_jline_base` واحد صافٍ)، `source_type` يبقى `"receipt"`/`"payment"` |

**اختبار إلزامي مُقوَّى (تصحيح Bilal — لا يكفي عدم استدعاء الدالة)**:
يجب إثبات **حالة قاعدة البيانات فعلياً**، لا فقط مسار الكود:
```
قبل Receipt:  OpeningPartyEntry.count() == N
Receipt بدفعة زائدة (Invoice=5,000، Receipt=7,000)
بعد Receipt:  OpeningPartyEntry.count() == N        (بلا تغيير — لم يُنشأ أي صف جديد)
              Settlement.count() == سابقه + 1
              SettlementAllocation.count() == سابقه + 1 (تخصيص الفاتورة فقط — لا صف للفائض)
              رصيد GL لحساب العميل يعكس الفائض 2,040 (أو المبلغ المحسوب) بالضبط
```
هذا أقوى بكثير من إثبات "لا استدعاء لدالة X" — يثبت الأثر الفعلي على
قاعدة البيانات، لا فقط مسار التنفيذ الداخلي.

**"استخدام الرصيد الفائض لاحقاً بفاتورة جديدة" — مؤجَّل رسمياً
لـ3B-3-b** (قرار Bilal نهائي، لا نقطة مفتوحة): لأن ذلك مقاصة (offset)
بين رصيدين بنفس الحساب، لا "قبض جديد" ولا هدف تسوية — خارج شكل
Exclusive Arc الحالي تماماً، ويحتاج تصميم workflow مستقل بمرحلة لاحقة.
نطاق 3B-3 الفعلي يكتفي بـ:
1. الدفعة الزائدة **مسموحة** وتُرحَّل صراحة كسطر GL صافٍ واحد على حساب العميل (لا رفض، لا اختفاء، لا سطر ثانٍ مكرر).
2. **Refund فقط** (رد الفائض نقداً) مدعوم بـ3B-3 (§4 أدناه).

---

## §3 — قواعد تصنيف الحساب (إنفاذ بالخدمة، لا الواجهة فقط)

كل نقطة أدناه **موجودة فعلاً بجزء منها** (§0) — الجديد فقط تعميمها على
`OpeningPartyEntry` والتخصيصات:

- `OpeningPartyEntry.kind == RECEIVABLE` يتطلب `party_account.subtype == CUSTOMER`. أي مخالفة → `OpeningBalanceError` صريح.
- `OpeningPartyEntry.kind == PAYABLE` يتطلب `party_account.subtype == SUPPLIER`.
- `post_receipt` (القبض) يتطلب `party_account.subtype == CUSTOMER` — يُرفض على `SUPPLIER`/`GENERAL` **حتى لو مرَّرها المستخدم عبر واجهة تسمح بذلك خطأً** — نفس نمط `_invoice_receivable_or_payable_account_id` الحالي حرفياً.
- `post_payment` (الدفع) يتطلب `party_account.subtype == SUPPLIER` — يُرفض على `CUSTOMER`/`GENERAL`.
- `allow_reconciliation == True` شرط إضافي إلزامي في كل الحالات أعلاه (كما بالفواتير اليوم).
- General Journal (`add_manual_line`/`post_manual_entry`) **لا يفرض أي تحقق تسوية** — اختيار حساب Customer بقيد عام يبقى قيداً عادياً، لا Settlement (§1.22).

---

## §4 — تدفقات العمليات

### 4.1 — إنشاء رصيد افتتاحي (Receivable أو Payable)

```
post_opening_party_entry(session, party_account_id, kind, reference,
                          amount_foreign, currency_code, exchange_rate,
                          opening_date) -> OpeningPartyEntry
```

خطوات (بنفس ترتيب `post_opening_inventory`/`post_opening_account_balances`،
لا commit/rollback داخل الخدمة):
1. تحقق `party_account` موجود ونشط.
2. تحقق `kind` يطابق `subtype` (§3).
3. تحقق `amount_foreign > 0` (**قرار Bilal النهائي، محسوم**: لا صفر
   هنا مطلقاً — بخلاف `unit_cost=0` بتكلفة المخزون التي لها معنى واقعي
   (بضاعة موجودة بقيمة تاريخية صفرية)، رصيد افتتاحي بقيمة صفر لا يمثل
   ديناً أصلاً؛ لا تشابه بين الحالتين رغم التشابه السطحي).
4. حساب `amount_base = money(amount_foreign × exchange_rate)`.
5. إنشاء `JournalEntry` **مستقل** (`source_type="opening_party_entry"`،
   `source_id` = **يُحدَّد لاحقاً كـ`OpeningPartyEntry.id` نفسه بعد إنشائه**
   — لاحظ الفرق عن 3B-2: هناك القيد يُنشَأ أولاً ثم الحركات تُشير إليه؛
   هنا كل شيء 1:1، فمن الأنظف أن يُنشأ `OpeningPartyEntry` أولاً ثم القيد
   يُشير إليه، أو كلاهما بنفس الـflush كـ3B-1 — **تفصيل تنفيذي بسيط لا
   يغيّر أي Invariant، يُترَك للتنفيذ الفعلي**).
6. سطرا القيد:
   - `RECEIVABLE`: `Dr party_account_id` / `Cr opening_balance_clearing_account_id`
   - `PAYABLE`: `Dr opening_balance_clearing_account_id` / `Cr party_account_id`
   بقيمة `amount_base`، بنفس أسلوب تفادي التحويل المزدوج المُتَّبع بـ3B-2 (exchange_rate=1 للسطر لأن `amount_base` محوَّل مسبقاً).
7. **لا Idempotency على مستوى الشركة هنا** (بخلاف 3B-1/3B-2 عمداً) — كل
   `OpeningPartyEntry` مستقل تماماً (قرار §1.5). **`reference` حقل
   وصفي/تتبعي بحت — قرار Bilal النهائي: بلا `UNIQUE constraint`
   إطلاقاً.** السبب: `reference` ليس هوية المستند بالنظام —
   `OpeningPartyEntry.id` هو المعرّف الحقيقي الوحيد المُستخدَم بكل
   FKs/`SettlementAllocation`؛ `reference` نص حر قد يتكرر منطقياً
   (نفس رقم فاتورة قديمة بنظامين مختلفين لعميلين، أو وصف نصي متكرر
   عمداً)، وفرض `UNIQUE` عليه قد يمنع حالة إدخال صحيحة فعلياً. منع
   التكرار غير المقصود (لو احتيج لاحقاً) يكون بتحذير UI اختياري، لا
   قيد قاعدة بيانات صارم.

### 4.2 — Receipt/Payment بتخصيص متعدد

```
post_receipt(session, party_account_id, amount_foreign, currency_code,
             settlement_rate, settlement_date, cash_account_id,
             allocations: list[AllocationInput]) -> JournalEntry
# AllocationInput = invoice_id XOR opening_party_entry_id + amount_foreign
```

خطوات:
1. تحقق `party_account.subtype == CUSTOMER` (أو `SUPPLIER` لـ`post_payment`) و`allow_reconciliation`.
2. تحقق `Σ(allocations.amount_foreign) <= amount_foreign` (المجموع **لا يتجاوز** القبضة — المساواة غير إلزامية بعد قرار السماح بالفائض §1.16؛ الفرق يُرحَّل كسطر GL صافٍ، لا رفض).
3. لكل `allocation`: تحقق Exclusive Arc، تحقق أن الهدف يخص **نفس** `party_account_id` (يطابق `Settlement.party_account_id` الإلزامي — §2.3)، تحقق `amount_foreign <= remaining_balance(target)`، تحقق `target.currency_code == settlement.currency_code` (§1.11).
4. لكل allocation حقيقي (له target): `booked_base_i = money(allocation.amount_foreign × target_rate_i)` (§5)، `allocation.fx_amount = money(allocation.amount_foreign × settlement_rate) − booked_base_i`. للجزء غير المخصَّص (إن وُجد): `unallocated_foreign = amount_foreign − Σ(allocations.amount_foreign)`، `unallocated_booked_base = money(unallocated_foreign × settlement_rate)` (**لا فرق صرف له بالتعريف** — مُسجَّل بسعر التسوية نفسه، §2.5).
5. **(تصحيح Bilal — سطر صافٍ واحد لا سطرين)** بناء `JournalEntry` واحد:
   - سطر الصندوق: `Dr/Cr cash_account_id` بقيمة `amount_foreign × settlement_rate`.
   - سطر `party_account_id` **واحد فقط**، عبر `_jline_base`: `Σ(booked_base_i لكل allocation) + unallocated_booked_base` (§2.5) — لا سطر مكرر على نفس الحساب مهما تعدد التخصيصات.
   - سطر فرق الصرف المُجمَّع (إن `total_fx = Σ(allocation.fx_amount) != 0`، الجزء غير المخصَّص لا يُساهم فيه أبداً).
6. إنشاء `Settlement` (بـ`party_account_id` الإلزامي) + صف `SettlementAllocation` لكل allocation **حقيقي فقط** (له target) — **لا صف لأي جزء غير مخصَّص** (يخالف Exclusive Arc، ولا حاجة له: يُحسَب ديناميكياً كـ`Settlement.amount_foreign − Σ(SettlementAllocation.amount_foreign)`).

### 4.3 — Refund (رد رصيد دائن/مدين)

```
post_customer_refund(session, party_account_id, amount_foreign,
                      currency_code, refund_rate, refund_date,
                      cash_account_id) -> JournalEntry
post_supplier_refund(session, party_account_id, amount_foreign,
                      currency_code, refund_rate, refund_date,
                      cash_account_id) -> JournalEntry
```

**تصحيح جوهري من Bilal — لا يكفي رصيد الحساب الإجمالي**: حساب العميل
قد يحمل أرصدة بعملات مختلفة في آن واحد (مثال: USD credit = 2,000 و
EUR credit = 1,000 بنفس الحساب `party_account_id`). **رصيد `get_account_statement`
الحالي يُظهر القيمة بالعملة الأساسية فقط (`debit_base`/`credit_base`)
— لا يفرّق بين مصادر العملات، وبالتالي لا يصلح لتحديد "كم دولار متاح
تحديداً" بمعزل عن اليورو.** هذا يتطلب دالة جديدة **لم تكن موجودة**
(تأكيد بالفحص المباشر لـ`app/reports/ledger.py`).

**تصحيح ثانٍ وأهم — القيمة الدفترية التاريخية موجودة فعلاً، ويجب عدم
تجاهلها (مراجعة Bilal الأخيرة)**: العبارة السابقة "لا سعر تاريخي أصلي
لهذا الرصيد" كانت **خاطئة**. الرصيد الفائض نشأ فعلياً من `JournalLine`
حقيقي بقيمة `base` محدَّدة وقت إنشائه (§2.5: `unallocated_booked_base
= unallocated_foreign × settlement_rate` **وقتها**) — عدم وجود جدول
`CustomerCredit` منفصل **لا يعني** غياب قيمة تاريخية؛ دفتر الأستاذ
نفسه يحتفظ بها ضمنياً عبر كل الأسطر المُرحَّلة على ذلك الحساب بتلك
العملة. تجاهل هذه القيمة عند Refund يُخرج مبلغاً `base` مختلفاً عمّا
كان مسجَّلاً فعلياً بلا تفسير محاسبي — خطأ حقيقي، لا تبسيطاً مقبولاً.

```
get_party_currency_balance(session, party_account_id, currency_code,
                            as_of_date=None) -> PartyCurrencyBalance
# PartyCurrencyBalance = dataclass(foreign_balance: Decimal, base_balance: Decimal)
```

**تعريف صريح لا يعتمد على نوع الطرف — دالة محايدة** (تصحيح Bilal —
لا يجوز أن يعتمد التنفيذ على مقارنة الرقم بالمبلغ فقط بلا تحقق من
طبيعة/إشارة الرصيد):

```
foreign_balance = Σ(credit) − Σ(debit)     # على JournalLine الخام لتلك العملة تحديداً
base_balance    = Σ(credit_base) − Σ(debit_base)
```

تُحسَب مباشرة من `JournalLine.debit`/`.credit` **الخام (لا `_base`)**
لـ`foreign_balance`، ومن `JournalLine.debit_base`/`.credit_base`
لـ`base_balance` — **بنفس الفلترة**:
`COALESCE(JournalLine.line_currency_code, JournalEntry.currency_code) == currency_code`
وبقيود `POSTED` فقط — استعلام واحد يعيد كلا الرقمين معاً من نفس الصفوف
(لا استعلامان منفصلان قد يتعارضان). **استعلام جديد مستقل عن
`get_account_statement` تماماً**، لا تعديل عليه (يبقى كما هو، لا يزال
يخدم غرضه — تقرير موحَّد بالعملة الأساسية — بلا مساس).

**قاعدة الإشارة الإلزامية** (تصحيح Bilal — يجب فحصها صراحة قبل أي
Refund، لا مجرد مقارنة قيمة مطلقة):
- `Customer Refund`: صحيح فقط إذا `foreign_balance > 0` (رصيد **دائن**
  حقيقي لصالح العميل). `foreign_balance <= 0` → رفض فوري
  (`SettlementError`) — العميل ببساطة لا رصيد فائض له بهذه العملة، بصرف
  النظر عن أي رقم آخر.
- `Supplier Refund`: صحيح فقط إذا `foreign_balance < 0` (رصيد **مدين**
  لنا لدى المورد، بنفس اتفاقية الإشارة أعلاه المطبَّقة على حساب مورد).
- `carrying_rate = abs(base_balance) / abs(foreign_balance)` — القيمة
  المطلقة تُستخدَم للنسبة فقط، **بعد** التأكد من صحة الإشارة أعلاه —
  لا يُستبدَل فحص الإشارة بمقارنة قيم مطلقة أبداً.

**القاعدة المحاسبية لـRefund — متوسط مرجَّح ضمني للقيمة الدفترية، لا
FIFO ولا تجاهل**:

```
carrying_rate = abs(base_balance) / abs(foreign_balance)
refund_booked_base = money(refund.amount_foreign × carrying_rate)
refund_new_base     = money(refund.amount_foreign × refund_rate)
refund_fx = refund_new_base − refund_booked_base   # موجب = خسارة صرف للعميل عند الرد (دفعنا أكثر مما كان مسجَّلاً)
```

**مثال (رقم Bilal بالضبط)**: `foreign_balance=2,000`, `base_balance=2,040`
→ `carrying_rate=1.02`. Refund `1,000 @ refund_rate=1.03`:
`refund_booked_base = 1,020.00`, `refund_new_base = 1,030.00`,
`refund_fx = 10.00` (خسارة).

القيد:
```
Dr party_account_id       refund_booked_base    (يُخفِّض الدائن بقيمته الدفترية الفعلية — لا القيمة الجديدة)
Dr/Cr فرق صرف              |refund_fx|            (خسارة إن موجب، ربح إن سالب — نفس إشارة _post_settlement الحالية)
    Cr cash_account_id     refund_new_base        (النقد الفعلي المدفوع بسعر اليوم)
```
**Supplier Refund — اتجاه فرق الصرف صراحة (تحذير Bilal الأخير، لا
"نفس المنطق معكوساً" بلا تفصيل)**: `refund_fx = refund_new_base −
refund_booked_base` بنفس المعادلة تماماً، **لكن معناها المحاسبي
ينعكس بين الطرفين**: موجب = خسارة صرف لصالح العميل عند `Customer
Refund` (المؤسسة دفعت أكثر مما كان مسجَّلاً)، بينما نفس الإشارة الموجبة
= **ربح صرف للمؤسسة** عند `Supplier Refund` (المؤسسة **استلمت** من
المورد أكثر مما كانت تسجّله كمدين له). مثال رقمي كامل — Supplier Debit
بقيمة دفترية 1,020، رد المورد `1,000 @ refund_rate=1.03`:
```
refund_booked_base = 1,020.00
refund_new_base     = 1,030.00   (النقد المُستلَم فعلياً من المورد)
refund_fx            = 10.00      (موجب — لكنه ربح صرف هنا، لا خسارة)

Dr Cash                 1,030.00
    Cr Supplier — party_account_id   1,020.00   (القيمة الدفترية)
    Cr FX Gain                          10.00   (وليس Dr FX Loss كما بحالة العميل)
```
**القاعدة الحاسمة للتنفيذ**: اتجاه (`Dr`/`Cr`) سطر فرق الصرف يعتمد على
**نوع الطرف** (`customer_refund` مقابل `supplier_refund`)، لا على
إشارة `refund_fx` وحدها بمعزل عن نوع العملية — نفس الإشارة الرياضية
تُترجَم لأثر محاسبي معاكس تماماً حسب الطرف. لا يُطبَّق "نفس القيد
معكوس الاتجاهين فقط (Dr↔Cr) لكل الأسطر بالتساوي" بلا فحص هذه النقطة
تحديداً — الخطأ الشائع المحتمل الذي حذَّر منه Bilal.

(بقية القيد للمورد: نفس المنطق معكوساً — `Cr party_account_id` بالقيمة الدفترية، `Dr cash_account_id` بالقيمة الجديدة، كما بالمثال أعلاه).

- القاعدة الكاملة: `refund.amount_foreign <= abs(foreign_balance)` **بعد** التحقق من صحة الإشارة أعلاه أولاً — **مطابقة عملة الاسترداد بعملة الرصيد المُراد ردّه بالضبط**، لا الرصيد الإجمالي متعدد العملات.
- **لماذا هذا لا يحتاج FIFO ولا `CustomerCredit` جديد**: `carrying_rate` متوسط مرجَّح **ضمني** مُشتَق مباشرة من مجموعي `foreign`/`base` الموجودين أصلاً بـGL — لا حاجة لمعرفة "أي قبضة بالتحديد" تُردّ، ولا لتتبع كل قبضة بمعزل. **توضيح دقيق (تصحيح Bilal — تجنّب الالتباس)**: هذا **مبدأ مشابه من حيث الفكرة** لـ`average cost` بمحرك المخزون (3B-2) — رقم واحد مُشتَق ديناميكياً بدل تتبع تفصيلي — **وليس** إعادة استخدام حرفية لنفس الخوارزمية أو نفس الطبقات؛ `carrying_rate` هنا مُشتَق من رصيد `JournalLine` مباشرة، لا من طبقات `InventoryMovement` إطلاقاً — لا علاقة كودية بين الاثنين، فقط تشابه مفهومي.
- `Settlement.kind = "customer_refund"`/`"supplier_refund"` — **لا
  `SettlementAllocation` لعملية Refund** (لا يوجد "هدف" يُطفَأ، هي
  إعادة نقد مباشرة) — نقطة تصميم بسيطة: `Settlement.allocations` قد
  تكون فارغة لهذا النوع تحديداً، وهذا مقبول (Refund ليس تسوية طرف
  ثالث، هو حركة صندوق بحتة).

---

## §5 — صيغ فرق الصرف (FX)

**تحديد صريح لمصدر `target_rate`** (تصحيح Bilal — لا يُترَك للتفسير
وقت التنفيذ):

```
target_rate =
    invoice.exchange_rate                    إذا كان الهدف Invoice
    opening_party_entry.exchange_rate        إذا كان الهدف OpeningPartyEntry
```

لكل `SettlementAllocation` (بهدف حقيقي فقط — لا الجزء غير المخصَّص، §2.5/§4.2):
```
new_base    = money(allocation.amount_foreign × settlement.settlement_rate)
booked_base = money(allocation.amount_foreign × target_rate)   # حسب الجدول أعلاه بالضبط
allocation.fx_amount = new_base − booked_base
```
`total_fx = Σ(allocation.fx_amount)` — القيمة الوحيدة التي تُرحَّل
كسطر فرق صرف واحد بالقيد الفعلي (`_jline_base`، تماماً كـ`_post_settlement`
الحالية). **اختبار إلزامي (§1.13)**: `total_fx == posted JournalLine
fx amount` بالضبط، بأي عدد من الأهداف وبأي مزيج فاتورة/رصيد افتتاحي.

---

## §6 — قواعد Reverse

- `OpeningPartyEntry`: عكس مرفوض إن وُجد أي `SettlementAllocation`
  يشير إليه (`opening_party_entry_id`) — فحص واحد بسيط، **لا حاجة لفحص
  "معزول لكل مستودع/زوج" كما بـ3B-2** لأن كل `OpeningPartyEntry` وحدة
  مستقلة بقيدها الخاص أصلاً (§1.5 يحسم هذا الفارق عمداً).
- `Settlement`/`SettlementAllocation`: **لا `reverse` مباشر إطلاقاً** —
  Append-Only بالكامل (§1.19). تصحيح خطأ تخصيص = قيد عكسي على قيد
  التسوية بالكامل (نفس محرك `reverse_manual_entry` العام)، ثم قبضة
  جديدة صحيحة. لا دالة `reverse_settlement()` مخصصة تُكتَب.

---

## §7 — خطة Migration + Backfill (إلزامية، بلا فقد بيانات)

1. إنشاء `opening_party_entries`، `settlement_allocations` (بالحقول أعلاه).
2. إضافة `currency_code`، `party_account_id` لـ`settlements` (nullable مؤقتاً، تُملأ بالخطوة 4).
3. **Backfill قبل حذف أي عمود قديم**: لكل صف `Settlement` قديم (له `invoice_id` NOT NULL اليوم) —
   - أنشئ صف `SettlementAllocation(settlement_id=s.id, invoice_id=s.invoice_id, amount_foreign=s.amount_foreign, fx_amount=s.fx_amount)`.
   - املأ `settlements.currency_code` من `s.invoice.currency_code`، و`settlements.party_account_id` من الفاتورة (`_invoice_receivable_or_payable_account_id`).
4. **تحقق ما بعد Backfill إلزامي قبل أي حذف**: `count(settlements) == count(settlement_allocations)` بالضبط (كل تسوية قديمة = تخصيص واحد بالضبط)، و`Σ(SettlementAllocation.amount_foreign) == Σ(Settlement.amount_foreign)` للتحقق من عدم فقد أي مبلغ.
5. فقط بعد تحقق الخطوة 4 فعلياً (اختبار مباشر، لا افتراض): حذف `settlements.invoice_id` كعمود.
6. Regression الكامل لكل اختبارات Settlement الحالية (`test_settlement_fx.py`، `test_settlement_tamper_resistance.py`، `test_invoice_cycle_customer_supplier.py`، `test_account_reconciliation_rules.py`، `test_allow_reconciliation_enforcement.py`، `test_ui_settlement_and_cancel.py`) **يجب أن يبقى أخضر 100% بلا تعديل على expected values** — لو احتاج أي رقم تعديلاً، هذا خلل بالـMigration لا تصحيحاً يُقبَل بصمت (نفس درس 3B-2 §65/§67 حرفياً).

---

## §8 — نطاق مستبعد صراحة من تنفيذ 3B-3 الأول

- Sales Order / Purchase Order (§1.23) — تسمية فقط، لا تنفيذ.
- استخدام Customer Credit/Supplier Debit كخصم مباشر بفاتورة مستقبلية (§2.5) — مؤجَّل لمرحلة فرعية لاحقة بعد تصميم آلية مقاصة منفصلة.
- FIFO أو أي allocation مساعد تلقائي.
- أي تعديل على `Invoice`/`InvoiceLine`/`InventoryMovement`/`item_queries.py`/`posting.py` (المخزون) خارج ما هو مذكور صراحة أعلاه.

---

## §9 — الاختبارات المطلوبة (قائمة كاملة، مطابقة لطلب §33 حرفياً)

**Account Classification**: Customer صحيح · Supplier صحيح · General لا يُعامَل كعميل/مورد · Customer يرفض Payable opening · Supplier يرفض Receivable opening.

**OpeningPartyEntry**: إنشاء رصيد عميل · إنشاء رصيد مورد · JournalEntry مستقل لكل سجل (لا تداخل بين A وB) · Opening Clearing صحيح رقمياً · Reverse مستقل لكل سجل · Reverse مرفوض بعد وجود Allocation.

**SettlementAllocation**: Allocation لفاتورة · Allocation لرصيد افتتاحي · Multiple Allocation بقبضة واحدة (فاتورة + رصيد افتتاحي معاً) · Partial Settlement · رفض تجاوز remaining · رفض mismatch عملة Settlement/Target · Exclusive Arc (رفض DB-level لصفين معاً أو لا شيء) عبر محاولة إدراج مباشرة تخالف CHECK.

**FX**: حساب FX لكل Allocation · Aggregate FX · تطابق Σ(allocation.fx_amount) مع JournalLine بالضبط · رفض Mixed Currency Targets بنفس Settlement.

**Overpayment**: Customer overpayment → **سطر GL صافٍ واحد** (لا سطرين) بالقيمة الصحيحة (allocated بسعر الهدف + unallocated بسعر التسوية، §2.5) · Supplier overpayment (نفس المنطق معكوساً) · نفس الأربعة للمورد · **اختبار حالة قاعدة بيانات فعلي (تصحيح Bilal — لا مسار-كود فقط)**: `OpeningPartyEntry.count()` قبل وبعد Receipt بدفعة زائدة **بلا تغيير**، `Settlement.count()`/`SettlementAllocation.count()` يزيدان بالمتوقَّع بالضبط (لا صف Allocation للجزء غير المخصَّص)، ورصيد GL للحساب يعكس الفائض بالقيمة الرقمية الصحيحة.

**Refund + القيمة الدفترية (تصحيح Bilal — أهم إضافة بهذه الجولة)**: `get_party_currency_balance()` يعيد `foreign_balance` **و** `base_balance` معاً من نفس الاستعلام (`= Σcredit − Σdebit` بكلتا العملتين، دالة محايدة لا تفرّق بين عميل/مورد بذاتها) · Refund بسعر مطابق للسعر الدفتري الضمني (`carrying_rate`) → `refund_fx == 0` بالضبط · Refund بسعر استرداد أعلى من `carrying_rate` → خسارة صرف محسوبة ومرحَّلة بالضبط (مثال Bilal حرفياً: `2,000 foreign / 2,040 base → carrying_rate=1.02`؛ `refund=1,000 @ 1.03 → booked=1,020, new=1,030, fx=10` خسارة) · نفس الاختبار بسعر أقل (ربح صرف) · تطابق `Dr party_account_id` بالقيد مع `refund_booked_base` بالضبط لا `refund_new_base` · رفض Refund أكبر من `abs(foreign_balance)` **لعملة الاسترداد تحديداً** لا الرصيد الإجمالي (اختبار حساب بعملتين مختلفتين معاً — USD credit + EUR credit بنفس الحساب، Refund بعملة واحدة لا يتأثر بالأخرى) · **جديد (تصحيح Bilal — فحص الإشارة، لا القيمة المطلقة فقط)**: رفض `Customer Refund` صراحة عندما `foreign_balance <= 0` (حساب عميل عليه دين لا رصيد فائض له) حتى لو كان الرقم المطلق كافياً ظاهرياً؛ ونفس الرفض المعكوس لـ`Supplier Refund` عندما `foreign_balance >= 0` · Oracle مستقل: `carrying_rate` يُحسَب يدوياً بالاختبار من نفس صفوف `JournalLine` الخام، لا استدعاء `get_party_currency_balance()` نفسها للتحقق من نفسها · **جديد (تحذير Bilal الأخير — اتجاه FX حسب نوع الطرف)**: اختبار `Supplier Refund` بـ`refund_fx` **موجب** يثبت أن القيد يرحّل `Cr FX Gain` (ربح، لا خسارة) — أي عكس اتجاه ما يحدث بنفس القيمة الموجبة لـ`Customer Refund` (`Dr FX Loss`) — تحديداً لمنع تفسير "نفس المعادلة إذن نفس الاتجاه" الخاطئ أثناء التنفيذ.

**Historical Data**: Migration فعلية على بيانات تشبه الإنتاج (فواتير + تسويات قديمة) · Backfill يطابق العدد والمجموع بالضبط · Regression الكامل للتسويات القديمة 100% أخضر بلا تعديل قيم.

**Append-Only**: منع تعديل/حذف `Settlement` (موجود، يبقى) · منع تعديل/حذف `SettlementAllocation` (جديد، يُضاف لنفس ملف `test_settlement_tamper_resistance.py`).

**Regression عام**: 29/29 + 200/200 الحاليين يبقيان أخضرين بلا تعديل — 3B-1/3B-2 غير متأثرتين إطلاقاً.

---

## §10 — القرارات النهائية على النقاط المفتوحة السابقة (محسومة بالكامل — لا نقاط مفتوحة متبقية)

1. **§2.5 (استخدام الرصيد الفائض بفاتورة مستقبلية)**: **مؤجَّل رسمياً
   لـ3B-3-b** — قرار Bilal نهائي. نطاق 3B-3 الحالي: دفعة زائدة مسموحة
   (تُرحَّل كرصيد GL صريح على حساب الطرف) + Refund فقط.
2. **§4.1 نقطة 7 (تفرّد `reference`)**: **لا `UNIQUE constraint`** —
   قرار Bilal نهائي. `reference` وصفي/تتبعي بحت؛ `OpeningPartyEntry.id`
   هو المعرّف الحقيقي الوحيد. منع تكرار غير مقصود (إن احتيج لاحقاً)
   عبر تحذير UI اختياري، لا قيد قاعدة بيانات.
3. **§4.1 نقطة 3 (`amount_foreign = 0`)**: **`amount_foreign > 0`
   إلزامي دائماً، لا استثناء** — قرار Bilal نهائي. الفرق عن
   `unit_cost=0` بـ3B-2 محسوم صراحة: كمية مخزون بتكلفة صفر لها معنى
   واقعي (بضاعة موجودة، قيمتها التاريخية صفر)؛ رصيد افتتاحي بقيمة صفر
   لا يمثل ديناً أصلاً — لا تشابه حقيقي بين الحالتين.
4. **قاعدة جديدة أضافها Bilal صراحة (§2.5)**: `OpeningPartyEntry`
   ورصيد الدفعة الزائدة (Overpayment credit/debit) **لا يُخلَطان
   بالنموذج أو الكود بأي شكل** — أوَّلهما بيانات تاريخية بسجل مستقل
   وقيد `JournalEntry` مستقل خاص به، والثاني رصيد GL محسوب بحت ناتج عن
   حركة `Settlement` فعلية لاحقة. جدول المقارنة بـ§2.5 يوثّق الفارق
   بندياً، والاختبار الجديد بـ§9 يثبته برمجياً.

لا كود، لا Migration فعلية، لا تعديل على `models.py`/`settlements.py` تم
تنفيذه بهذا التسليم — هذا المستند فقط، بانتظار الضوء الأخضر النهائي
لبدء التنفيذ.

---

## §11 — سجل التصحيحات (الجولة الرابعة، بعد إغلاق §10)

بعد إغلاق النقاط الثلاث بـ§10، راجع Bilal المواصفة مرة أخيرة وطلب 4
تصحيحات تقنية (لا إعادة فتح لأي قرار مُغلَق سابقاً):

1. **§2.5/§4.2**: تصحيح محاسبي — سطر GL **واحد صافٍ** على `party_account_id`
   عند الدفعة الزائدة (لا سطران دائنان منفصلان على نفس الحساب)؛ الجزء
   المُخصَّص يُسجَّل بسعر الهدف الأصلي، الجزء غير المخصَّص بسعر التسوية
   نفسه (لا فرق صرف له).
2. **§2.3**: `Settlement.party_account_id` **NOT NULL إلزامي بالنموذج**،
   لا توصية تنفيذية.
3. **§4.3**: Refund يتحقق من رصيد **العملة المطابقة تحديداً** عبر دالة
   جديدة `get_party_currency_balance()`، لا الرصيد الإجمالي متعدد
   العملات (`get_account_statement` الحالي لا يكفي لهذا — بالعملة
   الأساسية فقط).
4. **§5**: `target_rate` محدَّد صراحة بجدول (Invoice → `invoice.exchange_rate`،
   OpeningPartyEntry → `opening_party_entry.exchange_rate`) — لا مجال
   لتفسير مختلف وقت التنفيذ.
5. **§9**: اختبار "عدم الخلط" (`OpeningPartyEntry` ↔ Overpayment) عُزِّز
   ليثبت حالة قاعدة البيانات فعلياً (عدد الصفوف قبل/بعد)، لا فقط عدم
   استدعاء دالة معيّنة.

كل التصحيحات أعلاه طُبِّقت بالأقسام المذكورة. **لا نقطة مفتوحة متبقية
بهذا المستند** بعد هذه الجولة، حسب تأكيد Bilal الصريح ("إذا لم يتغيّر
أي قرار معماري آخر، أعتبر §2.5 وباقي المواصفة جاهزة للانتقال إلى
التنفيذ والMigration").

---

## §12 — سجل التصحيحات (الجولة الخامسة — تصحيح محاسبي جوهري في §4.3)

بعد §11، راجع Bilal §4.3 تحديداً ووجد خطأً محاسبياً حقيقياً في
الصياغة السابقة، لا تفصيلاً أسلوبياً:

**الخطأ**: النسخة السابقة افترضت أن "لا وجود جدول `CustomerCredit`
مستقل" يعني "لا قيمة دفترية تاريخية للرصيد الفائض"، وبنت على ذلك قراراً
بترحيل Refund كاملاً بسعر اليوم (`refund_rate`) بلا أي فرق صرف. **هذا
خطأ محاسبي فعلي**: الرصيد الفائض نشأ من `JournalLine` حقيقي بقيمة
`base` مُسجَّلة وقت نشوئه (§2.5) — غياب جدول تفصيلي منفصل لا يعني غياب
القيمة التاريخية؛ **دفتر الأستاذ نفسه يحملها ضمنياً**. ترحيل Refund
بسعر اليوم بلا مقارنة بالقيمة الدفترية الفعلية كان سيُخرج من حساب
العميل مبلغاً `base` مختلفاً عمّا كان مسجَّلاً فعلياً، بلا أي تفسير
محاسبي لذلك الفارق — قيد غير متوازن اقتصادياً رغم توازنه الحسابي.

**التصحيح**: `get_party_currency_balance()` تعيد الآن `foreign_balance`
**و**`base_balance` معاً؛ نسبتهما (`carrying_rate`) هي متوسط مرجَّح
**ضمني** لكل ما تراكم بتلك العملة على ذلك الحساب — **مبدأ مطابق تماماً
لـaverage cost بمحرك المخزون (3B-2)**، منقول هنا بلا أي بنية جديدة (لا
FIFO، لا `CustomerCredit` منفصل، لا تتبع كل قبضة بمعزل). الفرق بين
القيمة الدفترية (`carrying_rate`) والقيمة الجديدة (`refund_rate`)
يُرحَّل كفرق صرف صريح بقيد Refund نفسه — تماماً كأي تسوية أخرى بالنظام.

هذا يحسم §4.3 بالكامل. **لا نقطة مفتوحة متبقية بالمستند** بعد هذه
الجولة أيضاً.

---

## §13 — سجل التصحيحات (الجولة السادسة والأخيرة — إصلاحان تقنيان، لا معماريان)

بعد §12، راجع Bilal النسخة الكاملة وأكَّد إغلاق كل القرارات المعمارية
(23 قراراً)، مع إصلاحين تقنيين محدَّدين فقط قبل GREENLIGHT:

1. **`Settlement.kind` (§2.3) — كان سيُسبِّب فشلاً فعلياً**: `String(10)`
   لا يستوعب `"customer_refund"`/`"supplier_refund"` (15/15 حرفاً).
   صُحِّح لـ`String(20)`. هذا لم يكن تفصيلاً تجميلياً — كان سيُنتج
   `truncation` أو فشل إدخال حسب سلوك قاعدة البيانات، أي خطأ إنتاجي
   حقيقي لو نُفِّذ كما كان.
2. **`get_party_currency_balance()` (§4.3) — شرط إشارة صريح لا مقارنة
   قيمة مطلقة فقط**: أصبحت الدالة محايدة تماماً
   (`foreign_balance = Σcredit − Σdebit`، `base_balance` بنفس المنطق)
   بلا افتراض مسبق لنوع الطرف. `Customer Refund` يتطلب صراحة
   `foreign_balance > 0`، `Supplier Refund` يتطلب `foreign_balance < 0`
   — رُفض التنفيذ من الاعتماد على مقارنة رقمية فقط بلا تحقق من طبيعة
   الرصيد. `carrying_rate` أصبح `abs(base_balance)/abs(foreign_balance)`
   **بعد** التحقق من الإشارة، لا بديلاً عنه.
3. **توضيح صياغي (غير Blocking)**: استُبدلت عبارة "بنفس مبدأ average
   cost بمحرك المخزون حرفياً" بصياغة أدق — تشابه مفهومي فقط
   (متوسط مرجَّح ضمني من رصيد `JournalLine`)، لا إعادة استخدام كودية
   لمحرك `InventoryMovement`/3B-2 بأي شكل، لمنع أي التباس مستقبلي لدى
   من ينفذ الكود.

**لا قرار معماري من الـ23 المعتمدة أعيد فتحه بهذه الجولة.** المستند
الآن جاهز فعلياً للتنفيذ حسب تأكيد Bilal — بانتظار GREENLIGHT الحرفي
الأخير قبل أي كود أو Migration.
