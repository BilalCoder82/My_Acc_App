"""
General Ledger — كشف حساب
============================
كشف حساب أي حساب من دليل الحسابات: كل حركاته مرتبة بالتاريخ، مع رصيد جارٍ.
كل الحسابات بـDecimal حصراً (app/services/money.py) — لا float.

اتجاه الرصيد الطبيعي يختلف حسب نوع الحساب: أصول/مصروفات رصيدها الطبيعي
مدين، خصوم/حقوق ملكية/إيرادات رصيدها الطبيعي دائن.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import Account, AccountType, JournalLine, JournalEntry, JournalEntryStatus
from app.services.money import D, money

DEBIT_NORMAL_TYPES = {AccountType.ASSET, AccountType.EXPENSE}


@dataclass
class LedgerRow:
    entry_date: date
    ref_no: str
    description: str | None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal


@dataclass
class AccountStatement:
    account: Account
    opening_balance: Decimal
    rows: list[LedgerRow]
    closing_balance: Decimal


def _signed_balance(account_type: AccountType, debit: Decimal, credit: Decimal) -> Decimal:
    if account_type in DEBIT_NORMAL_TYPES:
        return debit - credit
    return credit - debit


def get_account_statement(
    session: Session, account_id: int,
    date_from: date | None = None, date_to: date | None = None,
) -> AccountStatement:
    account = session.get(Account, account_id)
    if account is None:
        raise ValueError(f"حساب غير موجود: id={account_id}")
    if account.is_group:
        raise ValueError(
            f"الحساب '{account.name_ar}' حساب تجميعي — لا يقبل قيوداً مباشرة."
        )

    opening_balance = Decimal("0")
    if date_from is not None:
        opening_rows = session.execute(
            select(JournalLine, JournalEntry)
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .where(
                JournalLine.account_id == account_id,
                JournalEntry.entry_date < date_from,
                JournalEntry.status == JournalEntryStatus.POSTED,
            )
        ).all()
        for line, _entry in opening_rows:
            opening_balance += _signed_balance(account.account_type, D(line.debit_base), D(line.credit_base))

    # قاعدة صارمة: القيود غير المرحّلة (DRAFT/CANCELLED) لا تظهر بأي تقرير مالي إطلاقاً
    query = (
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .where(
            JournalLine.account_id == account_id,
            JournalEntry.status == JournalEntryStatus.POSTED,
        )
        .order_by(JournalEntry.entry_date, JournalEntry.id)
    )
    if date_from is not None:
        query = query.where(JournalEntry.entry_date >= date_from)
    if date_to is not None:
        query = query.where(JournalEntry.entry_date <= date_to)

    rows: list[LedgerRow] = []
    running = opening_balance
    for line, entry in session.execute(query).all():
        running += _signed_balance(account.account_type, D(line.debit_base), D(line.credit_base))
        rows.append(LedgerRow(
            entry_date=entry.entry_date, ref_no=entry.ref_no, description=entry.description,
            # نعرض القيمة بالعملة الأساسية دائماً — متسق مع منطق التوازن (راجع models.is_balanced)
            debit=D(line.debit_base), credit=D(line.credit_base), running_balance=money(running),
        ))

    return AccountStatement(
        account=account, opening_balance=money(opening_balance),
        rows=rows, closing_balance=money(running),
    )
