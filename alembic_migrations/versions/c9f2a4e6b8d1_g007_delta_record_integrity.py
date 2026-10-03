"""G-007 — DeltaRecord integrity: DL-004 unique constraint + DL-003 exception marker

Revision ID: c9f2a4e6b8d1
Revises: 7b3e9d1f4c6a
Create Date: 2026-09-29 00:00:00.000000

ثلاثة تغييرات R2 على `correction_event_delta_records` (جدول غير مستخدَم
إنتاجياً قبل G-007، بلا بيانات حقيقية):

1. DL-004: `UNIQUE(candidate_state_id, movement_id)`.
2. DL-003: عمود `cost_basis_exception` (Boolean، افتراضي False) لوسم
   حركة OUT وصلت والرصيد السابق `Q<=0` — لا أساس تكلفة معرَّف تحت عقد
   `c(OUT)=A_{k-1}`.
3. `delta_inventory_value` تصبح قابلة لـNULL — NULL تعني "غير قابلة
   للحساب" (لا صفر، لا قيمة مخترَعة) وتُستخدَم حصراً مع
   `cost_basis_exception=True`.

`batch_alter_table` مطلوب لـSQLite (لا يدعم ALTER COLUMN/ADD CONSTRAINT
مباشرة).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c9f2a4e6b8d1'
down_revision: Union[str, Sequence[str], None] = '7b3e9d1f4c6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('correction_event_delta_records') as batch_op:
        batch_op.add_column(
            sa.Column('cost_basis_exception', sa.Boolean(), nullable=False,
                      server_default=sa.false())
        )
        batch_op.alter_column(
            'delta_inventory_value',
            existing_type=sa.Numeric(14, 4),
            nullable=True,
        )
        batch_op.create_unique_constraint(
            'uq_delta_record_one_per_movement_per_candidate',
            ['candidate_state_id', 'movement_id'],
        )


def downgrade() -> None:
    with op.batch_alter_table('correction_event_delta_records') as batch_op:
        batch_op.drop_constraint(
            'uq_delta_record_one_per_movement_per_candidate', type_='unique'
        )
        batch_op.alter_column(
            'delta_inventory_value',
            existing_type=sa.Numeric(14, 4),
            nullable=False,
        )
        batch_op.drop_column('cost_basis_exception')
