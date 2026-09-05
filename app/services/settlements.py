"""
app/services/settlements.py
==============================
القبض (Receipt) والدفع (Payment) وتسوية الفواتير — راجع WORKFLOW.md §42
للقواعد المحاسبية الكاملة قبل تعديل أي شيء هنا.

Phase 3B-3 (راجع PHASE3B3_DESIGN_SPEC.md قبل أي تعديل إضافي): أصبح
القبض/الدفع الواحد قادراً على تسوية أكثر من هدف معاً (فاتورة و/أو رصيد
افتتاحي لعميل/مورد) عبر SettlementAllocation، بدل الاقتصار على فاتورة
واحدة فقط. post_receipt()/post_payment() القديمتان (فاتورة واحدة، بلا
تغيير بالتوقيع) تبقيان كما هما تماماً — تُبنيان الآن داخلياً فوق المحرك
المُعمَّم (allocations=[هدف واحد ضمنياً]) بدل تكرار المنطق، لكن كل
الاستدعاءات الحالية بالمشروع (9 مواقع) تعمل بلا أي تغيير مطلوب فيها.

قاعدة التصميم الأساسية: كل تسوية = (amount_foreign, settlement_rate)
بعملة موحَّدة لكل التسوية بأكملها (Settlement.currency_code). القيمة
الأساسية الفعلية لكل هدف = amount_foreign المخصَّص لذلك الهدف × سعره
الدفتري الأصلي (فرق الصرف = الفرق بين هذا وبين سعر التسوية اليوم)،
والجزء غير المخصَّص (الفائض) يُسجَّل بسعر التسوية نفسه مباشرة (لا فرق
صرف له بالتعريف — لا سعر تاريخي "أصلي" يخصّه، §2.5 بالمواصفة).
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    Invoice, InvoiceKind, InvoiceStatus, JournalEntry, JournalEntryStatus, JournalLine,
    Settlement, SettlementAllocation, Account, AccountSubtype,
    OpeningPartyEntry, OpeningPartyKind,
)
from app.services.money import D, money
from app.services.invoice_calc import compute_invoice_totals
from app.services.posting import _jline, _jline_base, _get_setting
from app.services.opening_party_balances import get_opening_party_entry_balance_due


def _jline_party(account_id: int, debit_foreign: Decimal, credit_foreign: Decimal,
                  debit_base: Decimal, credit_base: Decimal) -> JournalLine:
    """سطر حساب الطرف (عميل/مورد) بمبلغ أجنبي وأساسي مُحدَّدين صراحة
    ومستقلين — لا عبر _jline (يفرض base=raw×rate بمعدل واحد فقط) ولا
    _jline_base (يفرض raw=base). ضروري هنا تحديداً لأن سطر حساب الطرف
    بقبضة متعددة الأهداف "مُدمَج" فعلياً من أجزاء بأسعار مختلفة (كل
    هدف بسعره الدفتري الأصلي + الفائض غير المخصَّص بسعر التسوية) —
    القيمة الأساسية الناتجة (base) ليست raw×أي سعر واحد بالتعريف، بينما
    القيمة الأجنبية (raw) تبقى المبلغ الفعلي المُستلَم/المدفوع بعملة
    الطرف نفسها (== عملة التسوية، مفروضة بـ§1.11). راجع
    PHASE3B3_DESIGN_SPEC.md §5 — bug حقيقي اكتُشف بالاختبار المستقل
    (Oracle) لا افتراضاً: استخدام _jline_base هنا كان يجعل raw=base
    خطأً (يُخرِج get_party_currency_balance() برقم foreign_balance
    غير صحيح — يساوي القيمة المُدمَجة بدل المبلغ الأجنبي الفعلي)."""
    return JournalLine(
        account_id=account_id,
        debit=money(debit_foreign), credit=money(credit_foreign),
        debit_base=money(debit_base), credit_base=money(credit_base),
    )


class SettlementError(Exception):
    pass


@dataclass
class AllocationInput:
    """تخصيص واحد ضمن قبض/دفع واحد — هدف واحد بالضبط (Exclusive Arc):
    invoice_id أو opening_party_entry_id، لا كلاهما ولا لا شيء."""
    amount_foreign: Decimal
    invoice_id: int | None = None
    opening_party_entry_id: int | None = None


@dataclass
class PartyCurrencyBalance:
    """رصيد حساب طرف (عميل/مورد) بعملة واحدة محدَّدة — لا الرصيد
    الإجمالي متعدد العملات. دالة محايدة تماماً تجاه نوع الطرف
    (§4.3/§13 بالمواصفة): موجب = دائن (لصالح الطرف)، سالب = مدين."""
    foreign_balance: Decimal
    base_balance: Decimal


def _invoice_receivable_or_payable_account_id(session: Session, invoice: Invoice) -> int:
    """
    الحساب الذي استُخدم فعلياً كطرف مقابل عند ترحيل الفاتورة (عميل أو
    مورد)، مأخوذ من السطر الأول لقيدها الفعلي — لا بإعادة استنتاجه من
    party_name (قد لا يطابق الحساب الفعلي المُستخدَم لو تغيّر الإعداد
    لاحقاً). إن كان هذا الحساب هو الصندوق الافتراضي نفسه، فالفاتورة
    نقدية أصلاً ولا رصيد مستحق للتسوية.

    subtype يجب أن يكون CUSTOMER أو SUPPLIER تحديداً، وallow_reconciliation=True
    معاً — كلاهما إلزامي.
    """
    if invoice.journal_entry_id is None:
        raise SettlementError(f"الفاتورة {invoice.invoice_no} غير مرحّلة — لا رصيد للتسوية")
    entry: JournalEntry = session.get(JournalEntry, invoice.journal_entry_id)
    first_line = entry.lines[0]
    default_cash = _get_setting(session, "default_cash_account_id")
    if first_line.account_id == default_cash:
        raise SettlementError(
            f"الفاتورة {invoice.invoice_no} نقدية (سُدِّدت بالكامل وقت الترحيل) — لا رصيد مستحق للتسوية"
        )
    account = session.get(Account, first_line.account_id)
    if account is None or account.subtype not in (AccountSubtype.CUSTOMER, AccountSubtype.SUPPLIER):
        raise SettlementError(
            f"الحساب المرتبط بفاتورة {invoice.invoice_no} "
            f"({account.name_ar if account else '?'}) ليس عميلاً أو مورداً (subtype) — لا تسوية له"
        )
    if not account.allow_reconciliation:
        raise SettlementError(
            f"الحساب المرتبط بفاتورة {invoice.invoice_no} ({account.name_ar}) "
            "غير مصرَّح له بالتسوية (allow_reconciliation=False) — راجع بطاقة الحساب"
        )
    return first_line.account_id


def get_invoice_balance_due(session: Session, invoice: Invoice) -> Decimal:
    """الرصيد المستحق بعملة الفاتورة نفسها — يُحسَب ديناميكياً دائماً.

    Phase 3B-3: يُجمِّع الآن من SettlementAllocation.filter_by(invoice_id=...)
    بدل Settlement.filter_by(invoice_id=...) مباشرة — invoice_id انتقل
    بالكامل لطبقة التخصيص (PHASE3B3_DESIGN_SPEC.md §1.10/§9).
    """
    if invoice.status != InvoiceStatus.POSTED:
        raise SettlementError(
            f"الفاتورة {invoice.invoice_no} غير مرحّلة (POSTED) — لا مفهوم لرصيد مستحق قبل الترحيل"
        )
    totals = compute_invoice_totals(invoice)
    return totals.grand_total - _sum_invoice_allocations(session, invoice.id)


def _sum_invoice_allocations(session: Session, invoice_id: int) -> Decimal:
    rows = session.query(SettlementAllocation).filter_by(invoice_id=invoice_id).all()
    return sum((D(r.amount_foreign) for r in rows), Decimal("0"))


def get_party_currency_balance(session: Session, party_account_id: int, currency_code: str) -> PartyCurrencyBalance:
    """رصيد حساب الطرف بعملة واحدة محدَّدة فقط — لا الرصيد الإجمالي
    متعدد العملات (§4.3 بالمواصفة). استعلام مستقل تماماً عن
    get_account_statement (ذاك بالعملة الأساسية فقط)، لا تعديل عليه."""
    lines = (
        session.query(JournalLine)
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .filter(JournalLine.account_id == party_account_id, JournalEntry.status == JournalEntryStatus.POSTED)
        .all()
    )
    foreign_balance = Decimal("0")
    base_balance = Decimal("0")
    for line in lines:
        line_ccy = line.line_currency_code or line.entry.currency_code
        if line_ccy != currency_code:
            continue
        foreign_balance += D(line.credit) - D(line.debit)
        base_balance += D(line.credit_base) - D(line.debit_base)
    return PartyCurrencyBalance(foreign_balance=foreign_balance, base_balance=base_balance)


def _resolve_allocation_target(session: Session, kind: str, alloc: AllocationInput) -> dict:
    has_invoice = alloc.invoice_id is not None
    has_opening = alloc.opening_party_entry_id is not None
    if has_invoice == has_opening:
        raise SettlementError(
            "كل تخصيص يجب أن يشير إلى فاتورة أو رصيد افتتاحي واحد بالضبط (Exclusive Arc) — "
            "لا كليهما، ولا لا شيء"
        )
    is_receipt = (kind == "receipt")
    if has_invoice:
        invoice = session.get(Invoice, alloc.invoice_id)
        if invoice is None:
            raise SettlementError(f"فاتورة غير موجودة (id={alloc.invoice_id})")
        expected_invoice_kind = InvoiceKind.SALES if is_receipt else InvoiceKind.PURCHASE
        if invoice.kind != expected_invoice_kind:
            raise SettlementError(
                f"فاتورة {invoice.invoice_no} من نوع {invoice.kind} — لا تصلح هدفاً لـ"
                f"{'قبض' if is_receipt else 'دفع'}"
            )
        return dict(
            party_account_id=_invoice_receivable_or_payable_account_id(session, invoice),
            currency_code=invoice.currency_code, rate=D(invoice.exchange_rate),
            remaining=get_invoice_balance_due(session, invoice),
        )
    else:
        ope = session.get(OpeningPartyEntry, alloc.opening_party_entry_id)
        if ope is None:
            raise SettlementError(f"رصيد افتتاحي غير موجود (id={alloc.opening_party_entry_id})")
        expected_kind = OpeningPartyKind.RECEIVABLE if is_receipt else OpeningPartyKind.PAYABLE
        if ope.kind != expected_kind:
            raise SettlementError(
                f"الرصيد الافتتاحي ({ope.reference}) من نوع {ope.kind} — لا يصلح هدفاً لـ"
                f"{'قبض' if is_receipt else 'دفع'}"
            )
        return dict(
            party_account_id=ope.party_account_id, currency_code=ope.currency_code,
            rate=D(ope.exchange_rate), remaining=get_opening_party_entry_balance_due(session, ope),
        )


def _post_settlement_multi(
    session: Session, *, kind: str, party_account_id: int, amount_foreign: Decimal,
    currency_code: str, settlement_rate: Decimal, settlement_date: date, cash_account_id: int,
    allocations: list[AllocationInput], description: str,
) -> JournalEntry:
    """المحرك المُعمَّم — يبني post_receipt()/post_payment() القديمتان
    فوقه (تخصيص واحد ضمني)، ويُستخدَم مباشرة للحالة متعددة الأهداف."""
    is_receipt = (kind == "receipt")
    party_account = session.get(Account, party_account_id)
    if party_account is None:
        raise SettlementError(f"حساب غير موجود (id={party_account_id})")
    required_subtype = AccountSubtype.CUSTOMER if is_receipt else AccountSubtype.SUPPLIER
    if party_account.subtype != required_subtype:
        raise SettlementError(
            f"{'القبض' if is_receipt else 'الدفع'} يتطلب حساب "
            f"{'عميل' if is_receipt else 'مورد'} — الحساب ({party_account.name_ar}) "
            f"نوعه {party_account.subtype}"
        )
    if not party_account.allow_reconciliation:
        raise SettlementError(f"الحساب ({party_account.name_ar}) غير مصرَّح له بالتسوية (allow_reconciliation=False)")

    amount_foreign = D(amount_foreign)
    if amount_foreign <= 0:
        raise SettlementError("مبلغ التسوية يجب أن يكون أكبر من صفر")

    resolved: list[tuple[AllocationInput, dict]] = []
    total_allocated = Decimal("0")
    for alloc in allocations:
        target = _resolve_allocation_target(session, kind, alloc)
        if target["party_account_id"] != party_account_id:
            raise SettlementError("كل الأهداف المخصَّصة بنفس التسوية يجب أن تخص حساب الطرف نفسه بالضبط")
        if target["currency_code"] != currency_code:
            raise SettlementError(
                f"عملة الهدف ({target['currency_code']}) يجب أن تطابق عملة التسوية "
                f"({currency_code}) — لا يُسمح بخلط عملات داخل تسوية واحدة (§1.11)"
            )
        alloc_amount = D(alloc.amount_foreign)
        if alloc_amount <= 0:
            raise SettlementError("مبلغ كل تخصيص يجب أن يكون أكبر من صفر")
        if alloc_amount > target["remaining"]:
            raise SettlementError(
                f"التخصيص ({alloc_amount}) يتجاوز الرصيد المتبقي للهدف ({target['remaining']}) — مرفوض"
            )
        total_allocated += alloc_amount
        resolved.append((alloc, target))

    if total_allocated > amount_foreign:
        raise SettlementError(
            f"مجموع التخصيصات ({total_allocated}) يتجاوز مبلغ التسوية ({amount_foreign}) — مرفوض"
        )

    settlement_rate = D(settlement_rate)
    booked_total_base = Decimal("0")
    raw_fx_total = Decimal("0")
    per_alloc_fx: list[Decimal] = []
    for alloc, target in resolved:
        amt = D(alloc.amount_foreign)
        booked = money(amt * target["rate"])
        newb = money(amt * settlement_rate)
        booked_total_base += booked
        fx = newb - booked
        raw_fx_total += fx
        per_alloc_fx.append(fx)

    unallocated_foreign = amount_foreign - total_allocated
    unallocated_base = money(unallocated_foreign * settlement_rate) if unallocated_foreign > 0 else Decimal("0")
    party_net_base = booked_total_base + unallocated_base

    # للعميل: قبضنا أكثر = ربح. للمورد: دفعنا أكثر = خسارة (إشارة معكوسة) — نفس اتفاقية _post_settlement الأصلية.
    fx_signed_for_report = raw_fx_total if is_receipt else -raw_fx_total

    entry = JournalEntry(
        entry_date=settlement_date,
        ref_no=_next_settlement_ref(session, kind),
        description=description,
        source_type=kind, source_id=None,
        currency_code=currency_code, exchange_rate=settlement_rate,
        status=JournalEntryStatus.POSTED,
    )

    lines: list[JournalLine] = []
    if is_receipt:
        lines.append(_jline(cash_account_id, amount_foreign, Decimal("0"), settlement_rate))
        lines.append(_jline_party(party_account_id, Decimal("0"), amount_foreign, Decimal("0"), party_net_base))
    else:
        lines.append(_jline_party(party_account_id, amount_foreign, Decimal("0"), party_net_base, Decimal("0")))
        lines.append(_jline(cash_account_id, Decimal("0"), amount_foreign, settlement_rate))

    if fx_signed_for_report != 0:
        fx_gain_acc = _get_setting(session, "default_fx_gain_account_id")
        fx_loss_acc = _get_setting(session, "default_fx_loss_account_id")
        amt = abs(fx_signed_for_report)
        if fx_signed_for_report > 0:
            lines.append(_jline_base(fx_gain_acc, Decimal("0"), amt))
        else:
            lines.append(_jline_base(fx_loss_acc, amt, Decimal("0")))

    entry.lines = lines
    if not entry.is_balanced():
        raise SettlementError("خطأ داخلي: قيد التسوية غير متوازن — لا يُرحّل")

    session.add(entry)
    session.flush()

    settlement = Settlement(
        journal_entry_id=entry.id, party_account_id=party_account_id, kind=kind,
        settlement_date=settlement_date, currency_code=currency_code,
        amount_foreign=amount_foreign, settlement_rate=settlement_rate,
        fx_amount=fx_signed_for_report,
    )
    session.add(settlement)
    session.flush()

    for (alloc, _target), fx in zip(resolved, per_alloc_fx):
        session.add(SettlementAllocation(
            settlement_id=settlement.id, invoice_id=alloc.invoice_id,
            opening_party_entry_id=alloc.opening_party_entry_id,
            amount_foreign=D(alloc.amount_foreign), fx_amount=fx,
        ))
    session.flush()
    return entry


def _next_settlement_ref(session: Session, kind: str) -> str:
    prefix = {"receipt": "JE-RCV", "payment": "JE-PAY",
              "customer_refund": "JE-CREF", "supplier_refund": "JE-SREF"}[kind]
    count = session.query(JournalEntry).filter(JournalEntry.source_type == kind).count()
    return f"{prefix}-{count + 1}"


def post_receipt(
    session: Session, invoice: Invoice, amount_foreign: Decimal, settlement_date: date,
    settlement_rate: Decimal, cash_account_id: int,
) -> JournalEntry:
    """قبض من عميل — الفاتورة يجب أن تكون SALES مرحّلة بغير نقد.
    (توقيع غير مُغيَّر عن ما قبل 3B-3 — يبني تخصيصاً واحداً ضمنياً
    فوق المحرك المُعمَّم، بلا أي تغيير مطلوب على أي من استدعاءاته
    التسعة الحالية بالمشروع)."""
    if invoice.kind not in (InvoiceKind.SALES,):
        raise SettlementError("post_receipt() لفواتير البيع فقط — استخدم post_payment() للشراء")
    party_account_id = _invoice_receivable_or_payable_account_id(session, invoice)
    return _post_settlement_multi(
        session, kind="receipt", party_account_id=party_account_id,
        amount_foreign=amount_foreign, currency_code=invoice.currency_code,
        settlement_rate=settlement_rate, settlement_date=settlement_date,
        cash_account_id=cash_account_id,
        allocations=[AllocationInput(amount_foreign=amount_foreign, invoice_id=invoice.id)],
        description=f"قبض من {invoice.party_name} — فاتورة {invoice.invoice_no}",
    )


def post_payment(
    session: Session, invoice: Invoice, amount_foreign: Decimal, settlement_date: date,
    settlement_rate: Decimal, cash_account_id: int,
) -> JournalEntry:
    """دفع لمورد — الفاتورة يجب أن تكون PURCHASE مرحّلة بغير نقد.
    (توقيع غير مُغيَّر عن ما قبل 3B-3 — نفس ملاحظة post_receipt أعلاه)."""
    if invoice.kind not in (InvoiceKind.PURCHASE,):
        raise SettlementError("post_payment() لفواتير الشراء فقط — استخدم post_receipt() للبيع")
    party_account_id = _invoice_receivable_or_payable_account_id(session, invoice)
    return _post_settlement_multi(
        session, kind="payment", party_account_id=party_account_id,
        amount_foreign=amount_foreign, currency_code=invoice.currency_code,
        settlement_rate=settlement_rate, settlement_date=settlement_date,
        cash_account_id=cash_account_id,
        allocations=[AllocationInput(amount_foreign=amount_foreign, invoice_id=invoice.id)],
        description=f"دفع لـ {invoice.party_name} — فاتورة {invoice.invoice_no}",
    )


def post_receipt_allocated(
    session: Session, party_account_id: int, amount_foreign: Decimal, currency_code: str,
    settlement_rate: Decimal, settlement_date: date, cash_account_id: int,
    allocations: list[AllocationInput],
) -> JournalEntry:
    """قبض من عميل بتخصيص متعدد الأهداف (فاتورة و/أو رصيد افتتاحي معاً) —
    القدرة الجديدة بـPhase 3B-3 (§4.2). للحالة أحادية الفاتورة البسيطة
    استخدم post_receipt() أعلاه مباشرة."""
    party_account = session.get(Account, party_account_id)
    name = party_account.name_ar if party_account else party_account_id
    return _post_settlement_multi(
        session, kind="receipt", party_account_id=party_account_id,
        amount_foreign=amount_foreign, currency_code=currency_code,
        settlement_rate=settlement_rate, settlement_date=settlement_date,
        cash_account_id=cash_account_id, allocations=allocations,
        description=f"قبض من {name}",
    )


def post_payment_allocated(
    session: Session, party_account_id: int, amount_foreign: Decimal, currency_code: str,
    settlement_rate: Decimal, settlement_date: date, cash_account_id: int,
    allocations: list[AllocationInput],
) -> JournalEntry:
    """دفع لمورد بتخصيص متعدد الأهداف — نظير post_receipt_allocated()."""
    party_account = session.get(Account, party_account_id)
    name = party_account.name_ar if party_account else party_account_id
    return _post_settlement_multi(
        session, kind="payment", party_account_id=party_account_id,
        amount_foreign=amount_foreign, currency_code=currency_code,
        settlement_rate=settlement_rate, settlement_date=settlement_date,
        cash_account_id=cash_account_id, allocations=allocations,
        description=f"دفع لـ {name}",
    )


def _post_refund(
    session: Session, *, kind: str, party_account_id: int, amount_foreign: Decimal,
    currency_code: str, refund_rate: Decimal, refund_date: date, cash_account_id: int,
) -> JournalEntry:
    """§4.3/§12/§13 بالمواصفة — القيمة الدفترية التاريخية للرصيد الفائض
    لا تُهمَل: carrying_rate متوسط مرجَّح ضمني من foreign_balance/base_balance
    (مبدأ مشابه فكرياً لـaverage cost بالمخزون، بلا أي علاقة كودية به —
    مُشتَق من JournalLine مباشرة، لا من InventoryMovement إطلاقاً)."""
    is_customer = (kind == "customer_refund")
    party_account = session.get(Account, party_account_id)
    if party_account is None:
        raise SettlementError(f"حساب غير موجود (id={party_account_id})")
    required_subtype = AccountSubtype.CUSTOMER if is_customer else AccountSubtype.SUPPLIER
    if party_account.subtype != required_subtype:
        raise SettlementError(
            f"استرداد {'عميل' if is_customer else 'مورد'} يتطلب حساب "
            f"{'عميل' if is_customer else 'مورد'} — الحساب ({party_account.name_ar}) نوعه {party_account.subtype}"
        )

    amount_foreign = D(amount_foreign)
    if amount_foreign <= 0:
        raise SettlementError("مبلغ الاسترداد يجب أن يكون أكبر من صفر")

    balance = get_party_currency_balance(session, party_account_id, currency_code)
    # قاعدة الإشارة الإلزامية (§4.3): Customer Refund يتطلب foreign_balance > 0
    # (رصيد دائن حقيقي)، Supplier Refund يتطلب foreign_balance < 0 (رصيد مدين لنا).
    if is_customer:
        if balance.foreign_balance <= 0:
            raise SettlementError(
                f"لا رصيد دائن متاح للعميل ({party_account.name_ar}) بعملة {currency_code} — "
                f"الرصيد الحالي: {balance.foreign_balance}"
            )
    else:
        if balance.foreign_balance >= 0:
            raise SettlementError(
                f"لا رصيد مدين متاح لدى المورد ({party_account.name_ar}) بعملة {currency_code} — "
                f"الرصيد الحالي: {balance.foreign_balance}"
            )

    available = abs(balance.foreign_balance)
    if amount_foreign > available:
        raise SettlementError(
            f"مبلغ الاسترداد ({amount_foreign}) يتجاوز الرصيد المتاح ({available}) بعملة {currency_code} — مرفوض"
        )

    carrying_rate = abs(D(balance.base_balance)) / abs(D(balance.foreign_balance))
    refund_rate = D(refund_rate)
    booked_base = money(amount_foreign * carrying_rate)
    new_base = money(amount_foreign * refund_rate)
    refund_fx = new_base - booked_base  # موجب: خسارة صرف عند العميل، ربح صرف عند المورد (§4.3 — الإشارة تنعكس بالمعنى، لا بالمعادلة)

    entry = JournalEntry(
        entry_date=refund_date,
        ref_no=_next_settlement_ref(session, kind),
        description=f"{'استرداد لعميل' if is_customer else 'استرداد من مورد'} {party_account.name_ar}",
        source_type=kind, source_id=None,
        currency_code=currency_code, exchange_rate=refund_rate,
        status=JournalEntryStatus.POSTED,
    )

    lines: list[JournalLine] = []
    if is_customer:
        # Dr party (يُخفِّض الدائن بقيمته الدفترية — raw=amount_foreign
        # الفعلي المُسترَد، base=booked_base الدفتري، مستقلان عمداً —
        # نفس سبب _jline_party أعلاه) / Cr Cash (النقد الفعلي بسعر اليوم)
        lines.append(_jline_party(party_account_id, amount_foreign, Decimal("0"), booked_base, Decimal("0")))
        lines.append(_jline(cash_account_id, Decimal("0"), amount_foreign, refund_rate))
        if refund_fx > 0:  # خسارة صرف للعميل
            fx_loss_acc = _get_setting(session, "default_fx_loss_account_id")
            lines.append(_jline_base(fx_loss_acc, refund_fx, Decimal("0")))
        elif refund_fx < 0:  # ربح صرف
            fx_gain_acc = _get_setting(session, "default_fx_gain_account_id")
            lines.append(_jline_base(fx_gain_acc, Decimal("0"), abs(refund_fx)))
    else:
        # Dr Cash (النقد المُستلَم من المورد) / Cr party (يُخفِّض المدين بقيمته الدفترية)
        lines.append(_jline(cash_account_id, amount_foreign, Decimal("0"), refund_rate))
        lines.append(_jline_party(party_account_id, Decimal("0"), amount_foreign, Decimal("0"), booked_base))
        if refund_fx > 0:  # ربح صرف للمؤسسة (عكس حالة العميل تماماً — تحذير Bilal الصريح)
            fx_gain_acc = _get_setting(session, "default_fx_gain_account_id")
            lines.append(_jline_base(fx_gain_acc, Decimal("0"), refund_fx))
        elif refund_fx < 0:  # خسارة صرف
            fx_loss_acc = _get_setting(session, "default_fx_loss_account_id")
            lines.append(_jline_base(fx_loss_acc, abs(refund_fx), Decimal("0")))

    entry.lines = lines
    if not entry.is_balanced():
        raise SettlementError("خطأ داخلي: قيد الاسترداد غير متوازن — لا يُرحّل")

    session.add(entry)
    session.flush()

    settlement = Settlement(
        journal_entry_id=entry.id, party_account_id=party_account_id, kind=kind,
        settlement_date=refund_date, currency_code=currency_code,
        amount_foreign=amount_foreign, settlement_rate=refund_rate,
        fx_amount=refund_fx if is_customer else -refund_fx,
    )
    session.add(settlement)
    session.flush()
    # لا SettlementAllocation لعملية Refund — لا "هدف" يُطفَأ (§4.3 بالمواصفة)
    return entry


def post_customer_refund(
    session: Session, party_account_id: int, amount_foreign: Decimal, currency_code: str,
    refund_rate: Decimal, refund_date: date, cash_account_id: int,
) -> JournalEntry:
    """رد رصيد دائن فائض لعميل — §4.3 بالمواصفة."""
    return _post_refund(session, kind="customer_refund", party_account_id=party_account_id,
                         amount_foreign=amount_foreign, currency_code=currency_code,
                         refund_rate=refund_rate, refund_date=refund_date, cash_account_id=cash_account_id)


def post_supplier_refund(
    session: Session, party_account_id: int, amount_foreign: Decimal, currency_code: str,
    refund_rate: Decimal, refund_date: date, cash_account_id: int,
) -> JournalEntry:
    """رد رصيد مدين فائض من مورد (استرداد مبلغ دفعناه زيادة) — §4.3 بالمواصفة."""
    return _post_refund(session, kind="supplier_refund", party_account_id=party_account_id,
                         amount_foreign=amount_foreign, currency_code=currency_code,
                         refund_rate=refund_rate, refund_date=refund_date, cash_account_id=cash_account_id)
