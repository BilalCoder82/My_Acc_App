"""
Invoice Posting Engine
=======================
يحوّل الفاتورة (Invoice) إلى قيد محاسبي متوازن (JournalEntry) + حركات مخزون.
المرتجعات لا تولّد قيداً جديداً مستقلاً — بل تعكس القيد الأصلي بالضبط.

كل الحسابات المالية هنا بـDecimal حصراً (app/services/money.py) — لا float.

الإعدادات المطلوبة مسبقاً بجدول Settings (Setting.key):
    default_cash_account_id      -> حساب الصندوق/الذمم الافتراضي
    default_sales_account_id     -> حساب المبيعات
    default_purchases_tax_account_id / default_sales_tax_account_id
"""

from __future__ import annotations
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import (
    Invoice, InvoiceKind, InvoiceStatus,
    JournalEntry, JournalLine, JournalEntryStatus,
    InventoryMovement, MovementDirection,
    Item, Setting, Warehouse,
)
from app.services.parties import get_or_create_party_account
from app.services.invoice_calc import compute_invoice_totals
from app.services.invoice_validation import validate_invoice_for_posting, InvoiceValidationError
from app.services.item_queries import get_item_stock_summary
from app.services.money import D, money
from app.services.sanity_guard import assert_reasonable_conversion


class PostingError(Exception):
    pass


def _get_setting(session: Session, key: str) -> int:
    row = session.get(Setting, key)
    if row is None:
        raise PostingError(f"إعداد مفقود بالنظام: {key} — راجع شاشة الإعدادات")
    return int(row.value)


def _next_ref_no(session: Session, prefix: str) -> str:
    count = session.query(JournalEntry).filter(
        JournalEntry.ref_no.like(f"{prefix}-%")
    ).count()
    return f"{prefix}-{count + 1:05d}"


def get_default_warehouse(session: Session) -> Warehouse:
    """يرجّع المستودع الرئيسي، وينشئه تلقائياً إن لم يكن موجوداً."""
    wh = session.query(Warehouse).filter_by(name_ar="المستودع الرئيسي").first()
    if wh is None:
        wh = Warehouse(name_ar="المستودع الرئيسي", is_active=True)
        session.add(wh)
        session.flush()
    return wh


def _invoice_warehouse_id(session: Session, invoice: Invoice) -> int:
    return invoice.warehouse_id or get_default_warehouse(session).id


def _jline(account_id: int, debit: Decimal, credit: Decimal, exchange_rate) -> JournalLine:
    """ينشئ سطر قيد مع حساب القيمة بالعملة الأساسية تلقائياً. debit/credit هنا
    دائماً بعملة المستند الأصلية (الفاتورة/سند القيد) — يُحوَّلان معاً بضربهما
    بـexchange_rate. لا تستخدمها لمبلغ محوَّل للعملة الأساسية مسبقاً؛ استخدم
    _jline_base بدلاً منها (راجع تعليقها لسبب وجود الاثنتين)."""
    d, c, rate = money(debit), money(credit), D(exchange_rate)
    debit_base, credit_base = money(d * rate), money(c * rate)
    # حارس اتساق (لا حجم — راجع sanity_guard.py): يتحقق فقط أن القيمة
    # المخزَّنة تطابق raw×rate، بصرف النظر عن حجم المبلغ. تكلفته زهيدة
    # وتبقيه فعّالاً ضد أي انحراف مستقبلي في هذا الحساب تحديداً.
    assert_reasonable_conversion(
        raw_amount=d, stored_base_amount=debit_base, exchange_rate=rate,
        context=f"_jline debit account={account_id}",
    )
    assert_reasonable_conversion(
        raw_amount=c, stored_base_amount=credit_base, exchange_rate=rate,
        context=f"_jline credit account={account_id}",
    )
    return JournalLine(
        account_id=account_id, debit=d, credit=c,
        debit_base=debit_base, credit_base=credit_base,
    )


