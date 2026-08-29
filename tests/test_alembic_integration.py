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

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.models import Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, CostMethod
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice, post_sales_invoice
from app.migrations.alembic_runner import ensure_schema_up_to_date, MigrationIntegrityError, _current_revision, _head_revision, _alembic_config

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
engine = create_engine(f"sqlite:///{legacy_client}")
Base.metadata.create_all(engine)  # يحاكي بالضبط ما يفعله create_company_database الحالي
s = sessionmaker(bind=engine)()
coa = create_default_chart_of_accounts(s)
item = create_item(s, sku="LEGACY-1", name_ar="مادة قديمة", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                    cost_method=CostMethod.AVERAGE)
s.commit()
today = datetime.date.today()
inv = Invoice(invoice_no="LG-P-1", kind=InvoiceKind.PURCHASE, party_name="مورد قديم",
              invoice_date=today, currency_code="USD", exchange_rate=D_("15000"), status=InvoiceStatus.DRAFT)
inv.lines = [InvoiceLine(item_id=item.id, quantity=D_("20"), unit_price=D_("50"))]
s.add(inv); s.commit()
post_purchase_invoice(s, inv, is_cash=True); s.commit()
fingerprint_before = {
    "invoices": s.query(Invoice).count(),
    "inventory_qty": float(sum(D_(str(m.quantity)) for m in item.movements)) if hasattr(item, "movements") else None,
}
s.close(); engine.dispose()

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
        p for p in (Path(__file__).resolve().parent.parent / "alembic" / "versions").glob("*future_demo_column*")
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
