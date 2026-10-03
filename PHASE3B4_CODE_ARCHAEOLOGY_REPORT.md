# PHASE3B4_CODE_ARCHAEOLOGY_REPORT
**Voucher Engine — فحص الواقع الحالي فقط، لا تصميم، لا كود، لا Migration.**

كل بند أدناه مُصنَّف صراحة: **[موجود فعلياً]** (وُجد بالكود، مع مرجع
الملف/السطر) · **[غير موجود]** (بُحث عنه ولم يُوجد) · **[يحتاج قراراً]**
(لا يمكن تحديده من الكود، قرار لك). لا خلط بين الثلاثة.

---

## 1. Existing Models

| Model | الغرض | PK | FKs | حقول مهمة | Status | العملة | المبلغ | Source | العلاقات | Constraints |
|---|---|---|---|---|---|---|---|---|---|---|
| `JournalEntry` | القيد المحاسبي — الوحدة الأساسية لكل شيء | `id` | `is_reversal_of→journal_entries.id` | `ref_no` (UNIQUE، `String(30)`)، `entry_date`، `description` | `JournalEntryStatus`: DRAFT/POSTED/CANCELLED | `currency_code`+`exchange_rate` (افتراضي القيد كله) | — (بالأسطر فقط) | `source_type: String(30)` (افتراضي `"manual"`) + `source_id: int\|None` | `lines` (1→N، cascade delete-orphan) | `ref_no` UNIQUE |
| `JournalLine` | سطر مدين/دائن واحد | `id` | `entry_id→journal_entries.id`، `account_id→accounts.id` | `debit`/`credit` (خام)، `debit_base`/`credit_base` (محفوظتان صراحة، لا تُحسَبان عند العرض) | — | `line_currency_code`/`line_exchange_rate` (`NULL`=يرث من القيد — **فقط لسند القيد اليدوي** حسب التعليق بالكود) | `Numeric(14,2)` | — | `entry`، `account` | `CHECK (debit=0 AND credit>=0) OR (credit=0 AND debit>=0)` |
| `Settlement` | حدث قبض/دفع/استرداد واحد (بعد 3B-3) | `id` | `journal_entry_id→journal_entries.id` (**UNIQUE** — 1:1)، `party_account_id→accounts.id` | `kind: String(20)` ("receipt"/"payment"/"customer_refund"/"supplier_refund") | — (لا enum status خاص بها) | `currency_code`+`settlement_rate` | `amount_foreign` | — (لا source_id) | `allocations` (1→N) | `CHECK amount_foreign>0`، `CHECK settlement_rate>0` |
| `SettlementAllocation` | تخصيص جزء من Settlement لهدف واحد | `id` | `settlement_id`، `invoice_id\|None`، `opening_party_entry_id\|None` | `amount_foreign`، `fx_amount` | — | يرث من Settlement | `Numeric(14,2)` | — | `settlement`، `invoice`، `opening_party_entry` | Exclusive Arc CHECK + `CHECK amount_foreign>0` |
| `OpeningPartyEntry` | رصيد افتتاحي عميل/مورد (3B-3) | `id` | `journal_entry_id` (**UNIQUE** — 1:1)، `party_account_id` | `kind` (RECEIVABLE/PAYABLE)، `reference` (نصي، **بلا UNIQUE** عمداً) | — | `currency_code`+`exchange_rate` | `original_amount_foreign`+`amount_base` | — | — | `CHECK original_amount_foreign>0`، `CHECK exchange_rate>0` |
| `OpeningBalanceEntry`/`OpeningInventoryEntry` | سجلات تفصيلية 3B-1/3B-2 | — | `journal_entry_id` | — | — | — | — | — | — | (راجع تقارير 3B-1/3B-2 السابقة — لا تكرار هنا) |
| `Account` | دليل الحسابات | `id` | `parent_id` (شجري) | `code`، `name_ar`، `is_group`، `is_active` | — | `currency_code` | — | — | `lines` | — |
| `AccountSubtype` (enum على `Account`) | تصنيف عمل الحساب | — | — | القيم: `GENERAL`/`CUSTOMER`/`SUPPLIER`/**`CASH`**/**`BANK`**/`EXPENSE`/`INCOME`/`OTHER` | — | — | — | — | — | — |

**لا يوجد model اسمه `Voucher` بأي شكل. [غير موجود]**
**لا يوجد `Numbering`/`Sequence` model منفصل. [غير موجود]**
**لا يوجد `Source`/`Document` model موحَّد يجمع الفواتير/القيود/التسويات تحت مظلة واحدة. [غير موجود]** — كل نوع مستند (`Invoice`، `JournalEntry`، `Settlement`، `OpeningPartyEntry`، `StockTransfer`) جدول مستقل تماماً بلا أي جدول أب مشترك.

