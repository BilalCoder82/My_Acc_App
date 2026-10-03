"""
Migration Runner — نمط PRAGMA user_version
=============================================
كل ملف .db (لكل عميل) يحمل رقم إصدار schema داخلي (PRAGMA user_version).
عند فتح أي ملف عميل، نطبّق فقط الترقيات (migrations) الناقصة بالترتيب،
بدون لمس البيانات الموجودة. لا نحذف أعمدة أبداً، فقط نضيف — هذا يضمن
أن فتح ملف عميل قديم بنسخة برنامج أحدث لا يفقد أي بيانات.

قاعدة صارمة: كل migration دالة idempotent (آمنة لو طُبّقت أكثر من مرة)
ولا تحتوي DROP COLUMN أو DROP TABLE إطلاقاً.

⚠️ حالة هذا الملف بعد إدخال Alembic (WORKFLOW.md §33-§34) — بالتفصيل:
  - **لماذا ما زال موجوداً؟** Alembic لا "يعرف" شيئاً عن عميل قديم أُنشئ
    قبل وجوده (لا alembic_version، Schema بأي إصدار PRAGMA سابق). هذا
    الملف هو الجسر الوحيد الذي يوصل تلك القواعد لحالة معروفة (= الأساسية
    Baseline) قبل أن يتولى Alembic الأمر.
  - **أين يُستدعى فعلياً؟** حصراً من
    `app/migrations/alembic_runner.py::ensure_schema_up_to_date()`،
    في فرع واحد فقط: عميل بلا `alembic_version` إطلاقاً (`current is
    None`). لا يُستدعى مباشرة من `app/db.py` بعد الآن.
  - **متى يتوقف استخدامه فعلياً لكل عميل؟** تلقائياً ولمرة واحدة فقط:
    أول مرة يُفتح فيها ملف ذلك العميل بعد هذا التحديث. بعدها يصبح
    `alembic_version` موجوداً، فلا يدخل الشرط `current is None` مرة أخرى
    — مضمون بنيوياً بالكود نفسه، لا اتفاقاً شفهياً (راجع
    `tests/test_migration_double_run_safety.py`).
  - **ضمان عدم التشغيل المزدوج**: بعد `apply_migrations()` مباشرة يُستدعى
    `stamp(cfg, _baseline_revision(cfg))` ثم `upgrade(cfg, "head")` —
    أي مرة قادمة `current == head` فتُرجع الدالة فوراً دون استدعاء
    `apply_migrations()` مطلقاً. مُختبَر صراحة (استدعاءان متتاليان،
    عداد استدعاءات = 1 فقط).
  - **هل يمكن حذفه لاحقاً؟** نعم، لكن فقط بعد أن يمر وقت كافٍ يضمن عدم
    وجود أي عميل حقيقي لم يُفتح بعد بهذا الإصدار من التطبيق (كل عملاء
    الإنتاج مرّوا بالتحويل مرة). قرار الحذف يحتاج موافقة صريحة موثّقة
    هنا لاحقاً بتاريخه — لا يُحذف تلقائياً أو "للتنظيف".
"""

from __future__ import annotations
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models import Base


def _get_version(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text("PRAGMA user_version")).scalar() or 0


def _set_version(engine: Engine, version: int) -> None:
    with engine.connect() as conn:
        conn.execute(text(f"PRAGMA user_version = {version}"))
        conn.commit()


def _migration_v1_initial_schema(engine: Engine) -> None:
    """الإصدار 1: إنشاء كل الجداول الأساسية إن لم تكن موجودة."""
    Base.metadata.create_all(engine)


def _migration_v2_multi_currency_and_warehouse(engine: Engine) -> None:
    """
    الإصدار 2: يضيف
      - جدول warehouses + عمود warehouse_id بـinventory_movements
      - عمودي debit_base / credit_base بـjournal_lines (تعدد العملات)
    آمن على البيانات القديمة: أي حركة مخزون قديمة بدون warehouse_id
    تُلحق تلقائياً بـ"المستودع الرئيسي"، وأي قيد قديم بدون base_amount
    يُحسب له تقريبياً = نفس القيمة الأصلية (exchange_rate=1 افتراضياً
    للقيود القديمة التي لم تكن تخزّن هذا الحقل أصلاً).
    """
    with engine.connect() as conn:
        tables = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))]

        if "warehouses" not in tables:
            conn.execute(text(
                "CREATE TABLE warehouses ("
                "id INTEGER PRIMARY KEY, name_ar VARCHAR(100), is_active BOOLEAN DEFAULT 1)"
            ))
        conn.execute(text(
            "INSERT INTO warehouses (name_ar, is_active) "
            "SELECT 'المستودع الرئيسي', 1 WHERE NOT EXISTS "
            "(SELECT 1 FROM warehouses WHERE name_ar='المستودع الرئيسي')"
        ))
        conn.commit()

        main_wh_id = conn.execute(text(
            "SELECT id FROM warehouses WHERE name_ar='المستودع الرئيسي'"
        )).scalar()

        im_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(inventory_movements)"))]
        if "warehouse_id" not in im_cols:
            conn.execute(text("ALTER TABLE inventory_movements ADD COLUMN warehouse_id INTEGER"))
            conn.execute(text(
                f"UPDATE inventory_movements SET warehouse_id = {main_wh_id} "
                "WHERE warehouse_id IS NULL"
            ))
            conn.commit()

        jl_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(journal_lines)"))]
        if "debit_base" not in jl_cols:
            conn.execute(text("ALTER TABLE journal_lines ADD COLUMN debit_base NUMERIC(14,2) DEFAULT 0"))
            conn.execute(text("UPDATE journal_lines SET debit_base = debit WHERE debit_base IS NULL OR debit_base = 0"))
            conn.commit()
        if "credit_base" not in jl_cols:
            conn.execute(text("ALTER TABLE journal_lines ADD COLUMN credit_base NUMERIC(14,2) DEFAULT 0"))
            conn.execute(text("UPDATE journal_lines SET credit_base = credit WHERE credit_base IS NULL OR credit_base = 0"))
            conn.commit()


