"""Phase 3B-3 — opening_party_entries, settlement_allocations, Settlement schema change

Revision ID: d4e8f2a6c1b3
Revises: c2d6b4a9f1e7
Create Date: 2026-09-05 00:00:00.000000

راجع PHASE3B3_DESIGN_SPEC.md §7 (خطة Migration/Backfill) قبل تعديل أي
شيء هنا. القاعدة الإلزامية: لا حذف settlements.invoice_id قبل التأكد
الفعلي (assert صريح، لا افتراض) أن كل صف Settlement قديم له بالضبط صف
SettlementAllocation واحد يقابله بنفس المبلغ، وأن party_account_id/
currency_code امتلآ لكل الصفوف القديمة بلا استثناء.

جدولان جديدان (opening_party_entries، settlement_allocations) — نفس
دور OpeningBalanceEntry/OpeningInventoryEntry بـ3B-1/3B-2 (سجل تفصيلي،
لا مصدر حقيقة محاسبية). لا أعمدة جديدة على Invoice/InvoiceLine/Item/
Warehouse/InventoryMovement — هذه المرحلة لا تمسّ المخزون أو الفواتير
إطلاقاً (فقط Settlement + جدولان جديدان تماماً).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import table, column, Integer, String, Numeric, Date


revision: str = 'd4e8f2a6c1b3'
down_revision: Union[str, Sequence[str], None] = 'c2d6b4a9f1e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1) جدولان جديدان بالكامل ---
    op.create_table(
        'opening_party_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('journal_entry_id', sa.Integer(), sa.ForeignKey('journal_entries.id'), nullable=False, unique=True),
        sa.Column('party_account_id', sa.Integer(), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),  # "receivable" | "payable"
        sa.Column('reference', sa.String(length=100), nullable=False),
        sa.Column('original_amount_foreign', sa.Numeric(14, 2), nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('exchange_rate', sa.Numeric(14, 6), nullable=False, server_default='1'),
        sa.Column('amount_base', sa.Numeric(14, 2), nullable=False),
        sa.Column('opening_date', sa.Date(), nullable=False),
        sa.CheckConstraint("original_amount_foreign > 0", name="ck_opening_party_amount_positive"),
        sa.CheckConstraint("exchange_rate > 0", name="ck_opening_party_rate_positive"),
    )

    op.create_table(
        'settlement_allocations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('settlement_id', sa.Integer(), sa.ForeignKey('settlements.id'), nullable=False),
        sa.Column('invoice_id', sa.Integer(), sa.ForeignKey('invoices.id'), nullable=True),
        sa.Column('opening_party_entry_id', sa.Integer(), sa.ForeignKey('opening_party_entries.id'), nullable=True),
        sa.Column('amount_foreign', sa.Numeric(14, 2), nullable=False),
        sa.Column('fx_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.CheckConstraint(
            "(invoice_id IS NOT NULL AND opening_party_entry_id IS NULL) OR "
            "(invoice_id IS NULL AND opening_party_entry_id IS NOT NULL)",
            name="ck_settlement_allocation_exclusive_target",
        ),
        sa.CheckConstraint("amount_foreign > 0", name="ck_settlement_allocation_amount_positive"),
    )

    # --- 2) إضافة أعمدة settlements الجديدة (nullable مؤقتاً للـbackfill) ---
    with op.batch_alter_table('settlements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('currency_code', sa.String(length=3), nullable=True))
        batch_op.add_column(sa.Column(
            'party_account_id', sa.Integer(),
            sa.ForeignKey('accounts.id', name='fk_settlements_party_account_id'), nullable=True,
        ))

    # --- 3) Backfill: كل Settlement قديم (invoice_id NOT NULL بعد) يُنتِج
    #        SettlementAllocation واحد يقابله بالضبط، ويُملأ currency_code/
    #        party_account_id من فاتورته. ---
    conn = op.get_bind()
    settlements_t = table(
        'settlements', column('id', Integer), column('invoice_id', Integer),
        column('amount_foreign', Numeric), column('fx_amount', Numeric),
        column('currency_code', String), column('party_account_id', Integer),
    )
    invoices_t = table(
        'invoices', column('id', Integer), column('currency_code', String),
        column('journal_entry_id', Integer),
    )
    journal_lines_t = table(
        'journal_lines', column('id', Integer), column('entry_id', Integer), column('account_id', Integer),
    )
    settlement_allocations_t = table(
        'settlement_allocations', column('id', Integer), column('settlement_id', Integer),
        column('invoice_id', Integer), column('opening_party_entry_id', Integer),
        column('amount_foreign', Numeric), column('fx_amount', Numeric),
    )

    old_settlements = conn.execute(
        sa.select(settlements_t.c.id, settlements_t.c.invoice_id,
                  settlements_t.c.amount_foreign, settlements_t.c.fx_amount)
    ).fetchall()

    for row in old_settlements:
        invoice_row = conn.execute(
            sa.select(invoices_t.c.currency_code, invoices_t.c.journal_entry_id)
            .where(invoices_t.c.id == row.invoice_id)
        ).first()
        if invoice_row is None:
            raise RuntimeError(
                f"Backfill فشل: Settlement id={row.id} يشير لفاتورة غير موجودة "
                f"(invoice_id={row.invoice_id}) — لا يمكن المتابعة بأمان."
            )
        first_line = conn.execute(
            sa.select(journal_lines_t.c.account_id)
            .where(journal_lines_t.c.entry_id == invoice_row.journal_entry_id)
            .order_by(journal_lines_t.c.id.asc())
            .limit(1)
        ).first()
        if first_line is None:
            raise RuntimeError(
                f"Backfill فشل: فاتورة الـSettlement id={row.id} بلا أسطر قيد — حالة غير متسقة."
            )

        conn.execute(
            settlements_t.update().where(settlements_t.c.id == row.id)
            .values(currency_code=invoice_row.currency_code, party_account_id=first_line.account_id)
        )
        conn.execute(
            settlement_allocations_t.insert().values(
                settlement_id=row.id, invoice_id=row.invoice_id, opening_party_entry_id=None,
                amount_foreign=row.amount_foreign, fx_amount=row.fx_amount,
            )
        )

    # --- 4) تحقق إلزامي قبل أي حذف — لا افتراض، فحص فعلي ---
    settlements_count = conn.execute(sa.select(sa.func.count()).select_from(settlements_t)).scalar()
    allocations_count = conn.execute(sa.select(sa.func.count()).select_from(settlement_allocations_t)).scalar()
    if settlements_count != allocations_count:
        raise RuntimeError(
            f"Backfill غير مكتمل: settlements={settlements_count} لكن "
            f"settlement_allocations={allocations_count} — يجب أن يتطابقا بالضبط قبل حذف invoice_id."
        )
    null_currency = conn.execute(
        sa.select(sa.func.count()).select_from(settlements_t).where(settlements_t.c.currency_code.is_(None))
    ).scalar()
    null_party = conn.execute(
        sa.select(sa.func.count()).select_from(settlements_t).where(settlements_t.c.party_account_id.is_(None))
    ).scalar()
    if null_currency or null_party:
        raise RuntimeError(
            f"Backfill غير مكتمل: {null_currency} صف بلا currency_code، {null_party} صف بلا "
            "party_account_id — لا يجوز المتابعة لحذف invoice_id."
        )

    sum_settlements = conn.execute(sa.select(sa.func.sum(settlements_t.c.amount_foreign))).scalar() or 0
    sum_allocations = conn.execute(sa.select(sa.func.sum(settlement_allocations_t.c.amount_foreign))).scalar() or 0
    if sum_settlements != sum_allocations:
        raise RuntimeError(
            f"Backfill غير مكتمل: مجموع settlements.amount_foreign={sum_settlements} لا يطابق "
            f"مجموع settlement_allocations.amount_foreign={sum_allocations} — فقد بيانات محتمل."
        )

    # --- 5) الآن فقط: حذف invoice_id، وتثبيت الأعمدة الجديدة NOT NULL،
    #        ورفع طول kind لاستيعاب customer_refund/supplier_refund،
    #        وإضافة UNIQUE (1:1 مع journal_entry) + CHECK للقيم الموجبة
    #        (§13 Hardening — Bilal) ---
    with op.batch_alter_table('settlements', schema=None) as batch_op:
        batch_op.drop_column('invoice_id')
        batch_op.alter_column('currency_code', existing_type=sa.String(length=3), nullable=False)
        batch_op.alter_column('party_account_id', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('kind', existing_type=sa.String(length=10), type_=sa.String(length=20), nullable=False)
        batch_op.create_unique_constraint('uq_settlements_journal_entry_id', ['journal_entry_id'])
        batch_op.create_check_constraint('ck_settlement_amount_positive', 'amount_foreign > 0')
        batch_op.create_check_constraint('ck_settlement_rate_positive', 'settlement_rate > 0')


def downgrade() -> None:
    with op.batch_alter_table('settlements', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'invoice_id', sa.Integer(),
            sa.ForeignKey('invoices.id', name='fk_settlements_invoice_id'), nullable=True,
        ))

    conn = op.get_bind()
    settlements_t = table('settlements', column('id', Integer), column('invoice_id', Integer))
    settlement_allocations_t = table(
        'settlement_allocations', column('settlement_id', Integer), column('invoice_id', Integer),
    )
    rows = conn.execute(sa.select(settlement_allocations_t.c.settlement_id, settlement_allocations_t.c.invoice_id)
                         .where(settlement_allocations_t.c.invoice_id.isnot(None))).fetchall()
    for row in rows:
        conn.execute(settlements_t.update().where(settlements_t.c.id == row.settlement_id)
                     .values(invoice_id=row.invoice_id))

    with op.batch_alter_table('settlements', schema=None) as batch_op:
        batch_op.alter_column('invoice_id', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('kind', existing_type=sa.String(length=20), type_=sa.String(length=10), nullable=False)
        batch_op.drop_column('party_account_id')
        batch_op.drop_column('currency_code')

    op.drop_table('settlement_allocations')
    op.drop_table('opening_party_entries')
