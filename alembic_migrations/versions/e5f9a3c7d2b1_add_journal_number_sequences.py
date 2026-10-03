"""PHASE3B4 — journal_number_sequences table (Sequence-based ref_no reservation)

Revision ID: e5f9a3c7d2b1
Revises: d4e8f2a6c1b3
Create Date: 2026-09-08 00:00:00.000000

راجع PHASE3B4_DESIGN_SPEC.md §1 قبل تعديل أي شيء هنا. جدول واحد جديد
فقط — لا تعديل على JournalEntry/JournalLine (ref_no/UNIQUE موجودان
أصلاً، لا تغيير Schema عليهما بهذه الخطوة). لا Backfill بهذه الهجرة —
البذر (seeding) لكل namespace قرار تنفيذي منفصل يُتخَذ صراحة عند نقل
كل دومين فعلياً (راجع الملاحظة الموثَّقة بتقرير الهجرة عن خطر تعدد
آليات الترقيم لنفس الـnamespace أثناء الفترة الانتقالية).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f9a3c7d2b1'
down_revision: Union[str, Sequence[str], None] = 'd4e8f2a6c1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'journal_number_sequences',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('namespace', sa.String(length=30), nullable=False),
        sa.Column('last_value', sa.Integer(), nullable=False, server_default='0'),
        sa.UniqueConstraint('namespace', name='uq_journal_number_sequences_namespace'),
    )


def downgrade() -> None:
    op.drop_table('journal_number_sequences')
