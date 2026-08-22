"""يثبت أن فتح ملف عميل قديم بعد إضافة migration جديدة لا يفقد بياناته."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from app.migrations.runner import apply_migrations, MIGRATIONS
from app.models import Account, AccountType
from sqlalchemy.orm import sessionmaker

TEST_DB = "test_migration.db"
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)

# محاكاة v1: عميل قديم بدون عمود notes
engine = create_engine(f"sqlite:///{TEST_DB}")
apply_migrations(engine)
Session = sessionmaker(bind=engine)
s = Session()
s.add(Account(code="1101", name_ar="الصندوق", account_type=AccountType.ASSET))
s.commit()
s.close()
print("v1: تم إنشاء حساب بالإصدار الأولي")

# الآن نفعّل v2 (إضافة عمود notes) ونطبّقها على نفس الملف القديم
def _migration_v2(engine):
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(accounts)"))]
        if "notes" not in cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN notes TEXT"))
            conn.commit()

MIGRATIONS[2] = _migration_v2
final_version = apply_migrations(engine)

s2 = Session()
acc = s2.query(Account).filter_by(code="1101").first()
print(f"إصدار الـschema النهائي: {final_version}")
print(f"الحساب القديم لسه موجود: {acc.name_ar}")
print(f"عدد الحسابات الكلي: {s2.query(Account).count()}")

with engine.connect() as conn:
    cols = [r[1] for r in conn.execute(text("PRAGMA table_info(accounts)"))]
    assert "notes" in cols, "فشل: العمود الجديد لم يُضف"
    assert acc is not None and acc.name_ar == "الصندوق", "فشل: البيانات القديمة اختفت!"

print("\n✅ نجح الاختبار: إضافة عمود جديد لم تمسح أي بيانات قديمة")
os.remove(TEST_DB)
