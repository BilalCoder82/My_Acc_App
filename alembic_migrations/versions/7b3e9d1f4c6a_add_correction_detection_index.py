"""PHASE3B5E — supporting index for Detection queries (schema-additive only)

Revision ID: 7b3e9d1f4c6a
Revises: a1b2c3d4e5f6
Create Date: 2026-09-17 00:00:00.000000

فهرس واحد فقط، بلا أي تعديل على أي جدول أو عمود موجود: `(item_id,
warehouse_id, movement_date)` على `inventory_movements`، بنفس ترتيب
الأعمدة الذي يبرره شكل استعلام الـDetection نفسه (Cluster 1) —
`WHERE item_id = ? AND warehouse_id = ? AND movement_date > ?`، حيث
المساواة تسبق المقارنة في ترتيب الفهرس. لا Detection/Analysis/
Recalculation منطق هنا إطلاقاً — schema فقط، تماماً كما فعلت
a1b2c3d4e5f6 قبلها لجداول Correction Event.
"""
from typing import Sequence, Union

from alembic import op


revision: str = '7b3e9d1f4c6a'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_inventory_movements_item_wh_date',
        'inventory_movements',
        ['item_id', 'warehouse_id', 'movement_date'],
    )


def downgrade() -> None:
    op.drop_index('ix_inventory_movements_item_wh_date', table_name='inventory_movements')
