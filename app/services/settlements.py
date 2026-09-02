"""
app/services/settlements.py
==============================
القبض (Receipt) والدفع (Payment) وتسوية الفواتير — راجع WORKFLOW.md §42
للقواعد المحاسبية الكاملة قبل تعديل أي شيء هنا.

قاعدة التصميم الأساسية: كل تسوية = (amount_foreign, settlement_rate)
بعملة الفاتورة نفسها. القيمة الأساسية الفعلية = amount_foreign × rate،
بصرف النظر عن العملة الفعلية للنقدية المستلمة. فرق الصرف يُحسَب بمقارنة
هذه القيمة بما كان مسجَّلاً وقت الفاتورة، ويُرحَّل لحساب ربح أو خسارة
صرف منفصل (لا حساب مشترك) حسب الإشارة، أبداً كليهما معاً بنفس القيد.
"""
from __future__ import annotations
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceKind, InvoiceStatus, JournalEntry, JournalEntryStatus, Settlement, Account, AccountSubtype
from app.services.money import D, money
from app.services.invoice_calc import compute_invoice_totals
from app.services.posting import _jline, _jline_base, _get_setting


class SettlementError(Exception):
    pass


def _invoice_receivable_or_payable_account_id(session: Session, invoice: Invoice) -> int:
    """
    الحساب الذي استُخدم فعلياً كطرف مقابل عند ترحيل الفاتورة (عميل أو
    مورد)، مأخوذ من السطر الأول لقيدها الفعلي — لا بإعادة استنتاجه من
    party_name (قد لا يطابق الحساب الفعلي المُستخدَم لو تغيّر الإعداد
    لاحقاً). إن كان هذا الحساب هو الصندوق الافتراضي نفسه، فالفاتورة
    نقدية أصلاً ولا رصيد مستحق للتسوية.

    §56 (مُصحَّح — راجع مراجعة Bilal التالية): التحقق هنا شرطان معاً، لا
    شرط واحد — يطابق حرفياً تصميمه الأصلي ("عند اختيار حساب: إذا كان
    Customer/Supplier وallow_reconciliation=True يظهر تسوية الفواتير").
    الإصدار السابق كان يتحقق من allow_reconciliation فقط ويتجاهل
    subtype تماماً — ثغرة حقيقية: كانت تسمح (نظرياً) بتفعيل التسوية على
    حساب Cash/Expense لو فُعِّل allow_reconciliation عليه خطأً، ولا
    تُطبِّق مطلب Bilal الصريح لاحقاً "تغيير subtype من Customer إلى
    General يجب أن ينعكس على صلاحية التسوية". الآن: subtype يجب أن يكون
    CUSTOMER أو SUPPLIER تحديداً، وallow_reconciliation=True معاً —
    كلاهما إلزامي، ولا اعتماد على account_type ولا رقم الحساب مطلقاً.
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
    """الرصيد المستحق بعملة الفاتورة نفسها — يُحسَب ديناميكياً دائماً،
    لا من حقل مخزَّن (راجع WORKFLOW.md §42.1).

    قرار §52 (بعد مراجعة Bilal): invariant الحالة (POSTED فقط) يُفرَض
    هنا صراحة، وليس تركه لكل مستدعٍ. السبب: "رصيد مستحق" مفهوم لا معنى
    له فعلياً قبل الترحيل — لا قيد محاسبي وُلِد بعد، فلا "رصيد" حقيقي
    ليُحسَب أصلاً، مهما كان رقم totals.grand_total صحيحاً حسابياً. هذا
    يطابق تماماً السلوك الموجود مسبقاً بـ_invoice_receivable_or_payable_account_id
    أعلاه (ترفض invoice.journal_entry_id is None بنفس المنطق) — كان
    غيابه هنا فجوة اتساق حقيقية اكتُشفت بالاختبار، لا تصميماً مقصوداً.
    كل مستدعٍ (UI أو خدمة) يحصل على هذا الضمان تلقائياً الآن، بدل
    الاعتماد على كل مستدعٍ ليتحقق بنفسه.
    """
    if invoice.status != InvoiceStatus.POSTED:
        raise SettlementError(
            f"الفاتورة {invoice.invoice_no} غير مرحّلة (POSTED) — لا مفهوم لرصيد مستحق قبل الترحيل"
        )
    totals = compute_invoice_totals(invoice)
    return totals.grand_total - _sum_settlements(session, invoice.id)


def _sum_settlements(session: Session, invoice_id: int) -> Decimal:
    rows = session.query(Settlement).filter_by(invoice_id=invoice_id).all()
    return sum((D(r.amount_foreign) for r in rows), Decimal("0"))


def _post_settlement(
    session: Session, invoice: Invoice, amount_foreign: Decimal, settlement_date: date,
    settlement_rate: Decimal, cash_account_id: int, *, kind: str,
) -> JournalEntry:
    if invoice.status == InvoiceStatus.CANCELLED:
        raise SettlementError(f"الفاتورة {invoice.invoice_no} ملغاة — لا يجوز تسويتها")
    if invoice.status != InvoiceStatus.POSTED:
        raise SettlementError(f"الفاتورة {invoice.invoice_no} غير مرحّلة بعد — لا يجوز تسويتها")

    counter_account_id = _invoice_receivable_or_payable_account_id(session, invoice)
    balance_due = get_invoice_balance_due(session, invoice)
    amount_foreign = D(amount_foreign)
    if amount_foreign <= 0:
        raise SettlementError("مبلغ التسوية يجب أن يكون أكبر من صفر")
    if amount_foreign > balance_due:
        raise SettlementError(
            f"مبلغ التسوية ({amount_foreign}) يتجاوز الرصيد المستحق ({balance_due}) — مرفوض"
        )

    settlement_rate = D(settlement_rate)
    new_base_value = money(amount_foreign * settlement_rate)
    booked_base_value = money(amount_foreign * D(invoice.exchange_rate))
    raw_fx_diff = new_base_value - booked_base_value  # موجب = قبضنا/دفعنا أكثر مما سُجِّل أصلاً

    is_receivable = (kind == "receipt")
    # للعميل: قبضنا أكثر = ربح. للمورد: دفعنا أكثر = خسارة (إشارة معكوسة).
    fx_signed_for_report = raw_fx_diff if is_receivable else -raw_fx_diff

    entry = JournalEntry(
        entry_date=settlement_date,
        ref_no=_next_settlement_ref(session, kind),
        description=f"{'قبض من' if is_receivable else 'دفع لـ'} {invoice.party_name} — فاتورة {invoice.invoice_no}",
        source_type=kind, source_id=invoice.id,
        currency_code=invoice.currency_code, exchange_rate=settlement_rate,
        status=JournalEntryStatus.POSTED,
    )

    lines = []
    # سطر الصندوق: المبلغ الخام = amount_foreign بعملة الفاتورة، وسعر
    # التحويل الخاص بهذا السطر = settlement_rate (سعر يوم التسوية الفعلي)
    # — استخدام _jline الصحيح: raw × rate خاص بهذا السطر تحديداً.
    #
    # سطر العميل/المورد المقابل: نفس amount_foreign الخام، لكن يُقفَل
    # بسعر الفاتورة الأصلي (invoice.exchange_rate) — لأنه يُطفئ جزءاً من
    # التزام مُسجَّل أصلاً بذلك السعر بالضبط، لا بسعر اليوم.
    if is_receivable:
        lines.append(_jline(cash_account_id, amount_foreign, Decimal("0"), settlement_rate))
        lines.append(_jline(counter_account_id, Decimal("0"), amount_foreign, D(invoice.exchange_rate)))
    else:
        lines.append(_jline(counter_account_id, amount_foreign, Decimal("0"), D(invoice.exchange_rate)))
        lines.append(_jline(cash_account_id, Decimal("0"), amount_foreign, settlement_rate))

    if fx_signed_for_report != 0:
        fx_gain_acc = _get_setting(session, "default_fx_gain_account_id")
        fx_loss_acc = _get_setting(session, "default_fx_loss_account_id")
        amt = abs(fx_signed_for_report)
        # مبلغ فرق الصرف بالعملة الأساسية مباشرة (ناتج طرح، لا "عملة
        # أصلية" منفصلة ذات معنى له) — _jline_base هي الصحيحة هنا، تماماً
        # كحالة COGS الموثَّقة بتعليق _jline_base نفسها.
        if fx_signed_for_report > 0:  # ربح
            lines.append(_jline_base(fx_gain_acc, Decimal("0"), amt))
        else:  # خسارة
            lines.append(_jline_base(fx_loss_acc, amt, Decimal("0")))

    entry.lines = lines
    if not entry.is_balanced():
        raise SettlementError("خطأ داخلي: قيد التسوية غير متوازن — لا يُرحّل")

    session.add(entry)
    session.flush()

    settlement = Settlement(
        invoice_id=invoice.id, journal_entry_id=entry.id, kind=kind,
        settlement_date=settlement_date, amount_foreign=amount_foreign,
        settlement_rate=settlement_rate, fx_amount=fx_signed_for_report,
    )
    session.add(settlement)
    session.flush()
    return entry


def _next_settlement_ref(session: Session, kind: str) -> str:
    prefix = "JE-RCV" if kind == "receipt" else "JE-PAY"
    count = session.query(JournalEntry).filter(JournalEntry.source_type == kind).count()
    return f"{prefix}-{count + 1}"


def post_receipt(
    session: Session, invoice: Invoice, amount_foreign: Decimal, settlement_date: date,
    settlement_rate: Decimal, cash_account_id: int,
) -> JournalEntry:
    """قبض من عميل — الفاتورة يجب أن تكون SALES مرحّلة بغير نقد."""
    if invoice.kind not in (InvoiceKind.SALES,):
        raise SettlementError("post_receipt() لفواتير البيع فقط — استخدم post_payment() للشراء")
    return _post_settlement(session, invoice, amount_foreign, settlement_date, settlement_rate,
                             cash_account_id, kind="receipt")


def post_payment(
    session: Session, invoice: Invoice, amount_foreign: Decimal, settlement_date: date,
    settlement_rate: Decimal, cash_account_id: int,
) -> JournalEntry:
    """دفع لمورد — الفاتورة يجب أن تكون PURCHASE مرحّلة بغير نقد."""
    if invoice.kind not in (InvoiceKind.PURCHASE,):
        raise SettlementError("post_payment() لفواتير الشراء فقط — استخدم post_receipt() للبيع")
    return _post_settlement(session, invoice, amount_foreign, settlement_date, settlement_rate,
                             cash_account_id, kind="payment")