def _jline_base(account_id: int, debit_base: Decimal, credit_base: Decimal) -> JournalLine:
    """**حرج**: لسطر قيمته مُحوَّلة للعملة الأساسية مسبقاً بالفعل — تحديداً
    COGS وحركات المخزون (`_average_cost`/`_return_unit_cost`)، لأن كلاهما
    يقرآن مباشرة من InventoryMovement.unit_cost المخزَّن بالعملة الأساسية
    دائماً (راجع WORKFLOW.md §29). هذا المبلغ **لا علاقة له بعملة الفاتورة
    أو سعر صرفها إطلاقاً** — تكلفة المادة مستقلة تماماً عن عملة عملية
    البيع/الشراء الحالية. تمرير هذا المبلغ عبر _jline مع exchange_rate
    الفاتورة كان يُحوِّله **مرتين** (خطأ حقيقي خطير — راجع WORKFLOW.md §30:
    مبلغ 1,200,000 كان يتحوّل بالخطأ إلى 21,600,000,000 بفاتورة USD).
    debit == debit_base دائماً هنا (لا "عملة أصلية" منفصلة ذات معنى للتكلفة)."""
    d, c = money(debit_base), money(credit_base)
    return JournalLine(account_id=account_id, debit=d, credit=c, debit_base=d, credit_base=c)


def _average_cost(session: Session, item_id: int, warehouse_id: int) -> Decimal:
    """كلفة الوحدة الحالية **لمستودع محدد تحديداً** (WORKFLOW.md §46 —
    التكلفة منفصلة لكل مستودع، لا موحّدة على مستوى الشركة). لا قيمة
    افتراضية لـwarehouse_id عمداً — كل استدعاء محاسبي فعلي يجب أن يعرف
    مستودعه بوضوح، لمنع أي انزلاق صامت لخلط التكلفة بين المستودعات مرة
    أخرى. مصدر الحساب الفعلي app/services/item_queries.py
    (get_item_stock_summary) — نفس الخوارزمية بالضبط، بمكان واحد فقط."""
    return get_item_stock_summary(session, item_id, warehouse_id=warehouse_id).average_cost


