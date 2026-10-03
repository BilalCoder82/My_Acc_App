"""
alembic/env.py
=================
معدَّل عن القالب الافتراضي لدعم تصميم المشروع (قاعدة SQLite منفصلة لكل
عميل، لا قاعدة واحدة ثابتة بـalembic.ini):

  alembic -x db_path=data/companies/client_001.db upgrade head

إذا لم يُمرَّر db_path، يُستخدم sqlalchemy.url من alembic.ini كافتراضي
(مفيد لتوليد migrations جديدة عبر autogenerate بدون تحديد عميل معيّن).

render_as_batch=True إلزامي لأن SQLite لا يدعم أغلب عمليات ALTER TABLE
مباشرة؛ Alembic ينفّذها عبر: إنشاء جدول جديد → نقل البيانات → حذف القديم
→ إعادة تسمية (batch mode) — موثّق رسمياً في:
https://alembic.sqlalchemy.org/en/latest/batch.html
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """يقرأ db_path من -x db_path=... إن وُجد، وإلا يستخدم alembic.ini."""
    db_path = context.get_x_argument(as_dictionary=True).get("db_path")
    if db_path:
        return f"sqlite:///{db_path}"
    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # إلزامي لـSQLite — راجع رأس الملف
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
