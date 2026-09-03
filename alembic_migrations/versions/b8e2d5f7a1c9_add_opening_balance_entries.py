"""add opening_balance_entries table (Phase 3B-1)

Revision ID: b8e2d5f7a1c9
Revises: a7c4f8d1b3e2
Create Date: 2026-09-02 00:00:00.000000

سجل تفصيلي (Opening Balance Detail Record) للأرصدة الافتتاحية للحسابات
— ليس Audit Log كاملاً (لا created_by/created_at/scope id)، لا يُستبدَل
به JournalEntry/JournalLine (القيد الفعلي مصدر الحقيقة المحاسبية
دائماً)، هذا فقط يحفظ المُدخَل الأصلي كما أدخله المستخدم. Settings
الجديدة (opening_balance_clearing_account_id،
opening_balances_accounts_posted_at) لا تحتاج هجرة — Setting جدول
key-value عام موجود أصلاً.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8e2d5f7a1c9'
down_revision: Union[str, Sequence[str], None] = 'a7c4f8d1b3e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'opening_balance_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('journal_entry_id', sa.Integer(), sa.ForeignKey('journal_entries.id'), nullable=False),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('debit_foreign', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('credit_foreign', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('exchange_rate', sa.Numeric(14, 6), nullable=False, server_default='1'),
        sa.Column('base_equivalent', sa.Numeric(14, 2), nullable=False),
        sa.Column('opening_date', sa.Date(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('opening_balance_entries')