def post_sales_invoice(session: Session, invoice: Invoice, is_cash: bool = True) -> JournalEntry:
    if invoice.status == InvoiceStatus.POSTED:
        raise PostingError("الفاتورة مرحّلة أصلاً — لا يمكن ترحيلها مرتين")
    try:
        validate_invoice_for_posting(invoice)
    except InvoiceValidationError as e:
        raise PostingError(str(e))

    if is_cash:
        cash_or_ar = _get_setting(session, "default_cash_account_id")
    else:
        party_account = get_or_create_party_account(session, invoice.party_name, is_customer=True)
        cash_or_ar = party_account.id
    # حساب مبيعات المادة (Item.sales_account_id) هو المصدر الأول الآن —
    # الإعداد العام default_sales_account_id احتياطي فقط لمادة لم تُحدَّد
    # لها حساب مبيعات خاص. راجع WORKFLOW.md §27 لسبب هذا التغيير.
    default_sales_acc = _get_setting(session, "default_sales_account_id")
    tax_acc = _get_setting(session, "default_sales_tax_account_id")
    warehouse_id = _invoice_warehouse_id(session, invoice)

    entry = JournalEntry(
        entry_date=invoice.invoice_date,
        ref_no=_next_ref_no(session, "JE-SAL"),
        description=f"فاتورة بيع رقم {invoice.invoice_no} — {invoice.party_name}",
        source_type="sales_invoice", source_id=invoice.id,
        currency_code=invoice.currency_code, exchange_rate=invoice.exchange_rate,
        status=JournalEntryStatus.POSTED,
    )

    total_sales, total_tax = Decimal("0"), Decimal("0")
    # مُجمَّعة بحساب المادة الفعلي — فاتورة بعدة مواد بحسابات مبيعات/مخزون/تكلفة
    # مختلفة تُنتج سطر قيد منفصلاً لكل حساب مختلف فعلاً، لا سطراً واحداً
    # بحسابات أول مادة فقط مطبَّقة على إجمالي كل السطور (كان هذا خطأً موجوداً
    # فعلياً بالنسخة السابقة لكل فاتورة متعددة المواد بحسابات مختلفة — راجع
    # WORKFLOW.md §27).
    sales_credits: dict[int, Decimal] = {}
    cogs_debits: dict[int, Decimal] = {}
    inventory_credits: dict[int, Decimal] = {}
    totals = compute_invoice_totals(invoice)

    for line_total in totals.lines:
        item = session.get(Item, line_total.line.item_id)
        total_sales += line_total.net_after_all_discounts
        total_tax += line_total.tax_amount

        sales_acc_id = item.sales_account_id or default_sales_acc
        sales_credits[sales_acc_id] = sales_credits.get(sales_acc_id, Decimal("0")) + line_total.net_after_all_discounts

        unit_cost = _average_cost(session, item.id, warehouse_id)
        line_cogs = money(unit_cost * D(line_total.line.quantity))
        cogs_debits[item.cogs_account_id] = cogs_debits.get(item.cogs_account_id, Decimal("0")) + line_cogs
        inventory_credits[item.inventory_account_id] = inventory_credits.get(item.inventory_account_id, Decimal("0")) + line_cogs

        session.add(InventoryMovement(
            item_id=item.id, warehouse_id=warehouse_id, direction=MovementDirection.OUT,
            quantity=line_total.line.quantity, unit_cost=unit_cost,
            movement_date=invoice.invoice_date, source_type="sales_invoice", source_id=invoice.id,
        ))

    entry.lines = [_jline(cash_or_ar, total_sales + total_tax, Decimal("0"), invoice.exchange_rate)]
    for acc_id, amount in sales_credits.items():
        entry.lines.append(_jline(acc_id, Decimal("0"), amount, invoice.exchange_rate))
    if total_tax:
        entry.lines.append(_jline(tax_acc, Decimal("0"), total_tax, invoice.exchange_rate))
    for acc_id, amount in cogs_debits.items():
        if amount:
            entry.lines.append(_jline_base(acc_id, amount, Decimal("0")))
    for acc_id, amount in inventory_credits.items():
        if amount:
            entry.lines.append(_jline_base(acc_id, Decimal("0"), amount))

    if not entry.is_balanced():
        raise PostingError("خطأ داخلي: القيد غير متوازن — لا يُرحّل")

    invoice.status = InvoiceStatus.POSTED
    session.add(entry)
    session.flush()
    invoice.journal_entry_id = entry.id
    return entry


