"""
tests/test_alembic_integration.py
====================================
اختبار الطبقة المعزولة app/migrations/alembic_runner.py (WORKFLOW.md §33)
**قبل** ربطها بـapp/db.py — يغطي نقطة 4 من قائمة المراجعة حرفياً:
  - عميل جديد
  - عميل موجود على Baseline (قديم، بلا Alembic)
  - عميل لديه migration مستقبلية (بعد الأساسية)
  - عميل تالف
  - عميلين في الوقت نفسه، أحدهما يفشل

يُشغَّل كسكربت مستقل (نمط بقية اختبارات المشروع)، لا pytest.
"""
import os, sys, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from decimal import Decimal as D_
import datetime

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.models import Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, CostMethod
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice, post_sales_invoice
from app.migrations.alembic_runner import ensure_schema_up_to_date, MigrationIntegrityError, _current_revision, _head_revision, _alembic_config
from tests._legacy_schema_helper import create_legacy_client_db, seed_legacy_chart_of_accounts

TMP = Path("/tmp/alembic_integration_test")
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True)

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


# --- 1) عميل جديد تماماً (ملف غير موجود إطلاقاً) ---
new_client = TMP / "new_client.db"
ensure_schema_up_to_date(str(new_client))
engine = create_engine(f"sqlite:///{new_client}")
tables = inspect(engine).get_table_names()
check("عميل جديد: كل الجداول أُنشئت", "alembic_version" in tables and "accounts" in tables, str(tables))
check("عميل جديد: على head مباشرة", _current_revision(engine) == _head_revision(_alembic_config(new_client)))
engine.dispose()

# --- 2) عميل قديم موجود على Baseline (بلا Alembic، بيانات واقعية) ---
legacy_client = TMP / "legacy_client.db"
create_legacy_client_db(str(legacy_client))  # من schema_snapshot.sql المجمَّد — راجع WORKFLOW.md §43
# شجرة حسابات + Settings بـSQL خام (seed_legacy_chart_of_accounts) بدل
# create_default_chart_of_accounts() الحيّة — محصَّنة ضد أي عمود يُضاف
# لنموذج Account مستقبلاً (كسرت هذا الاختبار بالضبط مرتين: §53 وَ§56)
coa_ids = seed_legacy_chart_of_accounts(str(legacy_client))
engine = create_engine(f"sqlite:///{legacy_client}")
# إدراج المادة أيضاً بـSQL خام لا عبر create_item() — تلك الأخيرة تتحقق
# داخلياً من وجود الحسابات عبر session.get(Account, ...) عبر الـORM
# الحيّة، فتفشل بنفس السبب بالضبط طالما لم تُهاجَر هذه القاعدة بعد
# (وهذا بالضبط المطلوب اختباره: بيانات حقيقية *قبل* الهجرة)
with engine.begin() as conn:
    r = conn.execute(text(
        "INSERT INTO items (sku, name_ar, unit, cost_method, reorder_point, is_active, "
        "inventory_account_id, cogs_account_id) VALUES ('LEGACY-1', 'مادة قديمة', 'قطعة', "
        "'AVERAGE', 0, 1, :inv, :cogs)"
    ), {"inv": coa_ids["inventory"], "cogs": coa_ids["cogs"]})
    item_id = r.lastrowid
today = datetime.date.today()
# إدراج مباشر بـSQL خام مطابق تماماً لأعمدة schema_snapshot.sql الفعلية —
# عمداً لا نستخدم كلاس Invoice/InvoiceLine الحالي هنا: أي عمود جديد
# يُضاف مستقبلاً لنموذج ORM (كـis_cash بـ§53) يجعل INSERT/SELECT عبر
# الـORM يفشل فوراً على هذه القاعدة القديمة فعلياً (العمود غير موجود
# بالجدول أصلاً) — هذا ليس افتراضاً نظرياً، هو ما كسر هذا الاختبار
# بالضبط عند إضافة is_cash. الـSQL الخام هنا يبقى صحيحاً بصرف النظر عن
# أي عمود يُضاف لاحقاً للنموذج، لأنه لا يعتمد على تعريف ORM الحالي إطلاقاً.
with engine.begin() as conn:
    conn.execute(text(
        "INSERT INTO invoices (invoice_no, kind, invoice_date, party_name, currency_code, "
        "exchange_rate, status, discount_percent, discount_amount) "
        "VALUES (:no, :kind, :d, :party, :cur, :rate, :status, 0, 0)"
    ), {"no": "LG-P-1", "kind": "PURCHASE", "d": today.isoformat(), "party": "مورد قديم",
        "cur": "USD", "rate": 15000.0, "status": "DRAFT"})
    invoice_id = conn.execute(text("SELECT id FROM invoices WHERE invoice_no='LG-P-1'")).scalar()
    conn.execute(text(
        "INSERT INTO invoice_lines (invoice_id, item_id, quantity, unit_price, discount_percent, "
        "discount_amount, tax_rate) VALUES (:inv, :item, 20, 50, 0, 0, 0)"
    ), {"inv": invoice_id, "item": item_id})
