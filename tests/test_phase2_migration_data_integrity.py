"""
tests/test_phase2_migration_data_integrity.py
==================================================
إغلاق Phase 2 / بند 2: سلامة بيانات الهجرة a7c4f8d1b3e2 على قاعدة عميل
واقعية — Backfill صحيح، لا reconciliation خاطئة على حسابات عامة/مجموعات،
تشغيل migration مرتين لا يفسد شيئاً، حفظ حساب جديد وإعادة فتحه (من
القرص فعلياً، لا نفس كائن Python) يحافظ على subtype/allow_reconciliation.
"""
import os, sys, sqlite3, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
from tests._legacy_schema_helper import create_legacy_client_db

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


TMP = Path("/tmp/phase2_migration_integrity")
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True)
db_path = TMP / "legacy_realistic.db"

# --- قاعدة عميل واقعية: شجرة حسابات + عملاء/موردون حقيقيون قبل أي هجرة ---
create_legacy_client_db(str(db_path))
conn = sqlite3.connect(str(db_path))
conn.execute("INSERT INTO accounts (id, code, name_ar, account_type, is_group, currency_code, is_active) "
             "VALUES (1,'1101','الصندوق','ASSET',0,'SYP',1)")
conn.execute("INSERT INTO accounts (id, code, name_ar, account_type, is_group, currency_code, is_active) "
             "VALUES (2,'1103','الذمم المدينة','ASSET',1,'SYP',1)")
conn.execute("INSERT INTO accounts (id, code, name_ar, account_type, is_group, currency_code, is_active, parent_id) "
             "VALUES (3,'1103-001','عميل حقيقي 1','ASSET',0,'SYP',1,2)")
conn.execute("INSERT INTO accounts (id, code, name_ar, account_type, is_group, currency_code, is_active, parent_id) "
             "VALUES (4,'1103-002','عميل حقيقي 2','ASSET',0,'SYP',1,2)")
conn.execute("INSERT INTO accounts (id, code, name_ar, account_type, is_group, currency_code, is_active) "
             "VALUES (5,'2101','الذمم الدائنة','LIABILITY',1,'SYP',1)")
conn.execute("INSERT INTO accounts (id, code, name_ar, account_type, is_group, currency_code, is_active, parent_id) "
             "VALUES (6,'2101-001','مورد حقيقي 1','LIABILITY',0,'SYP',1,5)")
# حساب عام غير مرتبط بـar_parent/ap_parent إطلاقاً — يجب ألا يحصل على
# customer/supplier ولا allow_reconciliation=True خطأً
conn.execute("INSERT INTO accounts (id, code, name_ar, account_type, is_group, currency_code, is_active) "
             "VALUES (7,'6201','مصروف متنوع غير مصنَّف','EXPENSE',0,'SYP',1)")
conn.execute("INSERT INTO settings (key, value) VALUES ('default_cash_account_id','1')")
conn.execute("INSERT INTO settings (key, value) VALUES ('ar_parent_account_id','2')")
conn.execute("INSERT INTO settings (key, value) VALUES ('ap_parent_account_id','5')")
conn.commit(); conn.close()

from app.migrations.alembic_runner import ensure_schema_up_to_date

def alembic_upgrade():
    """يستخدم مسار الترحيل الفعلي بالتطبيق (ensure_schema_up_to_date)
    لا `alembic upgrade head` عبر CLI مباشرة — تلك الأخيرة لا تعرف كيف
    تتعامل مع عميل قديم بلا alembic_version (تحاول إعادة تنفيذ الهجرة
    الأساسية من الصفر فتفشل بتصادم جداول موجودة أصلاً؛ stamp-then-
    upgrade هو بالضبط ما يفعله alembic_runner.py الحقيقي بالتطبيق)."""
    try:
        ensure_schema_up_to_date(str(db_path))
        return True, ""
    except Exception as e:
        return False, str(e)

