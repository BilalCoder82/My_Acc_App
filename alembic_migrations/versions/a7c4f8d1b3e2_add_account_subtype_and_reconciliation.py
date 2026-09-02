"""add account.subtype and account.allow_reconciliation (§56)

Revision ID: a7c4f8d1b3e2
Revises: f3a1c9b2e4d0
Create Date: 2026-09-01 03:00:00.000000

يُنفِّذ قرار Bilal: تصنيف عمل صريح للحساب (عميل/مورد/صندوق/بنك/
مصروف/إيراد/عام/أخرى) مستقل عن account_type، مع allow_reconciliation
كحقل صريح منفصل تتحقق منه الخدمة (لا account_type ولا رقم الحساب).

Backfill حقيقي لقواعد العملاء الحاليين — ليس عموداً فارغاً يُترَك
للمستخدم يملأه يدوياً لكل حساب: نقرأ Settings الموجودة فعلياً
(default_cash_account_id, ar_parent_account_id, ...) ونصنِّف الحسابات
المعروفة تلقائياً، بالضبط كما فعل create_default_chart_of_accounts()
لعميل جديد. حسابات لم تُذكَر في Settings (حسابات يدوية أضافها
المستخدم بنفسه) تبقى GENERAL/False — لا تخمين لتصنيفها.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import table, column, Integer, String, Boolean


revision: str = 'a7c4f8d1b3e2'
down_revision: Union[str, Sequence[str], None] = 'f3a1c9b2e4d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('subtype', sa.String(length=10), nullable=False, server_default='GENERAL'))
        batch_op.add_column(sa.Column('allow_reconciliation', sa.Boolean(), nullable=False, server_default='0'))

    conn = op.get_bind()
    accounts_t = table('accounts', column('id', Integer), column('subtype', String), column('allow_reconciliation', Boolean))
    settings_t = table('settings', column('key', String), column('value', String))

    def setting(key: str) -> int | None:
        row = conn.execute(sa.select(settings_t.c.value).where(settings_t.c.key == key)).first()
        return int(row[0]) if row and row[0] else None

    # صندوق/بنك: الصندوق الافتراضي معروف من الإعدادات فعلياً؛ لا إعداد
    # منفصل مسجَّل لحساب "البنك" تحديداً بالنسخة الحالية، فيبقى GENERAL
    # ما لم يُصنَّف يدوياً لاحقاً من بطاقة الحساب — لا تخمين هنا.
    cash_id = setting('default_cash_account_id')
    if cash_id:
        conn.execute(accounts_t.update().where(accounts_t.c.id == cash_id)
                     .values(subtype='CASH', allow_reconciliation=False))

    sales_id = setting('default_sales_account_id')
    if sales_id:
        conn.execute(accounts_t.update().where(accounts_t.c.id == sales_id)
                     .values(subtype='INCOME'))

    cogs_id = setting('default_cogs_account_id')
    if cogs_id:
        conn.execute(accounts_t.update().where(accounts_t.c.id == cogs_id)
                     .values(subtype='EXPENSE'))

    for fx_key in ('default_fx_gain_account_id', 'default_fx_loss_account_id'):
        acc_id = setting(fx_key)
        if acc_id:
            conn.execute(accounts_t.update().where(accounts_t.c.id == acc_id).values(subtype='OTHER'))

    # كل الحسابات الفرعية تحت ar_parent/ap_parent هي عملاء/موردون
    # فعليون (أُنشئت جميعها عبر get_or_create_party_account فقط، لا
    # طريقة أخرى بالكود الحالي لإضافة حساب تحتهما) — تُصنَّف
    # CUSTOMER/SUPPLIER وتُفعَّل التسوية تلقائياً، مطابقةً لما تفعله
    # get_or_create_party_account للحسابات الجديدة من الآن فصاعداً.
    ar_parent_id = setting('ar_parent_account_id')
    if ar_parent_id:
        conn.execute(accounts_t.update().where(accounts_t.c.id.in_(
            sa.select(sa.text('id')).select_from(sa.text('accounts')).where(sa.text('parent_id = :p')).params(p=ar_parent_id)
        )).values(subtype='CUSTOMER', allow_reconciliation=True))
    ap_parent_id = setting('ap_parent_account_id')
    if ap_parent_id:
        conn.execute(accounts_t.update().where(accounts_t.c.id.in_(
            sa.select(sa.text('id')).select_from(sa.text('accounts')).where(sa.text('parent_id = :p')).params(p=ap_parent_id)
        )).values(subtype='SUPPLIER', allow_reconciliation=True))


def downgrade() -> None:
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_column('allow_reconciliation')
        batch_op.drop_column('subtype')
