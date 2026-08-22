"""
Balance Sheet — الميزانية العمومية
=====================================
قائمة رصيد (stock statement): تُحسب "حتى تاريخ" (as_of_date)، بعكس قائمة
الدخل المرتبطة بفترة.

نقطة محاسبية جوهرية: بما أن النظام لا يُنشئ قيد إقفال فعلي (closing entry)
يحوّل صافي الربح لحساب حقوق الملكية تلقائياً بنهاية كل فترة، فإن الأصول
لن توازي الخصوم + حقوق الملكية المسجّلة فقط — الفرق هو بالضبط صافي الربح
غير المُقفل. لذلك نحسبه كبند مستقل "أرباح الفترة الحالية (غير مُقفلة)"
ونضيفه لحقوق الملكية عرضاً، دون إنشاء أي قيد فعلي بقاعدة البيانات.

المعادلة التي يجب أن تتحقق دائماً:
    الأصول = الخصوم + حقوق الملكية المسجّلة + صافي الربح غير المُقفل
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from sqlalchemy.orm import Session

from app.models import Account
from app.reports.rollup import get_account_balance
from app.reports.income_statement import get_income_statement
from app.services.money import money


@dataclass
class BalanceSheet:
    as_of_date: date
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity_recorded: Decimal
    unclosed_net_profit: Decimal
    total_equity_with_earnings: Decimal
    is_balanced: bool


def get_balance_sheet(
    session: Session, as_of_date: date,
    assets_root_code: str = "1", liabilities_root_code: str = "2", equity_root_code: str = "3",
) -> BalanceSheet:
    assets_root = session.query(Account).filter_by(code=assets_root_code).first()
    liabilities_root = session.query(Account).filter_by(code=liabilities_root_code).first()
    equity_root = session.query(Account).filter_by(code=equity_root_code).first()

    total_assets = get_account_balance(session, assets_root, None, as_of_date) if assets_root else Decimal("0")
    total_liabilities = get_account_balance(session, liabilities_root, None, as_of_date) if liabilities_root else Decimal("0")
    total_equity_recorded = get_account_balance(session, equity_root, None, as_of_date) if equity_root else Decimal("0")

    # صافي الربح التراكمي من بداية النظام حتى as_of_date — يمثّل الأرباح
    # التي لم تُقفَل بعد لحساب حقوق الملكية عبر قيد إقفال فعلي
    income = get_income_statement(session, date(1900, 1, 1), as_of_date)
    unclosed_net_profit = income.net_profit

    total_equity_with_earnings = money(total_equity_recorded + unclosed_net_profit)

    return BalanceSheet(
        as_of_date=as_of_date,
        total_assets=money(total_assets),
        total_liabilities=money(total_liabilities),
        total_equity_recorded=money(total_equity_recorded),
        unclosed_net_profit=money(unclosed_net_profit),
        total_equity_with_earnings=total_equity_with_earnings,
        is_balanced=(money(total_assets) == money(total_liabilities + total_equity_with_earnings)),
    )
