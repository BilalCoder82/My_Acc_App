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

النطاق: **حسابات عامة فقط** (3B-1). المخزون الافتتاحي (دالة
`set_item_opening_balance` أدناه، موجودة مسبقاً، غير مُوسَّعة هذه
الجولة عمداً) وأرصدة العملاء/الموردين الافتتاحية التفصيلية مؤجَّلة
لـ3B-2/3B-3 بالترتيب المُعتمَد — لا تُضَف هنا قبل ذلك.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from sqlalchemy.orm import Session

from app.models import InventoryMovement, MovementDirection, Item
from app.services.posting import get_default_warehouse


def set_item_opening_balance(
    session: Session, item_id: int, quantity: float, unit_cost: float,
    as_of_date: date | None = None, warehouse_id: int | None = None,
) -> InventoryMovement:
    """
    يسجّل رصيد افتتاحي لمادة معيّنة. يجب استدعاؤها قبل أي حركة بيع/شراء
    لهذه المادة، وبتاريخ أقدم من أي فاتورة — وإلا ينكسر ترتيب حساب
    المتوسط المرجّح (average cost يعتمد على الترتيب الزمني للحركات).

    warehouse_id: المستودع الذي يبدأ فيه الرصيد فعلياً — اختياري، يسقط
    للمستودع الافتراضي إن تُرك فارغاً (نفس نمط _invoice_warehouse_id،
    راجع WORKFLOW.md §46 — التكلفة منفصلة لكل مستودع، فالرصيد الافتتاحي
    يجب أن يُنسَب لمستودعه الصحيح لا الافتراضي دائماً بصمت).
    """
    item = session.get(Item, item_id)
    if item is None:
        raise ValueError(f"مادة غير موجودة: id={item_id}")

    existing_opening = session.query(InventoryMovement).filter_by(
        item_id=item_id, source_type="opening_balance"
    ).first()
    if existing_opening is not None:
        raise ValueError(
            f"رصيد افتتاحي مسجَّل مسبقاً للمادة '{item.name_ar}' — "
            "لا يُسمح بتكراره، عدّل السطر الموجود مباشرة إن لزم."
        )

    movement = InventoryMovement(
        item_id=item_id,
        warehouse_id=warehouse_id or get_default_warehouse(session).id,
        direction=MovementDirection.IN,
        quantity=quantity,
        unit_cost=unit_cost,
        movement_date=as_of_date or date.today(),
        source_type="opening_balance",
        note="رصيد افتتاحي",
    )
    session.add(movement)
    session.flush()
    return movement


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
from app.services.money import money, rate as rate_

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