**اكتشاف مهم [موجود فعلياً]**: `AccountSubtype.CASH`/`AccountSubtype.BANK` **مُعرَّفان بالـenum ويُستخدَمان فقط عند إنشاء دليل الحسابات الافتراضي** (`chart_of_accounts_template.py` أسطر 34-35) — **لا يوجد أي تحقق بالخدمات (`settlements.py`/`posting.py`) يفرض أن `cash_account_id` الممرَّر فعلياً من نوع `CASH`/`BANK`** — بعكس `CUSTOMER`/`SUPPLIER` (مُتحقَّق منهما صراحة بكل مكان). أي حساب نشط يمكن تمريره كـ`cash_account_id` بلا رفض.

---

## 2. Existing Accounting Engine

**[موجود فعلياً] `app/services/journal_edit.py` هو محرك القيد اليدوي الوحيد** (73 سطراً فقط):
- `ensure_editable(entry)` — يرفض التعديل إن `status != DRAFT` أو `is_reversal_of is not None`.
- `_validate_lines(entry)` — يرفض: سطر بلا حساب، سطر بمدين ودائن معاً، سطر فارغ (كلاهما صفر)، قيد غير متوازن، قيد بلا أسطر.
- `add_manual_line(session, entry, account_id, debit=0, credit=0, exchange_rate=None, line_currency_code=None)` — يبني `JournalLine` واحداً ويضيفه.
- `remove_manual_line(session, line)`.
- `post_manual_entry(session, entry)` — يستدعي `_validate_lines` ثم `status=POSTED`.
- `reverse_manual_entry(session, original_entry, reversal_date, description)` — يبني قيداً معاكساً جديداً (`is_reversal_of=original.id`)، يرفض إن `original.status != POSTED` أو كان معكوساً مسبقاً أو هو نفسه عكس قيد آخر (`already_reversed` check عبر استعلام `filter_by(is_reversal_of=...)`).

**[موجود فعلياً] `app/services/posting.py` يحتوي محركاً ثانياً موازياً تماماً**، بدواله الخاصة غير المرتبطة بـ`journal_edit.py` إطلاقاً:
- `_jline(account_id, debit, credit, rate)` — سطر بمعدل واحد (`base=raw×rate`).
- `_jline_base(account_id, debit_base, credit_base)` — سطر "مُحوَّل مسبقاً" (`raw=base` دائماً، موثَّق تاريخياً لتفادي التحويل المزدوج بـCOGS/الفروق).
- `post_sales_invoice`/`post_purchase_invoice` يبنيان `entry.lines = [...]` **مباشرة** (بناء يدوي لقائمة، لا عبر `add_manual_line`)، ثم `entry.is_balanced()` تحقق ذاتي، ثم `session.add(entry)` — **بلا استدعاء `post_manual_entry()` أو `_validate_lines()` إطلاقاً**.
- `_next_ref_no(session, prefix)`.

**[موجود فعلياً] `app/services/returns.py` يكرر نفس نمط `posting.py` بالضبط** (بناء `entry.lines` مباشرة + `_jline`/`_jline_base` + `is_balanced()` ذاتي) — لا استدعاء لـ`journal_edit.py` أيضاً.

**[موجود فعلياً] `app/services/settlements.py` (بعد 3B-3) يكرر نفس النمط أيضاً** — `_post_settlement_multi`/`_post_refund` يبنيان `entry.lines` مباشرة، بلا `journal_edit.py`.

**[موجود فعلياً] `app/services/opening_balances.py`/`opening_party_balances.py`** — هذان الوحيدان اللذان **يستخدمان `add_manual_line`/`post_manual_entry` من `journal_edit.py` فعلياً** (بدل البناء المباشر).

### إجابة السؤال المحوري: هل يوجد أكثر من مسار لإنشاء JournalEntry؟
**نعم — مساران منفصلان تماماً بلا أي تقاطع كودي:**
1. **المسار "اليدوي" الرسمي**: `journal_edit.py` (`add_manual_line`→`post_manual_entry`) — يفرض `_validate_lines()` مركزياً. مُستخدَم فقط بـ`opening_balances.py`/`opening_party_balances.py`/واجهة "سند القيد" (`journal_voucher_form.py`).
2. **المسار "الآلي" المباشر**: بناء `entry.lines = [...]` يدوياً + `_jline`/`_jline_base` + `entry.is_balanced()` تحقق ذاتي محلي بكل دالة على حدة، بلا `_validate_lines()` المركزي إطلاقاً. مُستخدَم بـ`posting.py`/`returns.py`/`settlements.py`/`invoice_cancel.py`.

**كلا المسارين يصلان لنفس القاعدة (`entry.is_balanced()` على الأقل)، لكن التحققات الأخرى لـ`_validate_lines()`** (سطر بمدين ودائن معاً، سطر فارغ، قيد بلا أسطر) **غير مفروضة على المسار الثاني بأي شكل مركزي** — كل دالة تفترض ضمنياً أنها بنت الأسطر بشكل صحيح، بلا حارس مشترك.

---

## 3. Existing Journal Entry APIs — القائمة الكاملة

