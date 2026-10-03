"""
app/migrations/alembic_runner.py
===================================
طبقة تكامل معزولة (WORKFLOW.md §33) — **غير مُستدعاة بعد من app/db.py**.
الهدف: اختبارها بمعزل تام أولاً (نقاط 4-7 من قائمة المراجعة) قبل ربطها
بمسار تشغيل التطبيق الحقيقي.

تُوحِّد ثلاث حالات لفتح أي قاعدة عميل:

  1) قاعدة فارغة تماماً (عميل جديد)         → alembic upgrade head مباشرة
  2) قاعدة قديمة (بلا alembic_version)      → apply_migrations() القديم
     أولاً (يوصلها لآخر إصدار PRAGMA معروف) ثم alembic stamp head — مرة
     واحدة فقط لكل عميل، لأن baseline migration تمثّل هذا الإصدار بالضبط.
  3) قاعدة تُدار بـAlembic أصلاً             → alembic upgrade head (يتخطى
     فوراً إن كانت already at head — فحص رخيص قبل أي محاولة ترقية)

نسخة احتياطية تلقائية **فقط** عند وجود ترقية فعلية مطلوبة (لا في الحالة
الشائعة "أصلاً عند head")، واستعادة تلقائية + رفع استثناء واضح عند الفشل
(خطة rollback — نقطة 9 بالمراجعة). لا يُحذف نظام migrations القديم
(app/migrations/runner.py) — يبقى مُستخدَماً فعلياً في الحالة 2.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from alembic.config import Config
from alembic.command import upgrade, stamp
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect

from app.migrations.runner import apply_migrations

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


class MigrationIntegrityError(Exception):
    """يُرفع عند فشل ترقية قاعدة عميل — القاعدة الأصلية تبقى سليمة (استُعيدت من نسخة احتياطية)."""


def _alembic_config(db_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    # اسم المجلد "alembic_migrations" وليس "alembic" عمداً — راجع WORKFLOW.md
    # §38: مجلد باسم "alembic" في جذر المشروع يتعارض مع حزمة alembic نفسها
    # عند غياب __init__.py (namespace package)، وهذا سبّب خطأ حقيقي فعلياً
    # (ModuleNotFoundError: No module named 'alembic.config') على جهاز
    # المستخدم رغم عمله في بيئة الاختبار هنا.
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic_migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _head_revision(cfg: Config) -> str | None:
    return ScriptDirectory.from_config(cfg).get_current_head()


def _baseline_revision(cfg: Config) -> str:
    """
    جذر سلسلة الـmigrations (down_revision=None) — وليس بالضرورة head.
    مهم: لو أُضيفت migrations جديدة بعد الأساسية مستقبلاً، تحويل عميل
    قديم يجب أن يُثبَّت (stamp) عند نقطة "ما يعادله apply_migrations
    القديم" تحديداً (= الأساسية)، ثم يُرقّى upgrade head لأي شيء تالٍ —
    وليس stamp head مباشرة، وإلا سنتخطى ترقيات حقيقية لاحقة بصمت.
    """
    bases = ScriptDirectory.from_config(cfg).get_bases()
    if len(bases) != 1:
        raise MigrationIntegrityError(
            f"سلسلة migrations غير خطية (bases={bases}) — يحتاج مراجعة يدوية قبل المتابعة"
        )
    return bases[0]


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _has_any_tables(engine: Engine) -> bool:
    return len(inspect(engine).get_table_names()) > 0


def _backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "_migration_backups"
    backup_dir.mkdir(exist_ok=True)
    stamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"{db_path.stem}_{stamp_str}.db"
    shutil.copy2(db_path, dest)
    return dest


def ensure_schema_up_to_date(db_path: str) -> None:
    """
    نقطة الدخول الوحيدة لهذه الطبقة. يجب استدعاؤها بدل (أو قبل حذف)
    apply_migrations() المباشرة في app/db.py::open_company_db().

    مبدأ التصميم (بعد إصلاح خلل اكتُشف فعلياً أثناء اختبار ملف تالف):
    كل عمليات *القراءة* (فحص وجود جداول، تحديد الإصدار الحالي) مغلَّفة
    بـtry منفصل خاص بها — لا تحتاج نسخة احتياطية لأنها لا تُعدِّل شيئاً،
    فقط تحويل أي DatabaseError لخطأ واضح النوع. عمليات *الكتابة* الفعلية
    (upgrade/stamp) فقط هي من تُسبَق بنسخة احتياطية وتُغلَّف باستعادة عند
    الفشل. هذا يمنع تحديداً الخلل الذي وقع سابقاً: قراءة تفشل خارج أي try.
    """
    path = Path(db_path)

    if not path.exists():
        # عميل جديد كلياً — لا بيانات لحمايتها، لا حاجة نسخة احتياطية
        cfg = _alembic_config(path)
        upgrade(cfg, "head")
        return

    engine = create_engine(f"sqlite:///{path}")
    cfg = _alembic_config(path)

    try:
        has_tables = _has_any_tables(engine)
    except Exception as exc:
        engine.dispose()
        raise MigrationIntegrityError(
            f"تعذّرت قراءة {path.name} — الملف قد يكون تالفاً (لم يُعدَّل شيء): {exc}"
        ) from exc

    if not has_tables:
        # ملف موجود لكن فارغ تماماً (بلا جداول) — كعميل جديد، بلا مخاطرة
        try:
            upgrade(cfg, "head")
        except Exception as exc:
            raise MigrationIntegrityError(f"فشل إنشاء schema في {path.name}: {exc}") from exc
        finally:
            engine.dispose()
        return

    try:
        current = _current_revision(engine)
        head = _head_revision(cfg)
    except Exception as exc:
        engine.dispose()
        raise MigrationIntegrityError(
            f"تعذّر تحديد إصدار {path.name} — الملف قد يكون تالفاً (لم يُعدَّل شيء): {exc}"
        ) from exc

    if current == head:
        # الحالة الشائعة — لا شيء لفعله، لا نسخة احتياطية، لا ترقية
        engine.dispose()
        return

    backup_path = _backup(path)
    try:
        if current is None:
            # حالة 2: قاعدة قديمة بلا تاريخ Alembic — نظام PRAGMA القديم
            # يبقى مسؤولاً عن الوصول لآخر إصدار معروف، ثم نُثبِّت عند نقطة
            # الأساسية تحديداً (لا head مباشرة — راجع تعليق _baseline_revision)،
            # ثم نُرقّي لأي migrations حقيقية أُضيفت بعدها لاحقاً.
            apply_migrations(engine)
            stamp(cfg, _baseline_revision(cfg))
            upgrade(cfg, "head")
        else:
            # حالة 3: تُدار بـAlembic أصلاً وتحتاج ترقية فعلية لإصدار أحدث
            upgrade(cfg, "head")

        # تحقق سلامة إلزامي بعد أي ترقية فعلية — لا نكتفي بعدم وجود Exception
        with engine.connect() as conn:
            ok = conn.exec_driver_sql("PRAGMA integrity_check;").fetchone()[0] == "ok"
        if not ok:
            raise MigrationIntegrityError("PRAGMA integrity_check فشل بعد الترقية")

    except Exception as exc:
        engine.dispose()
        shutil.copy2(backup_path, path)  # استعادة فورية — القاعدة الأصلية لا تبقى معطوبة
        raise MigrationIntegrityError(
            f"فشلت ترقية {path.name}، تمت الاستعادة من النسخة الاحتياطية "
            f"({backup_path.name}): {exc}"
        ) from exc
    finally:
        engine.dispose()
