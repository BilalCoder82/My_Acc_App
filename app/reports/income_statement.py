"""
Income Statement — قائمة الدخل
=================================
قائمة تدفق (flow statement): تُحسب دائماً لفترة زمنية محددة (date_from -> date_to)،
بعكس الميزانية العمومية اللي تُحسب "حتى تاريخ" (رصيد تراكمي).

صافي الربح = الإيرادات − تكلفة المبيعات − المصروفات

يعتمد على شجرة حسابات قياسية: الجذر "4" إيرادات، "5" تكلفة المبيعات،
"6" مصروفات — نفس ترقيم chart_of_accounts_template.py. لو استُخدمت
شجرة حسابات مخصّصة بترقيم مختلف، يجب تمرير جذور الحسابات صراحة.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from sqlalchemy.orm import Session

from app.models import Account
from app.reports.rollup import get_account_balance
from app.services.money import money


@dataclass
class IncomeStatement:
    date_from: date
    date_to: date
    total_revenue: Decimal
    total_cogs: Decimal
    total_expenses: Decimal
    gross_profit: Decimal
    net_profit: Decimal


def get_income_statement(
    session: Session, date_from: date, date_to: date,
    revenue_root_code: str = "4", cogs_root_code: str = "5", expenses_root_code: str = "6",
) -> IncomeStatement:
    revenue_root = session.query(Account).filter_by(code=revenue_root_code).first()
    cogs_root = session.query(Account).filter_by(code=cogs_root_code).first()
    expenses_root = session.query(Account).filter_by(code=expenses_root_code).first()

    total_revenue = get_account_balance(session, revenue_root, date_from, date_to) if revenue_root else Decimal("0")
    total_cogs = get_account_balance(session, cogs_root, date_from, date_to) if cogs_root else Decimal("0")
    total_expenses = get_account_balance(session, expenses_root, date_from, date_to) if expenses_root else Decimal("0")

    gross_profit = total_revenue - total_cogs
    net_profit = gross_profit - total_expenses

    return IncomeStatement(
        date_from=date_from, date_to=date_to,
        total_revenue=money(total_revenue), total_cogs=money(total_cogs),
        total_expenses=money(total_expenses), gross_profit=money(gross_profit),
        net_profit=money(net_profit),
    )