| الدالة | الملف | المسار (1 أو 2 أعلاه) |
|---|---|---|
| `add_manual_line`/`post_manual_entry`/`reverse_manual_entry` | `journal_edit.py` | 1 |
| `post_sales_invoice`/`post_purchase_invoice` | `posting.py` | 2 |
| `post_sales_return`/`post_purchase_return` | `returns.py` | 2 |
| `_post_settlement_multi`/`_post_refund` | `settlements.py` | 2 |
| `cancel_invoice` (يبني قيد عكس) | `invoice_cancel.py` | 2 |
| `post_opening_account_balances`/`reverse_opening_account_balances` | `opening_balances.py` | 1 |
| `post_opening_inventory`/`reverse_opening_inventory` | `opening_balances.py` | 1 |
| `post_opening_party_entry`/`reverse_opening_party_entry` | `opening_party_balances.py` | 1 |

**لا bypass لقواعد التوازن نفسها** (`is_balanced()` مُتحقَّق منه بكل مسار بلا استثناء) **لكن يوجد تكرار كودي حقيقي [موجود فعلياً]**: 8 دوال منفصلة تبني قيوداً، بنمطين مختلفين، بلا طبقة توحيد واحدة تُستدعى منها جميعاً.

---

## 4. Existing Source Types — جدول كامل، بلا اختراع

| `source_type` (على `JournalEntry`) | أين يُستخدَم | نوع المستند | ينشئ JournalEntry؟ | قابل للعكس؟ |
|---|---|---|---|---|
| `"manual"` (افتراضي العمود، لا استخدام صريح مؤكَّد بأي مكان) | افتراضي العمود فقط | — | — | — |
| `"sales_invoice"` | `posting.py` | فاتورة بيع | نعم | نعم (عبر `invoice_cancel.py`، لا `journal_edit.py`) |
| `"purchase_invoice"` | `posting.py` | فاتورة شراء | نعم | نعم (نفس الآلية) |
| `"sales_return"` | `returns.py` | مرتجع بيع | نعم | غير مؤكَّد من الفحص — لم يظهر مسار عكس صريح لمرتجع نفسه |
| `"purchase_return"` | `returns.py` | مرتجع شراء | نعم | نفس الملاحظة أعلاه |
| `"invoice_cancel"` | `invoice_cancel.py` | إلغاء فاتورة | نعم (قيد عكسي) | لا (هو نفسه قيد عكس) |
| `"manual_reversal"` | `journal_edit.py::reverse_manual_entry` | عكس أي قيد يدوي | نعم | لا |
| `"opening_balance"` | `opening_balances.py` (3B-1) | رصيد حساب افتتاحي | نعم | نعم |
| `"opening_inventory"` | `opening_balances.py` (3B-2) | رصيد مخزون افتتاحي | نعم | نعم (`opening_inventory_reverse`) |
| `"opening_party_entry"` | `opening_party_balances.py` (3B-3) | رصيد عميل/مورد افتتاحي | نعم | نعم (عبر `reverse_manual_entry` العام) |
| `"receipt"` | `settlements.py` | قبض | نعم | لا (Append-Only — تصحيح بقيد جديد فقط) |
| `"payment"` | `settlements.py` | دفع | نعم | لا |
| `"customer_refund"`/`"supplier_refund"` | `settlements.py` | استرداد | نعم | لا |
| `"stock_transfer"` | `inventory_transfer.py` | **على `InventoryMovement` فقط، لا `JournalEntry`** — لا قيد محاسبي يُنشأ إطلاقاً لتحويل المخزون | لا | — |

**لا قيمة أخرى وُجدت بالكود. [موجود فعلياً بحدود هذا الجدول فقط]**

**ملاحظة عدم اتساق [موجود فعلياً]**: `source_id` مُملَّأ دائماً بمستندات الفواتير/الأرصدة الافتتاحية (`source_id=invoice.id`، إلخ)، لكن **`settlements.py` تمرر `source_id=None` دائماً** لكل من `receipt`/`payment`/`customer_refund`/`supplier_refund` — لا رابط مباشر من `JournalEntry` إلى `Settlement`؛ التتبع العكسي الوحيد الممكن هو عبر `Settlement.journal_entry_id` (بالاتجاه المعاكس فقط).

---

## 5. Existing Ways to Create JournalEntry/JournalLine — التكرار المُسجَّل (بلا إصلاح)

**A. Duplicate accounting logic [موجود فعلياً]**: نمطان منفصلان (§2 أعلاه) — كل من `posting.py`/`returns.py`/`settlements.py`/`invoice_cancel.py` يعيد تطبيق نفس الفكرة (`_jline`/`_jline_base` + بناء `entry.lines` + `is_balanced()` ذاتي) بمعزل عن بعضها البعض وعن `journal_edit.py`.

**B. Direct JournalEntry creation خارج أي "محرك" [موجود فعلياً]**: كل الدوال بالمسار الثاني (§2) تنشئ `JournalEntry(...)` مباشرة بالكود، لا عبر factory أو نقطة دخول موحَّدة.