r1_ok, r1_err = alembic_upgrade()
check("الهجرة نجحت على قاعدة عميل واقعية (عبر مسار التطبيق الفعلي)", r1_ok, r1_err[-500:])

conn = sqlite3.connect(str(db_path))
rows = {row[0]: row for row in conn.execute(
    "SELECT id, code, name_ar, subtype, allow_reconciliation FROM accounts")}

check("الحسابات القديمة كلها بقيت سليمة (7 حسابات، لا فقدان)", len(rows) == 7)
check("العميل الحقيقي 1: subtype=CUSTOMER + allow_reconciliation=1",
      rows[3][3] == "CUSTOMER" and rows[3][4] == 1)
check("العميل الحقيقي 2: subtype=CUSTOMER + allow_reconciliation=1",
      rows[4][3] == "CUSTOMER" and rows[4][4] == 1)
check("المورد الحقيقي 1: subtype=SUPPLIER + allow_reconciliation=1",
      rows[6][3] == "SUPPLIER" and rows[6][4] == 1)
check("الصندوق: subtype=CASH + allow_reconciliation=0 (لا تسوية للصندوق نفسه)",
      rows[1][3] == "CASH" and rows[1][4] == 0)
check("مجموعة الذمم المدينة (is_group الأب نفسه): GENERAL + allow_reconciliation=0",
      rows[2][3] == "GENERAL" and rows[2][4] == 0)
check("مجموعة الذمم الدائنة (الأب نفسه): GENERAL + allow_reconciliation=0",
      rows[5][3] == "GENERAL" and rows[5][4] == 0)
check("حساب مصروف غير مرتبط بأي Setting معروف: GENERAL + allow_reconciliation=0 (لا تخمين)",
      rows[7][3] == "GENERAL" and rows[7][4] == 0)

# --- تشغيل الهجرة مرة ثانية (idempotent) لا يفسد شيئاً ---
r2_ok, r2_err = alembic_upgrade()
check("تشغيل الهجرة مرة ثانية لا يفشل (ensure_schema_up_to_date على head فعلاً)", r2_ok, r2_err[-500:])
conn2 = sqlite3.connect(str(db_path))
rows2 = {row[0]: row for row in conn2.execute(
    "SELECT id, code, name_ar, subtype, allow_reconciliation FROM accounts")}
check("بعد التشغيل الثاني: نفس البيانات بالضبط بلا تكرار أو تغيير",
      rows == rows2, f"before={rows} after={rows2}")
conn2.close()

# --- حفظ حساب جديد وإعادة فتحه من القرص فعلياً (جلسة/اتصال جديد تماماً) ---
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Account, AccountType, AccountSubtype

engine1 = create_engine(f"sqlite:///{db_path}")
s1 = sessionmaker(bind=engine1)()
new_acc = Account(code="9999", name_ar="عميل جديد بعد الهجرة", account_type=AccountType.ASSET,
                   subtype=AccountSubtype.CUSTOMER, allow_reconciliation=True)
s1.add(new_acc); s1.commit()
new_acc_id = new_acc.id
s1.close()
engine1.dispose()  # إغلاق الاتصال بالكامل — لا اعتماد على أي كاش بالذاكرة

engine2 = create_engine(f"sqlite:///{db_path}")
s2 = sessionmaker(bind=engine2)()
reopened = s2.get(Account, new_acc_id)
check("حساب جديد بعد الهجرة: subtype يُحفَظ ويُستعاد من القرص فعلياً (اتصال جديد تماماً)",
      reopened.subtype == AccountSubtype.CUSTOMER)
check("حساب جديد بعد الهجرة: allow_reconciliation يُحفَظ ويُستعاد من القرص فعلياً",
      reopened.allow_reconciliation is True)
s2.close(); engine2.dispose()

print()
print("=" * 70)
print(f"✅ سلامة بيانات هجرة §56 على قاعدة عميل واقعية — {len(results)} تحقّقاً")
print("=" * 70)
