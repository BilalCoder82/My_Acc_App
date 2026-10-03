"""add opening_inventory_entries table (Phase 3B-2)

Revision ID: c2d6b4a9f1e7
Revises: b8e2d5f7a1c9
Create Date: 2026-09-04 00:00:00.000000

سجل تفصيلي (Opening Inventory Detail Record) للأرصدة الافتتاحية للمخزون
— نفس دور opening_balance_entries بـ3B-1 تماماً (سجل تفصيلي، لا Audit
Log كامل)، لا يُستبدَل به InventoryMovement/JournalEntry كمصدر حقيقة
محاسبية. inventory_movement_id إضافة عن نمط 3B-1 — يفيد هنا تحديداً
لأن كل سطر يُنتِج حركة مخزون منفصلة (بخلاف 3B-1 حيث القيد وحده كافٍ).
لا أعمدة جديدة على items/warehouses/inventory_movements — هذا الجدول
هو الهجرة الوحيدة المطلوبة لهذه المرحلة. Setting الجديد
(opening_inventory_posted_at) لا يحتاج هجرة — جدول settings عام موجود
أصلاً.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2d6b4a9f1e7'
down_revision: Union[str, Sequence[str], None] = 'b8e2d5f7a1c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'opening_inventory_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('journal_entry_id', sa.Integer(), sa.ForeignKey('journal_entries.id'), nullable=False),
        sa.Column('item_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('warehouse_id', sa.Integer(), sa.ForeignKey('warehouses.id'), nullable=False),
        sa.Column('inventory_movement_id', sa.Integer(), sa.ForeignKey('inventory_movements.id'), nullable=False),
        sa.Column('quantity', sa.Numeric(14, 3), nullable=False),
        sa.Column('unit_cost_foreign', sa.Numeric(14, 4), nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('exchange_rate', sa.Numeric(14, 6), nullable=False, server_default='1'),
        sa.Column('unit_cost_base', sa.Numeric(14, 4), nullable=False),
        sa.Column('opening_date', sa.Date(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('opening_inventory_entries')