**C. Direct JournalLine creation قد يتجاوز validation [موجود فعلياً]**: لأن `_validate_lines()` (بـ`journal_edit.py`) غير مُستدعاة بالمسار الثاني، أي دالة جديدة تُضاف لهذا المسار **يمكنها تقنياً** إنشاء سطر فارغ أو بمدين ودائن معاً دون رفض مركزي — الحماية الوحيدة الفعلية بهذا المسار هي انضباط المبرمج + `is_balanced()` (توازن المجموع فقط، لا صحة كل سطر بمفرده).

**D. Direct DB mutation على سجلات مُرحَّلة [غير موجود]** — لم أجد أي مكان يُعدِّل `JournalLine`/`JournalEntry` مباشرة بعد `POSTED` بلا المرور عبر `ensure_editable`/فحص مكافئ. (`Settlement`/`SettlementAllocation` مُختبَران صراحة كـAppend-Only بـ3B-3).

**E. FX calculations duplicated [موجود فعلياً]**: صيغة `new_base − booked_base` (أو ما يكافئها) محسوبة يدوياً بثلاثة أماكن منفصلة على الأقل: `settlements.py::_post_settlement_multi`، `settlements.py::_post_refund`، وضمنياً بأي دالة فاتورة تستخدم `invoice.exchange_rate` لحساب COGS/الفروق — لا دالة FX مشتركة واحدة.

---

## 6. Existing General Journal ("سند القيد")

**[موجود فعلياً] الإجابة المباشرة على السؤال الأهم: نعم، الـGeneral Journal الحالي هو Voucher بدائي موجود فعلياً — بالاسم حرفياً.**

- الملف: `app/ui/accounting/journal_voucher_form.py` (713 سطراً) — عنوانه بالكود حرفياً **"Journal Voucher Form — سند القيد اليدوي"**.
- يُنشئ عبر `add_manual_line`/`post_manual_entry` (المسار 1 بـ§2) — **الاستخدام الوحيد لهذا المسار من واجهة المستخدم**.
- **Draft/Posted**: نعم — يبدأ `DRAFT`، يُقفَل بزر ترحيل صريح يستدعي `post_manual_entry`.
- **تعديل بعد POSTED**: لا — `ensure_editable` يرفض.
- **العملات**: نعم — `line_currency_code`/`line_exchange_rate` لكل سطر بمعزل (يسمح بخلط عملات بنفس القيد — موثَّق بالكود لحالة "تحويل نقدي دولار مقابل ليرة سورية").
- **Party accounts**: `list_postable_accounts()` **لا تميّز** — أي حساب فرعي نشط (بما فيها عملاء/موردون) قابل للاختيار بحرية، بلا تحقق `subtype`/`allow_reconciliation` (بعكس `settlements.py` الذي يفرض ذلك صراحة).
- **Cash/Bank accounts**: نفس الملاحظة — لا تمييز، أي حساب.
- **Reference**: `ref_no` عبر `_next_ref_no` **لكن الكود لم يُفحَص بدقة ليؤكد أي prefix يُستخدَم لسند القيد اليدوي تحديداً من الواجهة** — [يحتاج قراراً/فحصاً إضافياً لتأكيد الـprefix الفعلي المُستخدَم من `journal_voucher_form.py`، لم يظهر باستدعاء مباشر بالملف نفسه بالفحص الحالي].
- **Numbering**: نعم، عبر `_next_ref_no` (نمط عام، لا خاص بسند القيد وحده).
- **Reversal**: نعم — `reverse_manual_entry` متاحة (لا تأكيد ما إذا كانت مربوطة بزر بالواجهة من `journal_voucher_form.py` بالفحص الحالي — [يحتاج فحصاً إضافياً]).
- **التحقق من Debit=Credit**: مؤشر لوني فوري بالواجهة (أخضر/أحمر) **للعرض فقط**، والحماية الفعلية الوحيدة من `journal_edit.py::_validate_lines`/`is_balanced()` وقت الترحيل (موثَّق بتعليق الكود نفسه صراحة).

---

## 7. Existing Receipt/Payment (بعد 3B-3)

