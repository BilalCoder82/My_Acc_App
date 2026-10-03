"""add invoice.is_cash column

Revision ID: f3a1c9b2e4d0
Revises: dd7357b3550d
Create Date: 2026-09-01 00:00:00.000000

يُصلِح فجوة §53: طريقة الدفع (نقدي/آجل) كانت اختياراً عابراً بالواجهة
فقط لا يُخزَّن، فتُفقَد صامتاً عند إعادة فتح مسودة. راجع WORKFLOW.md §53
للسبب الكامل. Nullable عمداً — السجلات القديمة تبقى None، لا تخمين
بأثر رجعي لقيمة لم تُخزَّن أصلاً.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a1c9b2e4d0'
down_revision: Union[str, Sequence[str], None] = 'dd7357b3550d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # render_as_batch=True (env.py) — إلزامي لـALTER TABLE على SQLite
    with op.batch_alter_table('invoices', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_cash', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('invoices', schema=None) as batch_op:
        batch_op.drop_column('is_cash')
