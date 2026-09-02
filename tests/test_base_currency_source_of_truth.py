"""
tests/test_base_currency_source_of_truth.py
================================================
Phase 3A / §58: يُثبِت أن registry.db يبقى مصدر الحقيقة الوحيد
القابل للتعديل لعملة الشركة الأساسية، وأن قاعدة الشركة تحصل عليها
رسمياً وموثوقاً عند كل open_company_db() فعلي — لا SYP افتراضية،
ولا نسخة ثانية قابلة للتعديل يدوياً من داخل قاعدة الشركة.
"""
import os, sys, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path

import app.db as dbmod
from app.db import create_company, get_registry_session, open_company_db, CompanyRecord
from app.models import Setting
from app.services.posting import get_base_currency, PostingError

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


# --- بيئة معزولة تماماً (لا نلمس data/ الحقيقية) ---
TMP = Path("/tmp/base_currency_test_env")
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True)
dbmod.DATA_DIR = str(TMP)
dbmod.REGISTRY_PATH = str(TMP / "registry.db")

# =====================================================================
# 1) عملة أساسية إلزامية عند إنشاء الشركة — لا افتراضي مخفي
# =====================================================================
registry = get_registry_session()
try:
    create_company(registry, name="شركة تجربة", db_filename="test_usd.db", base_currency="")
    check("رفض إنشاء شركة بلا عملة أساسية صريحة", False, "لم يُرفَض!")
except ValueError:
    check("رفض إنشاء شركة بلا عملة أساسية صريحة", True)

company = create_company(registry, name="شركة USD", db_filename="test_usd.db", base_currency="usd")
check("العملة تُخزَّن بأحرف كبيرة دائماً (USD لا usd)", company.base_currency == "USD")
registry.close()

# =====================================================================
# 2) open_company_db الفعلي يُزامِن العملة تلقائياً من registry.db
# =====================================================================
session = open_company_db("test_usd.db")
check("Settings['base_currency'] بقاعدة الشركة = USD فعلياً بعد الفتح الأول",
      get_base_currency(session) == "USD")
session.close()

# =====================================================================
# 3) لا "SYP" مخفية بأي مكان — get_base_currency ترجع القيمة الحقيقية
#    من registry.db، ليست SYP افتراضية لشركة عملتها الفعلية غير SYP
# =====================================================================
check("العملة المُستعادة ليست SYP الافتراضية القديمة (تعكس الشركة الحقيقية)",
      get_base_currency(open_company_db("test_usd.db")) != "SYP")

# =====================================================================
# 4) تغيير العملة بـregistry.db لاحقاً (تصحيح إداري) ينعكس تلقائياً
#    عند إعادة الفتح — لا يبقى الـcache قديماً (stale) للأبد
# =====================================================================
registry2 = get_registry_session()
record = registry2.query(CompanyRecord).filter_by(db_filename="test_usd.db").first()
record.base_currency = "EUR"
registry2.commit()
registry2.close()

session2 = open_company_db("test_usd.db")
check("تغيير العملة بـregistry.db ينعكس تلقائياً عند إعادة فتح قاعدة الشركة (EUR الآن)",
      get_base_currency(session2) == "EUR")
session2.close()

# =====================================================================
# 5) قاعدة شركة "يتيمة" (لا سجل لها بالـregistry إطلاقاً — محاكاة قاعدة
#    أُنشئت يدوياً خارج create_company) لا تحصل على SYP افتراضية صامتة
# =====================================================================
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base
orphan_path = TMP / "companies" / "orphan.db"
orphan_path.parent.mkdir(parents=True, exist_ok=True)
orphan_engine = create_engine(f"sqlite:///{orphan_path}")
Base.metadata.create_all(orphan_engine)
orphan_session = sessionmaker(bind=orphan_engine)()
try:
    get_base_currency(orphan_session)
    check("قاعدة يتيمة بلا سجل registry: get_base_currency ترفض بوضوح (لا SYP صامتة)", False, "لم تُرفَض!")
except PostingError as e:
    check("قاعدة يتيمة بلا سجل registry: get_base_currency ترفض بوضوح (لا SYP صامتة)", True)
orphan_session.close()

# =====================================================================
# 6) لا نسخة ثانية قابلة للتعديل يدوياً من داخل قاعدة الشركة — تعديل
#    Settings['base_currency'] يدوياً من داخل قاعدة الشركة يُستبدَل
#    تلقائياً بقيمة registry.db الحقيقية عند الفتح التالي (registry
#    يبقى الحَكَم الوحيد، لا الكتابة المباشرة على قاعدة الشركة)
# =====================================================================
session3 = open_company_db("test_usd.db")
tampered = session3.get(Setting, "base_currency")
tampered.value = "TRY"  # محاولة تلاعب يدوي مباشر بقاعدة الشركة
session3.commit()
session3.close()

session4 = open_company_db("test_usd.db")
check("تعديل يدوي مباشر على cache قاعدة الشركة يُستبدَل تلقائياً بقيمة registry.db الحقيقية (EUR لا TRY)",
      get_base_currency(session4) == "EUR")
session4.close()

shutil.rmtree(TMP)

print()
print("=" * 70)
print(f"✅ حسم base_currency (registry.db مصدر حقيقة وحيد + مزامنة تلقائية) — {len(results)} تحقّقاً")
print("=" * 70)