كل هذا **[موجود فعلياً]**، ومُوثَّق بالكامل بـ`PHASE3B3_DESIGN_SPEC.md`/`WORKFLOW.md §68`:
- `JournalEntry` يُنشأ داخل `_post_settlement_multi`/`_post_refund` مباشرة (المسار 2).
- `cash_account_id`/`party_account_id` **يُمرَّران صراحة كمعاملات** من المستدعي — **لا اكتشاف تلقائي بالخدمة** (بعكس `_invoice_receivable_or_payable_account_id` التي تستنتج حساب الطرف من الفاتورة نفسها).
- `currency_code` صريح كمعامل، مع قيد إلزامي: كل الأهداف المخصَّصة يجب أن تطابق هذه العملة (§1.11 بمواصفة 3B-3).
- `exchange_rate` (باسم `settlement_rate`/`refund_rate`) صريح كمعامل — لا استنتاج تلقائي.
- **Reference**: `ref_no` عبر `_next_settlement_ref(session, kind)` — دالة **مستقلة تماماً** عن `_next_ref_no` العامة، بنفس المنطق (COUNT بمعزل عن `source_type`).
- **Voucher number منفصل عن `ref_no`**: **[غير موجود]** — لا حقل `voucher_no` مستقل، `ref_no` هو كل ما يوجد.
- **Document number**: **[غير موجود]** بمعنى منفصل عن `ref_no`.
- **Common validation rules**: تحقق `subtype`/`allow_reconciliation` مُكرَّر (نسخة شبه مطابقة) بين `_invoice_receivable_or_payable_account_id`، `_post_settlement_multi`، `_post_refund`، `post_opening_party_entry` — **منطق تحقق مكرر أربع مرات بصياغات متقاربة لا دالة تحقق واحدة مشتركة**.

**الأجزاء المشتركة الفعلية القابلة لإعادة الاستخدام بـ3B-4** (بدل إعادة البناء): `_jline`/`_jline_base`/`_jline_party` (`posting.py`+`settlements.py`)، `_get_setting` (`posting.py`)، نمط "COUNT-based ref_no" (مُكرَّر 5 مرات، مرشَّح طبيعي للتوحيد إن قرَّرتم ذلك لاحقاً — لا أقترح ذلك الآن).

---

## 8. Existing Numbering

**النتيجة الحاسمة: No existing dedicated voucher/sequence numbering engine found.**

كل ترقيم بالمشروع هو **COUNT-based محلي مُكرَّر 5 مرات مستقلة**:
1. `posting.py::_next_ref_no(session, prefix)` — `SELECT COUNT WHERE ref_no LIKE 'prefix-%'` ثم `f"{prefix}-{count+1:05d}"`.
2. `settlements.py::_next_settlement_ref(session, kind)` — نفس الفكرة، مفاتيح ثابتة لكل `kind`.
3. `opening_party_balances.py::_next_opening_party_ref(session)`.
4. `inventory_transfer.py::_next_transfer_no(session)`.
5. `invoice_cancel.py` — نمط inline مباشر (`f"INV-CXL-{count+1:06d}"`) بلا دالة مسماة حتى.

**لا حجز رقم قبل POST** — الرقم يُحسَب لحظة الإنشاء نفسها (DRAFT يحصل على رقمه فوراً، لا انتظار للترحيل) بكل الحالات المفحوصة.
**لا Uniqueness مفروضة بقاعدة البيانات إلا على `JournalEntry.ref_no`** (`unique=True` بالنموذج) — بقية الأرقام (`Invoice.invoice_no`، إلخ) **بلا قيد UNIQUE بقاعدة البيانات مؤكَّد من الفحص**.
**لا Numbering لكل شركة منفصل** — لا مفهوم "شركة" متعدد بالنموذج الحالي أصلاً (قاعدة بيانات واحدة لكل عميل، حسب فهم المشروع العام).
**لا Numbering لكل نوع Voucher منفصل بمعنى مُهيكَل** — فقط بادئات نصية مختلفة (`JE-SAL`، `JE-RCV`، إلخ) بنفس الآلية المكرَّرة.

**اكتشاف حرج [موجود فعلياً — Bug/Gap حقيقي، لا افتراض]**: **`Invoice.invoice_no` لا يُولَّد بترقيم حقيقي إطلاقاً بمسار الواجهة الفعلي.** بالفحص المباشر لـ`app/ui/common/document_form.py`:
- عند إنشاء فاتورة جديدة: `invoice_no=f"DRAFT-{id(self)}"` (**معرّف كائن Python بالذاكرة، ليس رقماً تسلسلياً**).
- عند `_post()` (الترحيل): **لا إعادة تعيين لـ`invoice_no` إطلاقاً** — يبقى `"DRAFT-{id(self)}"` حتى بعد `POSTED`.
- الواجهة تعرض تلميحاً "يُولَّد عند الترحيل" (`invoice_no_edit.setPlaceholderText`) **لكن لا كود فعلي ينفّذ ذلك**.
- كل اختبارات الانحدار (`test_*.py`) تمرِّر `invoice_no` **صريحاً يدوياً** (مثل `"SL-1"`، `"INV-1"`) عند بناء كائن `Invoice` مباشرة بالكود — **لهذا لم يكتشف أي اختبار موجود هذه الفجوة**؛ لا اختبار واحد يمر عبر `document_form.py`/`_save_draft`/`_post` الفعليين لفاتورة جديدة بلا `invoice_no` مُمرَّر يدوياً.

هذا **Technical Debt حقيقي منفصل تماماً عن 3B-4**، لكن مباشر الصلة: أي "Voucher Engine" يفترض ترقيماً موحَّداً يجب أن يقرر صراحة هل يُصلح هذه الفجوة ضمنياً أو يتركها بمعزل.

---

## 9. Existing Status/Workflow

