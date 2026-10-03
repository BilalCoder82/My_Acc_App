"""
tests/_legacy_schema_helper.py
=================================
مشكلة اكتُشفت فعلياً (WORKFLOW.md §43): استخدام `Base.metadata.create_all()`
لمحاكاة "عميل قديم بلا Alembic" كان يعمل فقط طالما لم يُضَف أي نموذج
جديد لاحقاً — بمجرد إضافة `Settlement`، أصبحت المحاكاة تُنشئ جدول
settlements ضمن "القاعدة القديمة" أيضاً (لأنه جزء من Base.metadata
الحالية)، فيتصادم مع migration الجديدة التي تحاول إنشاءه من جديد.

الإصلاح: عميل قديم فعلي يُبنى من `baseline/schema_snapshot.sql` **المجمَّد
وقت تلك النسخة تحديداً** (10 جداول، بلا Settlement) — يبقى صحيحاً تاريخياً
مهما نمت Base.metadata لاحقاً، بعكس استدعاء create_all() الحي.
"""
import sqlite3
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "baseline" / "schema_snapshot.sql"


def create_legacy_client_db(db_path: str) -> None:
    """ينشئ قاعدة SQLite بالضبط بشكل عميل قديم حقيقي (10 جداول، بلا
    alembic_version وبلا settlements) — لاختبار مسار التحويل فقط.

    PRAGMA user_version يُضبَط صراحة = 5 (آخر إصدار PRAGMA معروف وقت
    Baseline v2) — لأن أي عميل حقيقي وصل فعلياً لهذا الشكل من الجداول
    يكون قد مرّ بالضرورة عبر apply_migrations() القديمة تاريخياً، فلا
    يجوز أن يبقى 0 (القيمة الافتراضية لملف SQLite جديد لم يُضبَط له شيء
    صراحة). تركه صفراً كان فجوة في هذا المساعد نفسه، اكتُشفت فعلياً عند
    إضافة نموذج Settlement — راجع WORKFLOW.md §43 للتفاصيل الكاملة."""
    sql = SNAPSHOT_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    conn.executescript(sql)
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()


def seed_legacy_account(db_path: str, code: str, name_ar: str, account_type: str = "ASSET") -> int:
    """يُدرِج حساباً واحداً بـSQL خام مطابق تماماً لأعمدة schema_snapshot.sql
    الفعلية (لا عبر ORM الحالي). يُرجع الـid.

    السبب (اكتُشف مراراً فعلياً — §53 وَ§56، كلاهما كسر هذا النمط
    بالضبط): أي عمود جديد يُضاف لنموذج Account مستقبلاً يجعل INSERT عبر
    الـORM الحالي يفشل فوراً على قاعدة عميل قديمة لا تملك ذلك العمود
    أصلاً — بصرف النظر عن أي عمود يُضاف لاحقاً، لأن هذه الدالة لا تعتمد
    على تعريف ORM الحالي إطلاقاً. استخدم هذه بدل `Account(...)` مباشرة
    أو `create_default_chart_of_accounts()` في أي اختبار يحاكي عميلاً
    قديماً قبل الهجرة.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO accounts (code, name_ar, account_type, is_group, currency_code, is_active) "
        "VALUES (?, ?, ?, 0, 'SYP', 1)",
        (code, name_ar, account_type),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def seed_legacy_chart_of_accounts(db_path: str) -> dict:
    """نسخة SQL خام مطابقة وظيفياً لـcreate_default_chart_of_accounts()،
    لكن محصَّنة ضد أي عمود يُضاف لنموذج Account/Setting مستقبلاً — لأنها
    لا تستخدم الـORM إطلاقاً. تُرجع نفس المفاتيح (cash, sales, cogs,
    fx_gain, fx_loss, ar_parent, ap_parent) لتبقى الاختبارات التي تحاكي
    "عميلاً قديماً بمخزون/فواتير حقيقية" قابلة لإعادة الاستخدام دون أي
    تعديل عند إضافة أعمدة جديدة لاحقاً — هذا بالضبط ما كسر
    test_alembic_integration.py مرتين متتاليتين بجولتين مختلفتين (§53
    ثم §56) قبل هذا الإصلاح.
    """
    conn = sqlite3.connect(db_path)
    ids = {}
    rows = [
        ("cash", "1101", "الصندوق", "ASSET"), ("bank", "1102", "البنك", "ASSET"),
        ("ar_parent", "1103", "الذمم المدينة", "ASSET"), ("inventory", "1104", "المخزون", "ASSET"),
        ("ap_parent", "2101", "الذمم الدائنة", "LIABILITY"),
        ("sales_tax", "2102", "ضريبة مبيعات مستحقة", "LIABILITY"),
        ("purchases_tax", "2103", "ضريبة مشتريات قابلة للخصم", "LIABILITY"),
        ("sales", "4101", "المبيعات", "REVENUE"), ("fx_gain", "4103", "أرباح فروقات صرف", "REVENUE"),
        ("cogs", "5101", "كلفة البضاعة المباعة", "EXPENSE"),
        ("fx_loss", "6106", "خسائر فروقات صرف", "EXPENSE"),
    ]
    for key, code, name, atype in rows:
        cur = conn.execute(
            "INSERT INTO accounts (code, name_ar, account_type, is_group, currency_code, is_active) "
            "VALUES (?, ?, ?, 0, 'SYP', 1)", (code, name, atype),
        )
        ids[key] = cur.lastrowid
    settings = [
        ("default_cash_account_id", ids["cash"]), ("default_sales_account_id", ids["sales"]),
        ("default_sales_tax_account_id", ids["sales_tax"]), ("default_purchases_tax_account_id", ids["purchases_tax"]),
        ("ar_parent_account_id", ids["ar_parent"]), ("ap_parent_account_id", ids["ap_parent"]),
        ("default_inventory_account_id", ids["inventory"]), ("default_cogs_account_id", ids["cogs"]),
        ("default_fx_gain_account_id", ids["fx_gain"]), ("default_fx_loss_account_id", ids["fx_loss"]),
    ]
    for key, value in settings:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()
    return ids
