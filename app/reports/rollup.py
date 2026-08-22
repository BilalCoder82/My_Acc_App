"""
Account Rollup — تجميع هرمي للأرصدة
=======================================
رصيد أي حساب تجميعي (is_group=True) = مجموع أرصدة كل أبنائه (مباشرين
وغير مباشرين). رصيد أي حساب فرعي = حركاته المباشرة بدفتر الأستاذ.

هذا امتداد منفصل عن TrialBalanceService (اللي يعرض فقط الحسابات الفرعية
مباشرة) — ضروري تحديداً للقوائم الختامية اللي تحتاج "إجمالي الأصول"
كرقم واحد، لا قائمة حسابات فرعية متفرقة.

كل الحسابات بـDecimal (app/services/money.py).
"""

from __future__ import annotations
from datetime import date
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import Account, AccountType, JournalLine, JournalEntry
from app.services.money import D, money

DEBIT_NORMAL_TYPES = {AccountType.ASSET, AccountType.EXPENSE}


def _signed_balance(account_type: AccountType, debit: Decimal, credit: Decimal) -> Decimal:
    if account_type in DEBIT_NORMAL_TYPES:
        return debit - credit
    return credit - debit


def _leaf_balance(session: Session, account: Account, date_from: date | None, date_to: date | None) -> Decimal:
    query = (
        select(JournalLine)
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .where(JournalLine.account_id == account.id)
    )
    if date_from is not None:
        query = query.where(JournalEntry.entry_date >= date_from)
    if date_to is not None:
        query = query.where(JournalEntry.entry_date <= date_to)

    balance = Decimal("0")
    for line in session.execute(query).scalars().all():
        balance += _signed_balance(account.account_type, D(line.debit), D(line.credit))
    return balance


def get_account_balance(
    session: Session, account: Account,
    date_from: date | None = None, date_to: date | None = None,
) -> Decimal:
    """رصيد الحساب — رمزي/فرعي مباشر، أو مجموع كل الأبناء إن كان تجميعياً."""
    if not account.is_group:
        return money(_leaf_balance(session, account, date_from, date_to))

    children = session.query(Account).filter_by(parent_id=account.id).all()
    total = Decimal("0")
    for child in children:
        total += get_account_balance(session, child, date_from, date_to)
    return money(total)


def get_root_accounts(session: Session) -> list[Account]:
    """الحسابات بلا أب (1 الأصول، 2 الالتزامات...)."""
    return session.query(Account).filter(Account.parent_id.is_(None)).order_by(Account.code).all()