- **الكيانات ذات Lifecycle**: `Invoice` (`InvoiceStatus`: DRAFT/POSTED/CANCELLED)، `JournalEntry` (`JournalEntryStatus`: DRAFT/POSTED/CANCELLED). **لا `Settlement`/`SettlementAllocation`/`OpeningPartyEntry` — هذه بلا حقل status إطلاقاً (تُعتبَر "مُرحَّلة" بمجرد وجودها، Append-Only بالتصميم).**
- **اكتشاف [موجود فعلياً]**: `JournalEntryStatus.CANCELLED` **مُعرَّف بالـenum لكن لا استخدام واحد له بأي مكان بالخدمات** (بحث شامل: صفر نتيجة لـ`JournalEntryStatus.CANCELLED` كقيمة مُسنَدة). قيمة ميتة (dead enum value) بمستوى `JournalEntry` تحديداً — بعكس `InvoiceStatus.CANCELLED` المُستخدَمة فعلياً بـ`invoice_cancel.py`.
- **لا "REVERSED" status بأي enum** — العكس مُمثَّل بعمود `is_reversal_of` (self-referencing FK) على القيد **الجديد** يشير للقيد **الأصلي**؛ القيد الأصلي **يبقى `POSTED` للأبد**، لا يتغيّر حالته أبداً حتى بعد عكسه (تحقَّق منه فعلياً أثناء اختبار 3B-3 بالجولة السابقة).
- **من يسمح بالانتقال بين الحالات**: `ensure_editable`/`post_manual_entry` (المسار 1) — أو تحقق inline محلي بكل دالة (المسار 2، لا دالة `ensure_editable` مشتركة تُستدعى هناك).
- **POSTED immutable**: نعم، بكلا المسارين — لا `UPDATE` مباشر لسطر/قيد `POSTED` بأي مكان مفحوص.
- **Cancellation ≠ Reversal**: نعم، مفهومان منفصلان فعلياً — `invoice_cancel.py` يفحص وجود تسويات/حركات معتمدة عليها قبل الرفض، وينشئ قيد عكس (`is_reversal_of`) + يضع `Invoice.status=CANCELLED` (على الفاتورة، لا القيد). عكس عام (`reverse_manual_entry`) لا يغيّر أي status على القيد الأصلي إطلاقاً، فقط ينشئ قيداً معاكساً.
- **Workflow موحَّد؟ لا [موجود فعلياً — أو بالأحرى: غياب موحَّد]** — كل خدمة (`invoice_edit.py`/`journal_edit.py`/Settlement Append-Only) تطبّق قواعدها الخاصة بصياغة منفصلة، بلا enum/state-machine مشترك.

---

## 10. Existing Currency/FX Architecture

بالضبط كما هو موثَّق بـ§1 (نموذج `JournalLine`) — لا افتراض أسماء:
- `JournalEntry.currency_code`/`exchange_rate`: العملة/السعر **الافتراضيان لكل القيد** — تُستخدَمان ضمنياً لأي سطر لا يملك قيمه الخاصة.
- `JournalLine.line_currency_code`/`line_exchange_rate`: **اختياريان، `NULL` افتراضياً** — يسمحان لسطر واحد بعملة مختلفة عن باقي القيد؛ موثَّق بالكود أنهما "فقط لسند القيد اليدوي" لكن لا فرض تقني يمنع استخدامهما بمسارات أخرى.
- `JournalLine.debit`/`credit`: **خام** — بعملة السطر (`line_currency_code` أو `entry.currency_code`).
- `JournalLine.debit_base`/`credit_base`: **محفوظتان صراحة** (لا تُحسَبان بالعرض) — بالعملة الأساسية، محسوبتان وقت الترحيل بالسعر السائد وقتها.
- **الفرق بين `_jline` و`_jline_base`**: الأول يحسب `base=raw×rate` (سعر واحد)؛ الثاني يفرض `raw=base` (لخطوط لا معنى لـ"خام" منفصل لها، كـCOGS). **`_jline_party` (جديدة بـ3B-3)** أُضيفت خصيصاً لحل حالة ثالثة: `raw` و`base` **مستقلان تماماً وغير مرتبطين بسعر واحد** (سطر حساب طرف "مُدمَج" من أجزاء بأسعار مختلفة).
- **دالة FX مشتركة موحَّدة**: **[غير موجود]** — كل موضع يحسب الفرق يدوياً (`new_base - booked_base` أو مكافئها).

---

## 11. Existing Tests → Behavior Map

