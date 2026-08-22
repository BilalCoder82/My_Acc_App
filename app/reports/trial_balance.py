"""
Trial Balance — ميزان المراجعة
=================================
يجمع رصيد كل حساب فرعي (is_group=False) اللي له حركة، ويتحقق أن إجمالي
المدين = إجمالي الدائن. كل الحسابات بـDecimal حصراً.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.models import Account, JournalLine, JournalEntry
from app.services.money import money


@dataclass
class TrialBalanceRow:
    account: Account
    total_debit: Decimal
    total_credit: Decimal


@dataclass
class TrialBalanceReport:
    rows: list[TrialBalanceRow]
    total_debit: Decimal
    total_credit: Decimal
    is_balanced: bool


def get_trial_balance(session: Session, as_of_date: date | None = None) -> TrialBalanceReport:
    query = (
        select(
            JournalLine.account_id,
            func.sum(JournalLine.debit).label("total_debit"),
            func.sum(JournalLine.credit).label("total_credit"),
        )
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .group_by(JournalLine.account_id)
    )
    if as_of_date is not None:
        query = query.where(JournalEntry.entry_date <= as_of_date)

    rows: list[TrialBalanceRow] = []
    total_debit, total_credit = Decimal("0"), Decimal("0")
    for account_id, debit_sum, credit_sum in session.execute(query).all():
        account = session.get(Account, account_id)
        d = money(debit_sum) if debit_sum is not None else Decimal("0.00")
        c = money(credit_sum) if credit_sum is not None else Decimal("0.00")
        rows.append(TrialBalanceRow(account=account, total_debit=d, total_credit=c))
        total_debit += d
        total_credit += c

    rows.sort(key=lambda r: r.account.code)

    return TrialBalanceReport(
        rows=rows, total_debit=money(total_debit), total_credit=money(total_credit),
        is_balanced=(money(total_debit) - money(total_credit) == Decimal("0")),
    )
