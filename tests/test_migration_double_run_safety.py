"""
tests/test_migration_double_run_safety.py
=============================================
يثبت رقمياً (عدّاد استدعاءات حقيقي، لا قراءة كود) أن
app/migrations/runner.py::apply_migrations() تُستدعى **مرة واحدة بالضبط**
لكل عميل عبر دورة حياته الكاملة — أول فتح فقط، ثم أبداً مرة أخرى — حتى
لو فُتح نفس الملف عشرات المرات لاحقاً. هذا الضمان الذي وثّقناه في رأس
runner.py (WORKFLOW.md §34) كوصف نصي، هنا نثبته رقمياً.
"""
import os, sys, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Account, AccountType
import app.migrations.alembic_runner as ar_module
from tests._legacy_schema_helper import create_legacy_client_db, seed_legacy_account

TMP = Path("/tmp/test_double_run_safety")
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True)
db_path = TMP / "client.db"

create_legacy_client_db(str(db_path))  # عميل قديم فعلي (من schema_snapshot.sql المجمَّد، لا Base.metadata الحيّة)
engine = create_engine(f"sqlite:///{db_path}")
s = sessionmaker(bind=engine)()
seed_legacy_account(str(db_path), "1101", "الصندوق")
engine.dispose()

call_count = {"n": 0}
real_apply = ar_module.apply_migrations


def counting_apply_migrations(engine):
    call_count["n"] += 1
    return real_apply(engine)


with patch.object(ar_module, "apply_migrations", side_effect=counting_apply_migrations):
    ar_module.ensure_schema_up_to_date(str(db_path))   # المرة 1: يجب أن تستدعي apply_migrations
    ar_module.ensure_schema_up_to_date(str(db_path))   # المرة 2: يجب ألا تستدعيها إطلاقاً
    ar_module.ensure_schema_up_to_date(str(db_path))   # المرة 3: نفس الشيء
    ar_module.ensure_schema_up_to_date(str(db_path))   # المرة 4: نفس الشيء

assert call_count["n"] == 1, (
    f"فشل: apply_migrations استُدعيت {call_count['n']} مرة بدل 1 — "
    f"خطر تشغيل مزدوج حقيقي"
)

shutil.rmtree(TMP, ignore_errors=True)
print(f"✅ apply_migrations() استُدعيت {call_count['n']} مرة بالضبط عبر 4 محاولات فتح متتالية — لا تشغيل مزدوج")