# رتّب الدوال هنا بترتيب رقم الإصدار — لا تحذف أو تعيد ترقيم القديم أبداً
def _migration_v3_indexes_and_invoice_warehouse(engine: Engine) -> None:
    """
    الإصدار 3:
      - فهارس على journal_lines(account_id) و journal_entries(entry_date) —
        وقاية أداء رخيصة لأي حساب/تقرير مستقبلي على آلاف الصفوف، بدون
        تخزين أرقام جاهزة (لا يخالف مبدأ "التقارير تُحسب من القيود مباشرة")
      - عمود invoices.warehouse_id — كل فاتورة تنتمي لمستودع محدد الآن،
        بدل الاعتماد الضمني على "المستودع الرئيسي" فقط
    """
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_journal_lines_account_id "
            "ON journal_lines(account_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_journal_entries_entry_date "
            "ON journal_entries(entry_date)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_inventory_movements_item_warehouse "
            "ON inventory_movements(item_id, warehouse_id)"
        ))
        conn.commit()

        inv_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(invoices)"))]
        if "warehouse_id" not in inv_cols:
            conn.execute(text("ALTER TABLE invoices ADD COLUMN warehouse_id INTEGER"))
            main_wh_id = conn.execute(text(
                "SELECT id FROM warehouses WHERE name_ar='المستودع الرئيسي'"
            )).scalar()
            if main_wh_id is not None:
                conn.execute(text(
                    f"UPDATE invoices SET warehouse_id = {main_wh_id} WHERE warehouse_id IS NULL"
                ))
            conn.commit()


def _migration_v4_journal_status_and_transfers(engine: Engine) -> None:
    """
    الإصدار 4:
      - journal_entries.status (draft/posted/cancelled) — القيود القديمة
        (كلها كانت آلية من فواتير أو تعديل يدوي مباشر) تُعتبر 'posted'
        تلقائياً بالترقية، لأنها فعلياً كانت نهائية أصلاً
      - جدول stock_transfers لدعم التحويل بين مستودعات
      - عمود invoices.warehouse_id (لو لم يُضف بعد بترقية سابقة)
    """
    with engine.connect() as conn:
        je_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(journal_entries)"))]
        if "status" not in je_cols:
            conn.execute(text(
                "ALTER TABLE journal_entries ADD COLUMN status VARCHAR(20) DEFAULT 'posted'"
            ))
            conn.execute(text(
                "UPDATE journal_entries SET status = 'posted' WHERE status IS NULL"
            ))
            conn.commit()

        tables = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))]
        if "stock_transfers" not in tables:
            conn.execute(text(
                "CREATE TABLE stock_transfers ("
                "id INTEGER PRIMARY KEY, transfer_no VARCHAR(30) UNIQUE, "
                "transfer_date DATE, item_id INTEGER, from_warehouse_id INTEGER, "
                "to_warehouse_id INTEGER, quantity NUMERIC(14,3), note TEXT)"
            ))
            conn.commit()

        inv_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(invoices)"))]
        if "warehouse_id" not in inv_cols:
            conn.execute(text("ALTER TABLE invoices ADD COLUMN warehouse_id INTEGER"))
            conn.commit()


def _migration_v5_journal_line_currency(engine: Engine) -> None:
    """
    الإصدار 5: يضيف line_currency_code / line_exchange_rate بجدول journal_lines
    — يسمحان بخلط عملات مختلفة بنفس سند القيد اليدوي (مثال: تحويل دولار
    نقدي لليرة سورية بقيد واحد). القيم NULL افتراضياً لكل السطور القديمة —
    تعني "استخدم عملة القيد الافتراضية"، فلا يتأثر أي قيد موجود مسبقاً.
    """
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(journal_lines)"))]
        if "line_currency_code" not in cols:
            conn.execute(text("ALTER TABLE journal_lines ADD COLUMN line_currency_code VARCHAR(3)"))
            conn.commit()
        if "line_exchange_rate" not in cols:
            conn.execute(text("ALTER TABLE journal_lines ADD COLUMN line_exchange_rate NUMERIC(14,6)"))
            conn.commit()


MIGRATIONS = {
    1: _migration_v1_initial_schema,
    2: _migration_v2_multi_currency_and_warehouse,
    3: _migration_v3_indexes_and_invoice_warehouse,
    4: _migration_v4_journal_status_and_transfers,
    5: _migration_v5_journal_line_currency,
}


def apply_migrations(engine: Engine) -> int:
    """يطبّق كل الترقيات الناقصة بالترتيب، ويرجّع رقم الإصدار النهائي."""
    current = _get_version(engine)
    target = max(MIGRATIONS.keys())

    if current > target:
        raise RuntimeError(
            f"ملف العميل بإصدار schema ({current}) أحدث من نسخة البرنامج ({target}) — "
            "حدّث البرنامج قبل فتح هذا الملف، لا تكمل بدون تحديث."
        )

    for version in sorted(MIGRATIONS):
        if version > current:
            MIGRATIONS[version](engine)
            _set_version(engine, version)

    return target