def post_purchase_invoice(session: Session, invoice: Invoice, is_cash: bool = True) -> JournalEntry:
    """الشراء لا يولّد قيد COGS — البضاعة تدخل المخزون فقط."""
    if invoice.status == InvoiceStatus.POSTED:
        raise PostingError("الفاتورة مرحّلة أصلاً — لا يمكن ترحيلها مرتين")
    try:
        validate_invoice_for_posting(invoice)
    except InvoiceValidationError as e:
        raise PostingError(str(e))

    if is_cash:
        cash_or_ap = _get_setting(session, "default_cash_account_id")
    else:
        party_account = get_or_create_party_account(session, invoice.party_name, is_customer=False)
        cash_or_ap = party_account.id
    tax_acc = _get_setting(session, "default_purchases_tax_account_id")
    warehouse_id = _invoice_warehouse_id(session, invoice)

    entry = JournalEntry(
        entry_date=invoice.invoice_date,
        ref_no=_next_ref_no(session, "JE-PUR"),
        description=f"فاتورة شراء رقم {invoice.invoice_no} — {invoice.party_name}",
        source_type="purchase_invoice", source_id=invoice.id,
        currency_code=invoice.currency_code, exchange_rate=invoice.exchange_rate,
        status=JournalEntryStatus.POSTED,
    )

    total_purchase, total_tax = Decimal("0"), Decimal("0")
    inventory_debits: dict[int, Decimal] = {}
    totals = compute_invoice_totals(invoice)

    for line_total in totals.lines:
        item = session.get(Item, line_total.line.item_id)
        total_purchase += line_total.net_after_all_discounts
        total_tax += line_total.tax_amount

        inventory_debits[item.inventory_account_id] = (
            inventory_debits.get(item.inventory_account_id, Decimal("0")) + line_total.net_after_all_discounts
        )

        q = D(line_total.line.quantity)
        # **حرج**: تكلفة الوحدة المخزَّنة بـInventoryMovement يجب أن تكون
        # دائماً بالعملة الأساسية — نفس مبدأ WORKFLOW.md §23 ("تكلفة المخزون
        # تُسجَّل بالعملة الأساسية وقت الاقتناء"). net_after_all_discounts
        # بالأعلى بعملة الفاتورة الأصلية (USD مثلاً) بلا أي تحويل — قبل هذا
        # الإصلاح كانت تُخزَّن كما هي دون ضرب بسعر الصرف، فيختلط دولار خام
        # مع ليرة سورية بالمتوسط المرجّح لأي مادة تُشترى أحياناً بعملة أساسية
        # وأحياناً بعملة أجنبية — خطأ حقيقي انكشف فقط باختبار End-to-End
        # بفاتورة شراء دولارية فعلية، راجع WORKFLOW.md §29.
        net_in_base = money(line_total.net_after_all_discounts * D(invoice.exchange_rate))
        unit_cost_after_discount = (net_in_base / q) if q else Decimal("0")
        session.add(InventoryMovement(
            item_id=item.id, warehouse_id=warehouse_id, direction=MovementDirection.IN,
            quantity=line_total.line.quantity, unit_cost=unit_cost_after_discount,
            movement_date=invoice.invoice_date, source_type="purchase_invoice", source_id=invoice.id,
        ))

    entry.lines = [_jline(cash_or_ap, Decimal("0"), total_purchase + total_tax, invoice.exchange_rate)]
    for inv_acc_id, amount in inventory_debits.items():
        entry.lines.append(_jline(inv_acc_id, amount, Decimal("0"), invoice.exchange_rate))
    if total_tax:
        entry.lines.append(_jline(tax_acc, total_tax, Decimal("0"), invoice.exchange_rate))

    if not entry.is_balanced():
        raise PostingError("خطأ داخلي: القيد غير متوازن — لا يُرحّل")

    invoice.status = InvoiceStatus.POSTED
    session.add(entry)
    session.flush()
    invoice.journal_entry_id = entry.id
    return entry


def post_return(session: Session, original_invoice: Invoice, return_invoice: Invoice) -> JournalEntry:
    """يعكس القيد الأصلي بالضبط بدل توليد قيد مستقل، لضمان الدقة."""
    if original_invoice.journal_entry_id is None:
        raise PostingError("الفاتورة الأصلية غير مرحّلة — لا يمكن عكسها")

    original_entry = session.get(JournalEntry, original_invoice.journal_entry_id)
    reversal = JournalEntry(
        entry_date=return_invoice.invoice_date,
        ref_no=_next_ref_no(session, "JE-RET"),
        description=f"عكس قيد مرتجع — أصل الفاتورة {original_invoice.invoice_no}",
        source_type="return_invoice", source_id=return_invoice.id,
        is_reversal_of=original_entry.id,
        currency_code=original_entry.currency_code, exchange_rate=original_entry.exchange_rate,
        status=JournalEntryStatus.POSTED,
    )
    reversal.lines = [
        _jline(l.account_id, D(l.credit), D(l.debit), original_entry.exchange_rate)
        for l in original_entry.lines
    ]
    if not reversal.is_balanced():
        raise PostingError("خطأ داخلي: قيد العكس غير متوازن")

    return_invoice.status = InvoiceStatus.POSTED
    session.add(reversal)
    session.flush()
    return_invoice.journal_entry_id = reversal.id
    return reversal