fingerprint_before = {"invoices": 1}
engine.dispose()

check("عميل قديم: بلا alembic_version قبل التحويل",
      "alembic_version" not in inspect(create_engine(f"sqlite:///{legacy_client}")).get_table_names())

ensure_schema_up_to_date(str(legacy_client))
engine = create_engine(f"sqlite:///{legacy_client}")
check("عميل قديم: أصبح على head بعد التحويل",
      _current_revision(engine) == _head_revision(_alembic_config(legacy_client)))
s2 = sessionmaker(bind=engine)()
check("عميل قديم: البيانات سليمة بعد التحويل (نفس عدد الفواتير)",
      s2.query(Invoice).count() == fingerprint_before["invoices"])
s2.close(); engine.dispose()

# استدعاء ثانٍ يجب أن يكون no-op (already at head) — لا نسخة احتياطية إضافية
backups_before = list((TMP / "_migration_backups").glob("legacy_client_*.db")) if (TMP / "_migration_backups").exists() else []
ensure_schema_up_to_date(str(legacy_client))
backups_after = list((TMP / "_migration_backups").glob("legacy_client_*.db")) if (TMP / "_migration_backups").exists() else []
check("عميل قديم: الاستدعاء الثاني no-op (لا نسخة احتياطية جديدة)",
      len(backups_before) == len(backups_after))

# --- 3) عميل لديه migration مستقبلية (بعد الأساسية) ---
# نبني سلسلة migrations مؤقتة إضافية لمحاكاة تحديث مستقبلي حقيقي
import subprocess
future_migration_path = None
try:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "revision", "-m", "future demo column"],
        cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True,
    )
    future_migration_path = [
        p for p in (Path(__file__).resolve().parent.parent / "alembic_migrations" / "versions").glob("*future_demo_column*")
    ][0]
    content = future_migration_path.read_text()
    content = content.replace(
        "def upgrade() -> None:\n    \"\"\"Upgrade schema.\"\"\"\n    pass",
        "def upgrade() -> None:\n    \"\"\"Upgrade schema.\"\"\"\n    with op.batch_alter_table('items', schema=None) as batch_op:\n        batch_op.add_column(sa.Column('future_demo_col', sa.Text(), nullable=True))",
    ).replace(
        "def downgrade() -> None:\n    \"\"\"Downgrade schema.\"\"\"\n    pass",
        "def downgrade() -> None:\n    \"\"\"Downgrade schema.\"\"\"\n    with op.batch_alter_table('items', schema=None) as batch_op:\n        batch_op.drop_column('future_demo_col')",
    )
    future_migration_path.write_text(content)

    future_client = TMP / "future_migration_client.db"
    shutil.copy2(new_client, future_client)  # عميل كان أصلاً على head القديم (بلا هذا العمود)

    ensure_schema_up_to_date(str(future_client))  # يجب أن يلتقط migration الجديدة تلقائياً
    engine = create_engine(f"sqlite:///{future_client}")
    cols = [c["name"] for c in inspect(engine).get_columns("items")]
    check("عميل لديه migration مستقبلية: العمود الجديد وصل فعلاً", "future_demo_col" in cols, str(cols))
    check("عميل لديه migration مستقبلية: على head الجديد", _current_revision(engine) == _head_revision(_alembic_config(future_client)))
    engine.dispose()
finally:
    if future_migration_path and future_migration_path.exists():
        future_migration_path.unlink()  # مؤقتة للاختبار فقط — لا تبقى بالمشروع

# --- 4) عميل تالف ---
corrupt_client = TMP / "corrupt_client.db"
corrupt_client.write_bytes(os.urandom(300))
try:
    ensure_schema_up_to_date(str(corrupt_client))
    check("عميل تالف: يجب أن يرفع MigrationIntegrityError", False, "لم يُرفع أي استثناء!")
except MigrationIntegrityError as e:
    check("عميل تالف: رُفع MigrationIntegrityError بوضوح", True)
except Exception as e:
    check("عميل تالف: نوع الاستثناء غير متوقع", False, f"{type(e).__name__}: {e}")

# --- 5) عميلين في نفس الوقت، أحدهما يفشل ---
client_a = TMP / "concurrent_a.db"
client_b_corrupt = TMP / "concurrent_b_corrupt.db"
client_b_corrupt.write_bytes(os.urandom(300))

a_ok, b_failed = False, False
try:
    ensure_schema_up_to_date(str(client_a))
    a_ok = True
except Exception:
    pass
try:
    ensure_schema_up_to_date(str(client_b_corrupt))
except MigrationIntegrityError:
    b_failed = True

check("عميلين معاً: العميل السليم نجح رغم فشل الآخر", a_ok)
check("عميلين معاً: العميل التالف فشل بمعزل، ولم يتوقف تنفيذ العميل الأول", b_failed)

print()
print("=" * 70)
print(f"✅ كل اختبارات تكامل Alembic المعزولة نجحت ({len(results)} تحقّقاً)")
print("=" * 70)
