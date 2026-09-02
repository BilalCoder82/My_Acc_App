"""
طبقة الاتصال بقواعد البيانات
================================
registry.db  -> سجل مركزي واحد يحوي لائحة العملاء ومسارات ملفاتهم فقط
<client>.db  -> ملف منفصل كامل لكل عميل، بكامل جداول models.py

كل فتح لملف عميل يمر إجبارياً عبر ensure_schema_up_to_date()
(app/migrations/alembic_runner.py) لضمان تحديث الـschema بأمان دون
فقدان بيانات، بغض النظر متى أُنشئ الملف أو هل كان يُدار سابقاً بنظام
PRAGMA القديم (app/migrations/runner.py) أم لا. راجع WORKFLOW.md §33.
"""

from __future__ import annotations
import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session

from app.migrations.alembic_runner import ensure_schema_up_to_date

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
REGISTRY_PATH = os.path.join(DATA_DIR, "registry.db")


class RegistryBase(DeclarativeBase):
    pass


class CompanyRecord(RegistryBase):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    db_filename = Column(String(200), nullable=False, unique=True)
    # لا قيمة افتراضية عمداً — يجب اختيارها صراحة عند إنشاء كل شركة
    base_currency = Column(String(3), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


def create_company(session: Session, name: str, db_filename: str, base_currency: str) -> CompanyRecord:
    """ينشئ سجل شركة جديد. base_currency إلزامي — لا يوجد افتراضي مخفي."""
    if not base_currency or len(base_currency) != 3:
        raise ValueError("يجب تحديد عملة أساسية صريحة مكوّنة من 3 أحرف (مثال: SYP, USD, TRY)")
    record = CompanyRecord(name=name, db_filename=db_filename, base_currency=base_currency.upper())
    session.add(record)
    session.commit()
    return record


def get_registry_session() -> Session:
    os.makedirs(DATA_DIR, exist_ok=True)
    engine = create_engine(f"sqlite:///{REGISTRY_PATH}")
    RegistryBase.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def open_company_db(db_filename: str) -> Session:
    """
    يفتح ملف عميل، يطبّق أي migrations ناقصة تلقائياً عبر
    app/migrations/alembic_runner.py (WORKFLOW.md §33)، ويرجّع جلسة جاهزة.

    نظام PRAGMA القديم (app/migrations/runner.py) لم يُحذف — ما زال
    مُستخدَماً داخلياً لعملاء قدامى ينتقلون لـAlembic لأول مرة، ولن يُحذف
    قبل مرور فترة كافية بدون مشاكل (راجع WORKFLOW.md §33.4).

    §58 (حسم base_currency — مراجعة Bilal): registry.db يبقى مصدر
    الحقيقة الوحيد القابل للتعديل لعملة الشركة الأساسية (CompanyRecord.
    base_currency) — لا نسخة ثانية مستقلة قابلة للتعديل داخل قاعدة
    الشركة نفسها (قرار Bilal الصريح: هذا يخلق احتمال اختلاف مصدرين).
    لكن محرك الترحيل (posting.py وغيره) يعمل حصراً على جلسة قاعدة
    الشركة، بلا أي وصول لـregistry.db إطلاقاً بالتصميم الحالي — لذلك
    نُزامِن قيمة registry.db إلى Settings الخاصة بقاعدة الشركة (مفتاح
    "base_currency") في كل مرة تُفتَح، كـcache للقراءة فقط يُحدَّث
    تلقائياً، لا حقلاً يُعدَّل يدوياً من داخل قاعدة الشركة. الكود
    المحاسبي (get_base_currency أدناه) يقرأ من هذا الـcache فقط، ولا
    يفترض SYP أو أي عملة أبداً — يرفع خطأ واضحاً لو غاب.
    """
    path = os.path.join(DATA_DIR, "companies", db_filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ensure_schema_up_to_date(path)
    engine = create_engine(f"sqlite:///{path}")
    session = sessionmaker(bind=engine)()
    _sync_base_currency_from_registry(session, db_filename)
    return session


def _sync_base_currency_from_registry(company_session: Session, db_filename: str) -> None:
    """يُزامِن Settings['base_currency'] بقاعدة الشركة من CompanyRecord.base_currency
    الفعلي بـregistry.db، في كل فتح — لا يُترَك ليصبح قديماً (stale) لو
    غُيِّر لاحقاً بالـregistry. لا يفشل بصمت لو لم يوجد سجل بالـregistry
    (عميل تجريبي أُنشئت قاعدته يدوياً خارج create_company مثلاً) — يترك
    Settings كما هي (فارغة)، وget_base_currency() ترفع خطأ واضحاً حينها،
    لا SYP افتراضية.
    """
    from app.models import Setting  # استيراد محلي لتفادي دورة استيراد مع models.py
    registry = get_registry_session()
    try:
        record = registry.query(CompanyRecord).filter_by(db_filename=db_filename).first()
        if record is None:
            return
        existing = company_session.query(Setting).filter_by(key="base_currency").first()
        if existing is None:
            company_session.add(Setting(key="base_currency", value=record.base_currency))
        elif existing.value != record.base_currency:
            existing.value = record.base_currency
        company_session.commit()
    finally:
        registry.close()