| السلوك الموجود | الاختبار (إن وُجد) | النتيجة المحاسبية المتوقَّعة |
|---|---|---|
| سند قيد يدوي (add_manual_line/post_manual_entry) | **لا ملف مخصَّص** — يُستخدَم كإعداد أولي ضمن `test_e2e_scenario.py`/`test_opening_account_balances.py`/`test_full_inventory_lifecycle.py`/`test_accounting_edge_cases.py`/`test_aggressive_currency_inventory.py`/`test_app_path_after_alembic.py` | القيد يُقفَل POSTED، لا تعديل لاحقاً |
| رفض قيد غير متوازن/فارغ/سطر مزدوج | جزئي فقط بـ`test_aggressive_currency_inventory.py` (سطران فقط يستخدمان `JournalEditError`) | `JournalEditError` |
| عكس قيد يدوي (`reverse_manual_entry`) | غير مؤكَّد بملف مخصَّص — يُستخدَم ضمن اختبارات 3B-2/3B-3 كجزء من `reverse_opening_*` | قيد جديد معاكس، الأصلي يبقى POSTED |
| قبض/دفع/استرداد (3B-3) | `test_phase3b3_settlement_allocation.py` (50 تحقّقاً) | موثَّق بالكامل بـWORKFLOW.md §68 |
| تسوية فاتورة واحدة (قديم) | `test_settlement_fx.py`، `test_settlement_tamper_resistance.py`، `test_invoice_cycle_customer_supplier.py`، إلخ | موثَّق بتقارير 3B-3 |
| ترقيم (`ref_no`/`invoice_no`) بمعزل | **[غير موجود]** — لا اختبار مخصَّص لصحة/تفرّد الترقيم نفسه | — |
| إلغاء فاتورة (`invoice_cancel.py`) | `test_cancel_invoice.py`، `test_ui_settlement_and_cancel.py` | موثَّق سابقاً |
| تحويل مخزون (`stock_transfer`) | غير مؤكَّد بملف مخصَّص بهذا الفحص | — |

**الخلاصة**: تغطية جيدة جداً لكل ما بُني بـ3B-1/2/3، لكن **صفر تغطية اختبار مخصَّصة لمحرك "سند القيد اليدوي" نفسه (المسار 1) كموضوع مستقل** — كل تغطيته الحالية غير مباشرة (Side effect لاختبار شيء آخر).

---

## 12. Existing UI

| الشاشة | الملف | تستدعي |
|---|---|---|
| سند القيد اليدوي ("Voucher") | `app/ui/accounting/journal_voucher_form.py` + `journal_voucher_list.py` | `journal_edit.py` (المسار 1) |
| نافذة التسوية (قبض/دفع لفاتورة) | `app/ui/common/settlement_dialog.py` | `settlements.py::post_receipt`/`post_payment` (فاتورة واحدة فقط — **لا واجهة لـ`post_receipt_allocated`/الاسترداد الجديدين بـ3B-3 حتى الآن**) |
| فاتورة بيع/شراء | `app/ui/sales/invoice_form.py`/`app/ui/purchases/invoice_form.py` (عبر `document_form.py` المشترك) | `posting.py` |
| مرتجع بيع/شراء | `app/ui/sales/return_form.py`/`app/ui/purchases/return_form.py` | `returns.py` |
| كشف حساب | `app/ui/accounting/account_statement_dialog.py` | `ledger.py::get_account_statement` |
| دليل الحسابات | `app/ui/accounting/chart_of_accounts_view.py` + `account_card_dialog.py` | `account_edit.py`/`account_queries.py` |

**لا شاشة Receipt/Payment/Refund مستقلة عن الفاتورة [غير موجود]** — `settlement_dialog.py` هو الوحيد، ومرتبط بفاتورة واحدة تحديداً (لا يعرض قائمة "أرصدة مفتوحة" لعميل يمكن تخصيص قبض واحد عليها متعددة، رغم أن الخدمة الخلفية `post_receipt_allocated` أصبحت تدعم ذلك فعلياً منذ 3B-3 — **فجوة UI↔Service حقيقية موجودة الآن**).
**لا شاشة Cash/Bank/Treasury مستقلة [غير موجود]** — لا "دفتر صندوق" أو ما يكافئه.
**لا شاشة Voucher عامة غير مرتبطة بالمحاسبة [لا ينطبق]** — "Voucher" بهذا المشروع = سند القيد المحاسبي حصراً، لا مفهوم مستند إداري منفصل.

---

## 13. Existing Documentation

`WORKFLOW.md` يوثّق تاريخياً قرارات §1 حتى §69 (آخرها Technical Debt الخاص بـ`returns.py`/`get_party_currency_balance`، غير ذي صلة مباشرة بـ3B-4 لكنه القيد الأحدث المُسجَّل). `PHASE3B2_DESIGN_SPEC.md`/`PHASE3B3_DESIGN_SPEC.md` يوثّقان تصميمي 3B-2/3B-3 بالتفصيل. **لا وثيقة سابقة تصف "General Journal" أو "Voucher" كمفهوم مستقل بذاته قبل هذا التقرير** — كل ما وُجد عنه هو تعليقات كود متناثرة (`journal_edit.py`/`journal_voucher_form.py`).

---

## 14. Duplicate Logic (مُسجَّل فقط، بلا إصلاح — كما طُلب)

