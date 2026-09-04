"""
Opening Balances — أرصدة افتتاحية (حسابات ومواد)
====================================================
إجراء قياسي موحّد يُستخدم مرة واحدة لكل عميل جديد له تاريخ محاسبي سابق،
قبل تسجيل أي فاتورة. بدون هذا، أي كشف حساب أو تقرير مخزون لاحق يكون
ناقصاً أو مضللاً.

Phase 3B-1 (مُعتمَد من Bilal — راجع PHASE3_DESIGN_SPEC.md §6/§9/§11):
الأرصدة الافتتاحية للحسابات العامة أصبحت الآن دالة مخصصة فعلياً
(`post_opening_account_balances` أدناه) — الملاحظة القديمة أعلاه
("لا يحتاج دالة خاصة، قيد يدوي عادي كافٍ") كانت صحيحة قبل اعتماد
المواصفة، وأصبحت متجاوَزة الآن: القيد اليدوي وحده لا يفرض حساب Clearing
إلزامياً من إعدادات الشركة، ولا يمنع تكرار الترحيل (Idempotency)، ولا
يحتفظ بسجل تفصيلي منفصل (`OpeningBalanceEntry` — سجل Opening Balance
Detail، لا Audit Log كامل، راجع تعريف النموذج بـmodels.py) — كل هذه قواعد عمل
صريحة يجب فرضها بالخدمة، لا تركها لتقدير كل استدعاء يدوي.

Phase 3B-2 (مُعتمَد من Bilal — راجع PHASE3B2_DESIGN_SPEC.md وWORKFLOW.md
§§63–65): المخزون الافتتاحي أصبح أيضاً دالة مخصصة (`post_opening_inventory`
أدناه)، بنفس فلسفة 3B-1 تماماً. الدالة القديمة `set_item_opening_balance()`
كانت هنا مؤقتاً بعد أول تنفيذ لـ3B-2 (لعدم كسر 4 استدعاءات باختبارات
انحدار كانت تعتمد عليها) وحُذفت نهائياً الآن بعد نقل تلك الاستدعاءات
الأربعة فعلياً لـ`post_opening_inventory()` والتحقق من نجاح كل اختبار
بأرقامه المحاسبية الجديدة (لا COGS/Trial Balance قديمة أُبقيت كما هي
بلا مبرر — راجع رسائل commit المرتبطة). لا مسارين بعد الآن.

النطاق: **حسابات عامة** (3B-1) **ومخزون** (3B-2). أرصدة العملاء/الموردين
الافتتاحية التفصيلية مؤجَّلة لـ3B-3 بالترتيب المُعتمَد.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from sqlalchemy.orm import Session

from app.models import InventoryMovement, MovementDirection, Item

# ---------------------------------------------------------------------------
# Phase 3B-1 — الأرصدة الافتتاحية للحسابات العامة فقط
# ---------------------------------------------------------------------------
# مبدأ التصميم: لا آلية ترحيل جديدة. يُعاد استخدام add_manual_line() +
# post_manual_entry() الموجودتين فعلياً بـjournal_edit.py حرفياً — القيد
# الافتتاحي قيد يدوي عادي بمنظور المحرك، فقط بقواعد تحقق إضافية قبل
# بنائه (حساب Clearing إلزامي من إعدادات الشركة، منع تكرار الترحيل).

from app.models import Account, AccountType, JournalEntry, JournalEntryStatus, Setting, OpeningBalanceEntry
from app.services.journal_edit import add_manual_line, post_manual_entry, reverse_manual_entry
from app.services.posting import get_base_currency, _next_ref_no
from app.services.money import money, rate as rate_, D, qty

OPENING_BALANCES_SETTING_KEY = "opening_balances_accounts_posted_at"
CLEARING_ACCOUNT_SETTING_KEY = "opening_balance_clearing_account_id"


class OpeningBalanceError(Exception):
    pass


@dataclass
class OpeningBalanceLineInput:
    """سطر رصيد افتتاحي واحد كما يُدخِله المستخدم — قبل أي تحويل محاسبي."""
    account_id: int
    debit_foreign: Decimal = field(default_factory=lambda: Decimal("0"))
    credit_foreign: Decimal = field(default_factory=lambda: Decimal("0"))
    currency_code: str | None = None  # None = يرث العملة الأساسية للشركة (get_base_currency)
    exchange_rate: Decimal = field(default_factory=lambda: Decimal("1"))


def _get_clearing_account(session: Session) -> Account:
    """§6: حساب التوازن إعداد شركة إلزامي — لا اختراع تلقائي. يجب أن
    يكون موجوداً، غير تجميعي، نشطاً، ونوعه EQUITY حصراً (فصل مفاهيمي
    عن أرباح/مصاريف الفترة الحالية، طلب Bilal الصريح)."""
    row = session.get(Setting, CLEARING_ACCOUNT_SETTING_KEY)
    if row is None or not row.value:
        raise OpeningBalanceError(
            "لم يُحدَّد حساب التوازن الافتتاحي (Opening Balance Clearing) بإعدادات الشركة بعد — "
            "حدّده أولاً قبل إدخال أي رصيد افتتاحي"
        )
    account = session.get(Account, int(row.value))
    if account is None:
        raise OpeningBalanceError(
            f"حساب التوازن الافتتاحي المحدَّد بإعدادات الشركة (id={row.value}) غير موجود فعلياً"
        )
    if account.is_group:
        raise OpeningBalanceError(f"حساب التوازن الافتتاحي ({account.name_ar}) حساب تجميعي — لا يقبل قيوداً مباشرة")
    if not account.is_active:
        raise OpeningBalanceError(f"حساب التوازن الافتتاحي ({account.name_ar}) غير نشط")
    if account.account_type != AccountType.EQUITY:
        raise OpeningBalanceError(
            f"حساب التوازن الافتتاحي ({account.name_ar}) يجب أن يكون من نوع حقوق الملكية (EQUITY) — "
            f"نوعه الحالي {account.account_type.value}. لا يجوز أن يكون إيراداً أو مصروفاً "
            "(يشوّه قياس ربحية الفترة الحالية)."
        )
    return account


def _validate_entry_account(session: Session, account_id: int, clearing_id: int) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise OpeningBalanceError(f"حساب غير موجود (id={account_id})")
    if account.is_group:
        raise OpeningBalanceError(f"الحساب ({account.name_ar}) تجميعي — لا يقبل رصيداً افتتاحياً مباشراً")
    if not account.is_active:
        raise OpeningBalanceError(f"الحساب ({account.name_ar}) غير نشط")
    if account.id == clearing_id:
        raise OpeningBalanceError(
            f"لا يجوز إدخال رصيد افتتاحي على حساب التوازن نفسه ({account.name_ar}) — "
            "هو سطر التوازن التلقائي، لا سطر يدوي"
        )
    if account.account_type in (AccountType.REVENUE, AccountType.EXPENSE):
        raise OpeningBalanceError(
            f"الحساب ({account.name_ar}) من نوع إيراد/مصروف — لا يجوز أن يحمل رصيداً افتتاحياً "
            "(يشوّه قياس ربحية الفترة الحالية؛ حسابات الإيراد/المصروف تبدأ كل فترة من صفر)"
        )
    return account


def post_opening_account_balances(
    session: Session, entries: list[OpeningBalanceLineInput], opening_date: date,
) -> JournalEntry:
    """
    3B-1 حصراً — راجع رأس الملف. يبني قيداً واحداً POSTED مباشرة عبر
    add_manual_line()/post_manual_entry() الموجودتين فعلياً — لا منطق
    ترحيل جديد. §9: Idempotency برفض صريح — لا تنفيذ ثانٍ لنفس النطاق.

    قاعدة صريحة (مراجعة Bilal): **لا تعني نجاح هذه الدالة أن العملية
    أصبحت committed فعلياً على القرص** — تعني أنها أصبحت جاهزة ضمن
    transaction المُستدعي الحالية فقط (لا `session.commit()` هنا، مطابقةً
    لكل خدمات المشروع الأخرى). المُستدعي مسؤول عن `commit()` لتثبيتها
    نهائياً، أو `rollback()` لإلغائها بالكامل (بما فيها Setting القفل
    نفسها — مُختبَر صراحة أن rollback يُزيل القفل أيضاً، لا يتركه معلَّقاً).

    قاعدة صريحة أخرى (مراجعة Bilal): `exchange_rate` بكل سطر يعني دائماً
    "وحدات العملة الأساسية مقابل وحدة واحدة من عملة السطر" — أي
    `base_equivalent = amount × exchange_rate` مباشرة، لا معكوساً (ليس
    "كم من عملة السطر يساوي وحدة أساسية"). هذا هو الاتجاه الوحيد
    المدعوم، ومُختبَر صراحة بقيمة ≠ 1 (`test_opening_account_balances.py`
    §2: EUR 1,000 @ 1.1 → 1,100 USD بالضبط) — لا فقط بعملة الشركة نفسها
    حيث `rate=1` يُخفي أي خطأ اتجاه محتمل.
    """
    already_posted = session.get(Setting, OPENING_BALANCES_SETTING_KEY)
    if already_posted is not None:
        raise OpeningBalanceError(
            f"الأرصدة الافتتاحية للحسابات مُرحَّلة مسبقاً بتاريخ {already_posted.value} — "
            "لا يجوز ترحيلها مرة ثانية. للتصحيح: اعكس القيد الحالي أولاً عبر "
            "reverse_opening_account_balances()، ثم أعد الإدخال."
        )
    if not entries:
        raise OpeningBalanceError("لا يمكن ترحيل دفعة أرصدة افتتاحية فارغة — لم تُدخَل أي أسطر")

    clearing_account = _get_clearing_account(session)
    base_currency = get_base_currency(session)

    for e in entries:
        d, c = money(e.debit_foreign), money(e.credit_foreign)
        if d > 0 and c > 0:
            raise OpeningBalanceError(f"سطر الحساب {e.account_id}: لا يجوز مبلغ مدين ودائن معاً")
        if d == 0 and c == 0:
            raise OpeningBalanceError(f"سطر الحساب {e.account_id}: لازم مبلغ مدين أو دائن — لا سطر فارغ")

    entry = JournalEntry(
        entry_date=opening_date,
        ref_no=_next_ref_no(session, "JV-OPEN"),
        description="قيد الأرصدة الافتتاحية للحسابات",
        source_type="opening_balance", currency_code=base_currency, exchange_rate=Decimal("1"),
        status=JournalEntryStatus.DRAFT,
    )
    session.add(entry)
    session.flush()

    audit_rows: list[OpeningBalanceEntry] = []
    for e in entries:
        account = _validate_entry_account(session, e.account_id, clearing_account.id)
        line_currency = e.currency_code or base_currency
        # None يعني "يرث عملة القيد الافتراضية" (نفس نمط add_manual_line
        # الموثَّق) — لا نمرّره صراحة إلا لو كانت عملة السطر مختلفة فعلاً
        passed_currency = None if line_currency == base_currency else line_currency
        add_manual_line(
            session, entry, account_id=account.id,
            debit=e.debit_foreign, credit=e.credit_foreign, exchange_rate=e.exchange_rate,
            line_currency_code=passed_currency, line_exchange_rate=e.exchange_rate,
        )
        base_eq = money((e.debit_foreign or e.credit_foreign) * rate_(e.exchange_rate))
        audit_rows.append(OpeningBalanceEntry(
            journal_entry_id=entry.id, account_id=account.id, currency_code=line_currency,
            debit_foreign=money(e.debit_foreign), credit_foreign=money(e.credit_foreign),
            exchange_rate=e.exchange_rate, base_equivalent=base_eq, opening_date=opening_date,
        ))

    # سطر التوازن التلقائي — الفرق بالعملة الأساسية بين كل الأسطر
    # اليدوية، على حساب Clearing المُعتمَد. القيد متوازن دائماً بالتعريف
    # بعد هذا السطر (§6) — لا يعني أن كل مُدخَل صحيح محاسبياً، فقط أن
    # القيد متوازن حسابياً؛ عرض الفرق للمستخدم قبل التأكيد مسؤولية
    # الواجهة لاحقاً (3B-6)، خارج نطاق الخدمة هنا.
    # ملاحظة تقنية: نستعلم JournalLine مباشرة (لا entry.lines) عمداً —
    # الوصول المبكر لـentry.lines هنا كان يُخزِّن مجموعة قديمة (cache)
    # لا تلتقط سطر التوازن المُضاف لاحقاً عبر add_manual_line (تُدرِج
    # عبر entry_id مباشرة، لا عبر entry.lines.append())، فيفشل تحقق
    # is_balanced() داخل post_manual_entry لاحقاً بفارق كامل قيمة السطر
    # الأول رغم وجود السطرين فعلياً بقاعدة البيانات — بق حقيقي اكتُشف
    # واختُبِر أثناء بناء 3B-1، لا افتراضياً.
    from app.models import JournalLine as _JournalLine
    current_lines = session.query(_JournalLine).filter_by(entry_id=entry.id).all()
    total_debit_base = sum(money(l.debit_base) for l in current_lines)
    total_credit_base = sum(money(l.credit_base) for l in current_lines)
    diff = total_debit_base - total_credit_base
    if diff != 0:
        if diff > 0:
            add_manual_line(session, entry, account_id=clearing_account.id, credit=diff, exchange_rate=Decimal("1"))
        else:
            add_manual_line(session, entry, account_id=clearing_account.id, debit=-diff, exchange_rate=Decimal("1"))

    session.expire(entry, ["lines"])  # يفرض إعادة تحميل نظيفة تلتقط كل الأسطر فعلياً قبل post_manual_entry
    post_manual_entry(session, entry)  # يفرض is_balanced() + كل تحققات journal_edit.py الحالية، لا تكرار

    for row in audit_rows:
        session.add(row)
    session.add(Setting(key=OPENING_BALANCES_SETTING_KEY, value=str(opening_date)))
    session.flush()
    return entry


def reverse_opening_account_balances(session: Session, journal_entry: JournalEntry, reversal_date: date) -> JournalEntry:
    """يعكس قيد الأرصدة الافتتاحية للحسابات — يُعيد استخدام
    reverse_manual_entry() الموجودة فعلياً حرفياً (لا آلية عكس جديدة)،
    ثم يمسح Setting['opening_balances_accounts_posted_at'] فقط، للسماح
    بإعادة الإدخال بعده مباشرة (§9). القيد الأصلي لا يُحذَف أبداً —
    يبقى بسجل القيود دائماً، فقط يُعكَس أثره (نفس مبدأ cancel_invoice)."""
    reversal = reverse_manual_entry(session, journal_entry, reversal_date,
                                     description="عكس قيد الأرصدة الافتتاحية للحسابات")
    setting_row = session.get(Setting, OPENING_BALANCES_SETTING_KEY)
    if setting_row is not None:
        session.delete(setting_row)
    session.flush()
    return reversal


# ---------------------------------------------------------------------------
# Phase 3B-2 — الأرصدة الافتتاحية للمخزون
# ---------------------------------------------------------------------------
# نفس فلسفة post_opening_account_balances() تماماً (دفعة واحدة، قيد
# محاسبي واحد، Idempotency على مستوى الدفعة، لا commit/rollback هنا) —
# راجع PHASE3B2_DESIGN_SPEC.md للقيود الـ16 الكاملة. الفارق الوحيد
# المتعمَّد عن نمط 3B-1: أسطر المدين هنا لا تُمرَّر عبر add_manual_line
# بعملتها الأجنبية + exchange_rate لتحويلها هناك — بل تُحوَّل للعملة
# الأساسية هنا أولاً (unit_cost_base = unit_cost_foreign × exchange_rate)
# ثم تُمرَّر جاهزة بـexchange_rate=1. السبب: نفس الخطأ التاريخي الموثَّق
# بـposting.py::_jline_base (WORKFLOW.md §30) — تمرير مبلغ مُحوَّل مسبقاً
# عبر مسار تحويل عام مرة ثانية يضاعف التحويل. هنا تحديداً unit_cost_base
# يُخزَّن حرفياً بـInventoryMovement.unit_cost أيضاً، فيجب أن يكون نفس
# الرقم بالضبط المستخدم بالقيد — تحويل مزدوج هنا يعني قيمتين مختلفتين
# لنفس الحركة (Acceptance Gate #11: debit_base == quantity×unit_cost_base
# بالضبط، لا تقريباً).

from app.models import Warehouse, OpeningInventoryEntry
from decimal import ROUND_HALF_UP

OPENING_INVENTORY_SETTING_KEY = "opening_inventory_posted_at"
_UNIT_COST_QUANT = Decimal("0.0001")  # يطابق دقة عمود InventoryMovement.unit_cost


@dataclass
class OpeningInventoryLineInput:
    """سطر رصيد افتتاحي واحد لمخزون مادة بمستودع محدد."""
    item_id: int
    warehouse_id: int  # إلزامي دائماً — لا افتراضي، لا get_default_warehouse()
    quantity: Decimal
    unit_cost_foreign: Decimal
    currency_code: str | None = None  # None = يرث العملة الأساسية للشركة
    exchange_rate: Decimal = field(default_factory=lambda: Decimal("1"))


def post_opening_inventory(
    session: Session, entries: list[OpeningInventoryLineInput], opening_date: date,
) -> JournalEntry:
    """
    Idempotency: Setting['opening_inventory_posted_at'] — دفعة واحدة لكل
    الشركة بالكامل (ليس لكل item+warehouse بمعزل)، نفس نمط
    post_opening_account_balances() حرفياً.

    نجاح هذه الدالة لا يعني أن العملية أصبحت committed على القرص —
    فقط جاهزة ضمن transaction المُستدعي الحالية (لا session.commit()/
    rollback() هنا، مطابقةً لكل خدمات المشروع). المُستدعي مسؤول عن
    التثبيت أو الإلغاء الكامل.
    """
    already_posted = session.get(Setting, OPENING_INVENTORY_SETTING_KEY)
    if already_posted is not None:
        raise OpeningBalanceError(
            f"الأرصدة الافتتاحية للمخزون مُرحَّلة مسبقاً بتاريخ {already_posted.value} — "
            "لا يجوز ترحيلها مرة ثانية، حتى لو كانت الدفعة الجديدة لمواد مختلفة كلياً. "
            "للتصحيح: اعكس القيد الحالي أولاً عبر reverse_opening_inventory()، ثم أعد "
            "إدخال الدفعة الصحيحة كاملة."
        )
    if not entries:
        raise OpeningBalanceError("لا يمكن ترحيل دفعة أرصدة مخزون افتتاحية فارغة — لم تُدخَل أي أسطر")

    clearing_account = _get_clearing_account(session)
    base_currency = get_base_currency(session)

    # --- تحقق كامل قبل أي أثر بقاعدة البيانات ---
    seen_pairs: set[tuple[int, int]] = set()
    for i, e in enumerate(entries, start=1):
        pair = (e.item_id, e.warehouse_id)
        if pair in seen_pairs:
            raise OpeningBalanceError(
                f"السطر {i}: تكرار (مادة={e.item_id}, مستودع={e.warehouse_id}) بنفس الدفعة — "
                "كل (مادة، مستودع) مسموح مرة واحدة فقط بنفس دفعة الافتتاح."
            )
        seen_pairs.add(pair)

        item = session.get(Item, e.item_id)
        if item is None:
            raise OpeningBalanceError(f"السطر {i}: مادة غير موجودة (id={e.item_id})")
        if not item.is_active:
            raise OpeningBalanceError(f"السطر {i}: المادة ({item.name_ar}) غير نشطة — لا يجوز رصيد افتتاحي لها")

        warehouse = session.get(Warehouse, e.warehouse_id)
        if warehouse is None:
            raise OpeningBalanceError(f"السطر {i}: مستودع غير موجود (id={e.warehouse_id})")
        if not warehouse.is_active:
            raise OpeningBalanceError(f"السطر {i}: المستودع ({warehouse.name_ar}) غير نشط")

        if D(e.quantity) <= 0:
            raise OpeningBalanceError(f"السطر {i}: الكمية يجب أن تكون أكبر من صفر (المُدخَل: {e.quantity})")
        if D(e.unit_cost_foreign) < 0:
            raise OpeningBalanceError(f"السطر {i}: تكلفة الوحدة لا يجوز أن تكون سالبة (المُدخَل: {e.unit_cost_foreign})")

    entry = JournalEntry(
        entry_date=opening_date,
        ref_no=_next_ref_no(session, "JV-OPNINV"),
        description="قيد الأرصدة الافتتاحية للمخزون",
        source_type="opening_inventory", currency_code=base_currency, exchange_rate=Decimal("1"),
        status=JournalEntryStatus.DRAFT,
    )
    session.add(entry)
    session.flush()

    detail_rows: list[OpeningInventoryEntry] = []
    total_value_base = Decimal("0")
    for e in entries:
        item = session.get(Item, e.item_id)
        line_qty = qty(e.quantity)
        unit_cost_base = (D(e.unit_cost_foreign) * rate_(e.exchange_rate)).quantize(
            _UNIT_COST_QUANT, rounding=ROUND_HALF_UP
        )
        line_value_base = money(line_qty * unit_cost_base)
        total_value_base += line_value_base

        # exchange_rate=1 عمداً: line_value_base مُحوَّل للعملة الأساسية
        # مسبقاً بالأعلى — تمريره بعملته الأجنبية الأصلية مع exchange_rate
        # الحقيقي هنا كان يُحوِّله مرتين (راجع تعليق رأس القسم).
        #
        # حالة حدّية غير مذكورة صراحة بالمواصفة: unit_cost_foreign=0
        # مقبول صراحة (§4)، لكن add_manual_line/journal_edit.py يرفض أي
        # سطر قيمته صفر (قاعدة أعمق بالمحرك: سطر قيد بلا مدين ولا دائن
        # عديم المعنى محاسبياً — نفس القاعدة المطبَّقة بكل مكان آخر
        # بالنظام، لا استثناء يُخترَع هنا). القرار: مادة تكلفتها صفر
        # تُسجَّل كمية حقيقية (InventoryMovement + سطر تفصيلي) بلا أي
        # أثر بالقيد المحاسبي (لا سطر Dr لها، ولا تُحتسَب بالإجمالي الذي
        # يوازيه Clearing) — القيمة صفر فعلياً، فلا شيء يُرحَّل عنها
        # محاسبياً، تماماً كما لو لم تكن موجودة بالقيد رغم وجودها فعلياً
        # بالمخزون. يستحق تأكيداً صريحاً من Bilal لأنه قرار غير مذكور
        # نصاً بالمواصفة المعتمدة.
        if line_value_base > 0:
            add_manual_line(
                session, entry, account_id=item.inventory_account_id,
                debit=line_value_base, credit=Decimal("0"), exchange_rate=Decimal("1"),
            )

        movement = InventoryMovement(
            item_id=item.id, warehouse_id=e.warehouse_id, direction=MovementDirection.IN,
            quantity=line_qty, unit_cost=unit_cost_base, movement_date=opening_date,
            source_type="opening_inventory", source_id=entry.id,
            note="رصيد افتتاحي للمخزون",
        )
        session.add(movement)
        session.flush()

        detail_rows.append(OpeningInventoryEntry(
            journal_entry_id=entry.id, item_id=item.id, warehouse_id=e.warehouse_id,
            inventory_movement_id=movement.id, quantity=line_qty,
            unit_cost_foreign=money(e.unit_cost_foreign), currency_code=e.currency_code or base_currency,
            exchange_rate=e.exchange_rate, unit_cost_base=unit_cost_base, opening_date=opening_date,
        ))

    # سطر التوازن (Clearing) — لا COGS، لا Revenue، لا Sales: طرف مقابل
    # واحد فقط لكل قيمة المخزون المُدخَلة (§13 بالمواصفة). لو كانت كل
    # مواد الدفعة تكلفتها صفر (حالة نادرة)، لا قيمة توازن ولا سطر Clearing
    # ولا قيد محاسبي إطلاقاً — فقط حركات مخزون بكمية حقيقية وتكلفة صفر.
    if total_value_base > 0:
        add_manual_line(session, entry, account_id=clearing_account.id,
                         credit=money(total_value_base), exchange_rate=Decimal("1"))

    session.expire(entry, ["lines"])  # نفس الإصلاح الموثَّق بـpost_opening_account_balances أعلاه
    if entry.lines:
        post_manual_entry(session, entry)
    else:
        entry.status = JournalEntryStatus.POSTED
        session.flush()

    for row in detail_rows:
        session.add(row)
    session.add(Setting(key=OPENING_INVENTORY_SETTING_KEY, value=str(opening_date)))
    session.flush()
    return entry


def reverse_opening_inventory(session: Session, journal_entry: JournalEntry, reversal_date: date) -> JournalEntry:
    """يعكس قيد الأرصدة الافتتاحية للمخزون + ينشئ حركات InventoryMovement
    عكسية (OUT) بنفس الكمية والتكلفة التاريخية لكل حركة IN أصلية بالضبط
    (نفس نمط cancel_invoice — عكس حرفي، لا إعادة حساب).

    فحص أول إلزامي: يجب أن يكون `journal_entry.source_type ==
    "opening_inventory"` تحديداً — لا "opening_balance" (قيد حسابات
    3B-1)، القيمتان مختلفتان عمداً بالتصميم لمنع تمرير قيد من النوع
    الخطأ لهذه الدالة بالغلط.

    يُرفَض العكس بالكامل (لا عكس جزئي) لو وُجدت — لأي (item, warehouse)
    بالدفعة — أي حركة OUT لاحقة (بتاريخ ≥ تاريخ الافتتاح) اعتمدت فعلياً
    على متوسط يشمل رصيد الافتتاح هذا؛ الفحص لكل (item, warehouse) بمعزل
    تماماً (مستودع مختلف لنفس المادة لا يمنع العكس). السبب: عكس حركة
    استُخدمت فعلاً بحساب COGS لبيع لاحق يكسر السلسلة التاريخية
    Opening→Movement→Average Cost→COGS→Journal بأثر رجعي (WORKFLOW.md §39:
    "History is never re-priced retroactively").
    """
    if journal_entry.source_type != "opening_inventory":
        raise OpeningBalanceError(
            f"القيد {journal_entry.ref_no} ليس قيد افتتاح مخزون (source_type="
            f"'{journal_entry.source_type}') — لا يجوز عكسه عبر reverse_opening_inventory()"
        )

    detail_rows = session.query(OpeningInventoryEntry).filter_by(journal_entry_id=journal_entry.id).all()
    if not detail_rows:
        raise OpeningBalanceError(f"القيد {journal_entry.ref_no} بلا أسطر افتتاح مخزون تفصيلية — حالة غير متسقة")

    for row in detail_rows:
        blocking = session.query(InventoryMovement).filter(
            InventoryMovement.item_id == row.item_id,
            InventoryMovement.warehouse_id == row.warehouse_id,
            InventoryMovement.direction == MovementDirection.OUT,
            InventoryMovement.movement_date >= row.opening_date,
        ).first()
        if blocking is not None:
            item = session.get(Item, row.item_id)
            warehouse = session.get(Warehouse, row.warehouse_id)
            raise OpeningBalanceError(
                f"لا يمكن عكس الرصيد الافتتاحي للمادة ({item.name_ar}) بالمستودع "
                f"({warehouse.name_ar}) — توجد حركة خروج لاحقة اعتمدت فعلياً على متوسط "
                "يشمل هذا الرصيد (بيع/تحويل بعد تاريخ الافتتاح). عكسه الآن يكسر السلسلة "
                "التاريخية Opening→Movement→Average Cost→COGS→Journal بأثر رجعي."
            )

    reversal = reverse_manual_entry(session, journal_entry, reversal_date,
                                     description="عكس قيد الأرصدة الافتتاحية للمخزون")

    reversal_movements = [
        InventoryMovement(
            item_id=row.item_id, warehouse_id=row.warehouse_id, direction=MovementDirection.OUT,
            quantity=row.quantity, unit_cost=row.unit_cost_base, movement_date=reversal_date,
            source_type="opening_inventory_reverse", source_id=journal_entry.id,
            note="عكس رصيد افتتاحي للمخزون",
        )
        for row in detail_rows
    ]
    session.add_all(reversal_movements)

    setting_row = session.get(Setting, OPENING_INVENTORY_SETTING_KEY)
    if setting_row is not None:
        session.delete(setting_row)
    session.flush()
    return reversal
