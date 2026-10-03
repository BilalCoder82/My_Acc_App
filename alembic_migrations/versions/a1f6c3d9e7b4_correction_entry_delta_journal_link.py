"""DL-006 — سجل ربط DeltaRecord بقيد التصحيح

Revision ID: a1f6c3d9e7b4
Revises: c9f2a4e6b8d1
Create Date: 2026-10-03 00:00:00.000000

تغيير R2 واحد: جدول جديد `correction_event_delta_journal_links` (لا بيانات
حقيقية، لا تعديل على جداول قائمة):
- UNIQUE(delta_record_id): Delta واحد لا يدخل أكثر من قيد تصحيح.
- CHECK على treatment: ثلاث قيم فقط.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a1f6c3d9e7b4'
down_revision: Union[str, Sequence[str], None] = 'c9f2a4e6b8d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'correction_event_delta_journal_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('delta_record_id', sa.Integer(),
                  sa.ForeignKey('correction_event_delta_records.id'), nullable=False),
        sa.Column('accounting_correction_id', sa.Integer(),
                  sa.ForeignKey('correction_event_accounting_corrections.id'), nullable=False),
        sa.Column('treatment', sa.String(24), nullable=False),
        sa.Column('cogs_amount', sa.Numeric(14, 4), nullable=False),
        sa.UniqueConstraint('delta_record_id', name='uq_delta_journal_link_one_per_delta'),
        sa.CheckConstraint(
            "treatment IN ('cogs_entry', 'carried_by_documents', 'transfer_no_entry')",
            name='ck_delta_journal_link_treatment',
        ),
    )
    op.create_index(
        'ix_correction_event_delta_journal_links_accounting_correction_id',
        'correction_event_delta_journal_links', ['accounting_correction_id'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_correction_event_delta_journal_links_accounting_correction_id',
        table_name='correction_event_delta_journal_links',
    )
    op.drop_table('correction_event_delta_journal_links')
