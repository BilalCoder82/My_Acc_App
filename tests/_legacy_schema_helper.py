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
