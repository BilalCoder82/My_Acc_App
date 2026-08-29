"""
tests/test_migration_safety.py
=================================
مُعاد كتابته بالكامل (WORKFLOW.md §33.5) — النسخة القديمة كانت تختبر
runner.py بمنطق أصبح غير صالح فعلياً (تحاكي حقن migration v2 وهمية،
لكن MIGRATIONS الحقيقية تجاوزتها منذ زمن). لا يُترك Known Failure دائم؛
هذا استبدال كامل يختبر نفس الوعد الجوهري (بيانات قديمة لا تُفقد عند
إضافة migration جديدة) لكن عبر app/migrations/alembic_runner.py الفعلي،
بنفس الطريقة التي سيعمل بها المشروع فعلياً من الآن فصاعداً.

للتغطية الشاملة الكاملة (5 حالات: جديد/قديم/مستقبلي/تالف/متزامن)، راجع
tests/test_alembic_integration.py — هذا الملف يبقى مركَّزاً وسريعاً كإثبات
أساسي واحد، بروح الاختبار الأصلي.
"""
import os, sys, shutil, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.models import Base, Account, AccountType
from app.migrations.alembic_runner import ensure_schema_up_to_date, _current_revision, _head_revision, _alembic_config

TMP = Path("/tmp/test_migration_safety_new")
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True)
db_path = TMP / "legacy_v1_client.db"

# محاكاة عميل قديم قبل أي Alembic إطلاقاً (بالضبط كما تفعل
# create_company_database الحالية بـmodels.py)
engine = create_engine(f"sqlite:///{db_path}")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
s = Session()
s.add(Account(code="1101", name_ar="الصندوق", account_type=AccountType.ASSET))
s.commit()
s.close()
print("قاعدة عميل قديمة (بلا alembic_version) بها حساب حقيقي — تحاكي الوضع الحالي فعلياً")

# ننشئ migration مستقبلية حقيقية (عمود جديد) لمحاكاة تحديث لاحق للمشروع
project_root = Path(__file__).resolve().parent.parent
result = subprocess.run(
    [sys.executable, "-m", "alembic", "revision", "-m", "test add notes column"],
    cwd=str(project_root), capture_output=True, text=True,
)
new_migration = list((project_root / "alembic_migrations" / "versions").glob("*test_add_notes_column*"))[0]
content = new_migration.read_text()
content = content.replace(
    'def upgrade() -> None:\n    """Upgrade schema."""\n    pass',
    "def upgrade() -> None:\n    \"\"\"Upgrade schema.\"\"\"\n    with op.batch_alter_table('accounts', schema=None) as batch_op:\n        batch_op.add_column(sa.Column('notes', sa.Text(), nullable=True))",
).replace(
    'def downgrade() -> None:\n    """Downgrade schema."""\n    pass',
    "def downgrade() -> None:\n    \"\"\"Downgrade schema.\"\"\"\n    with op.batch_alter_table('accounts', schema=None) as batch_op:\n        batch_op.drop_column('notes')",
)
new_migration.write_text(content)

try:
    # هذا هو الاختبار الفعلي: فتح قاعدة عميل قديمة بعد وجود migration جديدة
    ensure_schema_up_to_date(str(db_path))

    engine2 = create_engine(f"sqlite:///{db_path}")
    cols = [c["name"] for c in inspect(engine2).get_columns("accounts")]
    assert "notes" in cols, "فشل: العمود الجديد لم يُضف"

    s2 = sessionmaker(bind=engine2)()
    acc = s2.query(Account).filter_by(code="1101").first()
    assert acc is not None and acc.name_ar == "الصندوق", "فشل: البيانات القديمة اختفت!"
    print(f"الحساب القديم لسه موجود: {acc.name_ar}")

    current = _current_revision(engine2)
    head = _head_revision(_alembic_config(db_path))
    assert current == head, f"لم يصل لـhead: current={current} head={head}"

    print("\n✅ نجح الاختبار: إضافة عمود جديد لعميل قديم عبر Alembic لم تمسح أي بيانات قديمة")
finally:
    new_migration.unlink()  # مؤقتة للاختبار فقط — لا تبقى بالمشروع
    shutil.rmtree(TMP, ignore_errors=True)
