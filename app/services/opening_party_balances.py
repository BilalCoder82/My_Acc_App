"""
app/services/opening_party_balances.py
=========================================
Phase 3B-3 — الأرصدة الافتتاحية للعملاء/الموردين. راجع
PHASE3B3_DESIGN_SPEC.md قبل تعديل أي شيء هنا.

فارق متعمَّد عن post_opening_inventory()/post_opening_account_balances():
كل OpeningPartyEntry يحصل على JournalEntry مستقل به وحده — لا قيد
مُجمَّع لعدة أرصدة (§1.5 بالمواصفة) — لأن الوحدة المطلوبة هنا هي
(OpeningPartyEntry + JournalEntry + إمكانية Reverse) مستقلة تماماً؛
عكس رصيد A يجب ألا يمسّ B أو C إطلاقاً. لذلك لا Idempotency على مستوى
الشركة كلها هنا (بخلاف 3B-1/3B-2 عمداً) — كل سجل مستقل بذاته.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    Account, AccountSubtype, OpeningPartyEntry, OpeningPartyKind,
    JournalEntry, JournalEntryStatus, SettlementAllocation,
)
from app.services.money import D, money, rate as rate_
from app.services.journal_edit import add_manual_line, post_manual_entry, reverse_manual_entry
from app.services.posting import get_base_currency
from app.services.opening_balances import _get_clearing_account, OpeningBalanceError


def post_opening_party_entry(
    session: Session, party_account_id: int, kind: OpeningPartyKind, reference: str,
    amount_foreign: Decimal, opening_date: date,
    currency_code: str | None = None, exchange_rate: Decimal = Decimal("1"),
) -> OpeningPartyEntry:
    """
    §4.1 بالمواصفة. لا commit/rollback هنا — نفس عقد كل خدمات المشروع.
    currency_code=None يعني العملة الأساسية للشركة (نفس اتفاقية
    OpeningBalanceLineInput بـ3B-1).
    """
    party_account = session.get(Account, party_account_id)
    if party_account is None:
        raise OpeningBalanceError(f"حساب غير موجود (id={party_account_id})")
    if not party_account.is_active:
        raise OpeningBalanceError(f"الحساب ({party_account.name_ar}) غير نشط")

    if kind == OpeningPartyKind.RECEIVABLE and party_account.subtype != AccountSubtype.CUSTOMER:
        raise OpeningBalanceError(
            f"رصيد افتتاحي RECEIVABLE يتطلب حساباً من نوع CUSTOMER — "
            f"الحساب ({party_account.name_ar}) نوعه {party_account.subtype}"
        )
    if kind == OpeningPartyKind.PAYABLE and party_account.subtype != AccountSubtype.SUPPLIER:
        raise OpeningBalanceError(
            f"رصيد افتتاحي PAYABLE يتطلب حساباً من نوع SUPPLIER — "
            f"الحساب ({party_account.name_ar}) نوعه {party_account.subtype}"
        )

    amount_foreign = D(amount_foreign)
    if amount_foreign <= 0:
        raise OpeningBalanceError(
            f"مبلغ الرصيد الافتتاحي يجب أن يكون أكبر من صفر (المُدخَل: {amount_foreign}) — "
            "لا معنى لرصيد افتتاحي بقيمة صفر (بخلاف تكلفة المخزون، راجع §4.1.3 بالمواصفة)"
        )

    clearing_account = _get_clearing_account(session)
    base_currency = get_base_currency(session)
    effective_currency = currency_code or base_currency
    amount_base = money(amount_foreign * rate_(exchange_rate))

    is_receivable = (kind == OpeningPartyKind.RECEIVABLE)
    entry = JournalEntry(
        entry_date=opening_date,
        ref_no=_next_opening_party_ref(session),
        description=f"رصيد افتتاحي {'مدين' if is_receivable else 'دائن'} — {party_account.name_ar} ({reference})",
        source_type="opening_party_entry", currency_code=base_currency, exchange_rate=Decimal("1"),
        status=JournalEntryStatus.DRAFT,
    )
    session.add(entry)
    session.flush()

    # exchange_rate=1 عمداً: amount_base مُحوَّل للعملة الأساسية مسبقاً —
    # نفس تفادي التحويل المزدوج المُتَّبع بـ3B-2.
    if is_receivable:
        add_manual_line(session, entry, account_id=party_account_id, debit=amount_base, exchange_rate=Decimal("1"))
        add_manual_line(session, entry, account_id=clearing_account.id, credit=amount_base, exchange_rate=Decimal("1"))
    else:
        add_manual_line(session, entry, account_id=clearing_account.id, debit=amount_base, exchange_rate=Decimal("1"))
        add_manual_line(session, entry, account_id=party_account_id, credit=amount_base, exchange_rate=Decimal("1"))

    session.expire(entry, ["lines"])
    post_manual_entry(session, entry)

    opening_entry = OpeningPartyEntry(
        journal_entry_id=entry.id, party_account_id=party_account_id, kind=kind,
        reference=reference, original_amount_foreign=amount_foreign,
        currency_code=effective_currency, exchange_rate=exchange_rate,
        amount_base=amount_base, opening_date=opening_date,
    )
    session.add(opening_entry)
    session.flush()
    return opening_entry


def get_opening_party_entry_balance_due(session: Session, entry: OpeningPartyEntry) -> Decimal:
    """الرصيد المتبقي — يُحسَب ديناميكياً دائماً، لا من عمود مخزَّن
    (§1.14 بالمواصفة، نفس مبدأ get_invoice_balance_due())."""
    allocated = sum(
        (D(a.amount_foreign) for a in
         session.query(SettlementAllocation).filter_by(opening_party_entry_id=entry.id).all()),
        Decimal("0"),
    )
    return D(entry.original_amount_foreign) - allocated


def reverse_opening_party_entry(session: Session, entry: OpeningPartyEntry, reversal_date: date) -> JournalEntry:
    """يعكس رصيداً افتتاحياً واحداً بمعزل تام عن أي رصيد آخر (§1.5).
    مرفوض صراحة إن وُجد أي SettlementAllocation يشير إليه (§6/§27 —
    الرصيد أصبح مستخدَماً بتسوية فعلية، عكسه يكسر التاريخ)."""
    existing_allocation = session.query(SettlementAllocation).filter_by(opening_party_entry_id=entry.id).first()
    if existing_allocation is not None:
        raise OpeningBalanceError(
            f"لا يمكن عكس الرصيد الافتتاحي ({entry.reference}) — له تسوية (SettlementAllocation) "
            "مرتبطة به فعلياً. التصحيح يكون بحركة محاسبية جديدة، لا بعكس التاريخ."
        )
    journal_entry: JournalEntry = session.get(JournalEntry, entry.journal_entry_id)
    if journal_entry.source_type != "opening_party_entry":
        raise OpeningBalanceError(
            f"القيد {journal_entry.ref_no} ليس قيد رصيد افتتاحي لعميل/مورد "
            f"(source_type='{journal_entry.source_type}')"
        )
    return reverse_manual_entry(session, journal_entry, reversal_date,
                                 description=f"عكس الرصيد الافتتاحي — {entry.reference}")


def _next_opening_party_ref(session: Session) -> str:
    count = session.query(JournalEntry).filter(JournalEntry.source_type == "opening_party_entry").count()
    return f"JV-OPNPTY-{count + 1}"