ملخَّص لِما فُصِّل بالأقسام أعلاه:
- **2 مسار مختلف لإنشاء JournalEntry** (§2/§5).
- **5 دوال ترقيم منفصلة** بنفس منطق COUNT (§8).
- **تحقق subtype/allow_reconciliation مُكرَّر 4 مرات** بصياغات متقاربة (§7).
- **حساب FX يدوي مُكرَّر بلا دالة مشتركة** (§10).
- **`_validate_lines()` المركزي غير مُطبَّق على 4 من أصل 8 دوال إنشاء قيود** (§5-C).

---

## 15. Direct Journal Bypasses

راجع §5 (B/C) أعلاه — لا "bypass" بمعنى كسر التوازن (`is_balanced()` مفروض بكل مكان)، لكن **تحقق-الأسطر-الفردية المركزي (`_validate_lines`) غير مفروض إلا بالمسار 1 (2 من أصل 8 دوال)**.

---

## 16. Existing Voucher-like Functionality

- **سند القيد اليدوي = Voucher بدائي، بالاسم حرفياً بالواجهة** (§6) — أقرب شيء لـ"Voucher Engine" موجود فعلياً اليوم.
- **`settlement_dialog.py`** = نافذة قبض/دفع، لكن مرتبطة بفاتورة واحدة فقط، لا مفهوم "سند" مستقل قابل لإدخاله بمعزل عن فاتورة.
- **لا "Contra Voucher"/"Transfer Voucher" بمعنى محاسبي [غير موجود]** — `StockTransfer` موجود لكنه **مخزوني بحت، بلا أي قيد محاسبي** (موثَّق بتعليق الكود نفسه: "بدون أي قيد محاسبي").
- **لا "Treasury"/"دفتر صندوق" [غير موجود]**.

---

## 17. Technical Debt relevant to 3B-4

1. **مساران منفصلان لإنشاء JournalEntry** (§2/§5) — أي "Voucher Engine" يحتاج قراراً: هل يوحّدهما أم يضيف مساراً ثالثاً؟
2. **5 دوال ترقيم مكرَّرة** (§8) — مرشَّح طبيعي لتوحيد لو قرَّرتم ذلك، لكن ليس قراراً اتخذته الآن.
3. **`Invoice.invoice_no` بلا ترقيم حقيقي فعلياً بمسار الواجهة** (§8) — Bug/Gap موجود مسبقاً، منفصل عن 3B-4 لكن يتقاطع معه مباشرة لو شمل 3B-4 الفواتير بأي شكل.
4. **`AccountSubtype.CASH`/`BANK` مُعرَّفتان لكن غير مُتحقَّق منهما بأي خدمة** (§1) — فجوة تحقق مشابهة لِما كان قائماً قبل تصحيح `subtype`/`allow_reconciliation` بمرحلة سابقة (§56 بـWORKFLOW.md).
5. **`JournalEntryStatus.CANCELLED` قيمة ميتة غير مُستخدَمة** (§9).
6. **`settlement_dialog.py` لا يعرض قدرات `post_receipt_allocated`/الاسترداد الجديدة (فجوة UI↔Service بعد 3B-3)** (§12).
7. **Technical Debt سابق مُسجَّل بـWORKFLOW.md §69** (`get_party_currency_balance` مع مرتجعات شراء غير نقدية) — لا يزال قائماً، غير ذي صلة مباشرة بـ3B-4 لكن مذكور للاكتمال.

---

## 18. Recommended Scope Boundary for 3B-4 — **[يحتاج قراراً منك، لا اقتراح تصميم مني]**

بناءً على ما سبق فقط (حقائق، لا اقتراح معماري):
- **إن كان المقصود بـ"Voucher Engine" توحيد نقاط الدخول الحالية** (المسارين بـ§2) تحت واجهة واحدة، فهذا يمس `posting.py`/`returns.py`/`settlements.py`/`invoice_cancel.py` معاً — نطاق واسع جداً يتقاطع مع كل ما أُغلق بـ3B-1/2/3.
- **إن كان المقصود توسيع `journal_voucher_form.py`/`journal_edit.py` (الموجود فعلياً كـVoucher بدائي) ليشمل ترقيماً موحَّداً وربطاً بحسابات Cash/Bank/Party بتحقق صريح**، فهذا نطاق أضيق بكثير، يبني مباشرة على المسار 1 الموجود بلا مساس بالمسار 2 (الفواتير/التسويات تبقى كما هي).
- **إن كان المقصود إصلاح فجوة الترقيم (`invoice_no`) كجزء من 3B-4**، فهذا قرار منفصل تماماً عن السابقين، يمس `document_form.py`/UI الفواتير مباشرة.

هذه ثلاث تفسيرات مختلفة جوهرياً لـ"Voucher Engine" — **لا يمكن تحديد أيها المقصود من الكود وحده**. هذا القرار الأول والأهم قبل أي مواصفة.

---

**لا كود، لا Migration، لا Model جديد، لا تعديل على أي ملف تم بهذا الفحص. تقرير Code Archaeology فقط، بانتظار توجيهك.**
