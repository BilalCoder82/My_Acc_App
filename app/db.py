"""
طبقة الاتصال بقواعد البيانات
================================
registry.db  -> سجل مركزي واحد يحوي لائحة العملاء ومسارات ملفاتهم فقط
<client>.db  -> ملف منفصل كامل لكل عميل، بكامل جداول models.py

كل فتح لملف عميل يمر إجبارياً عبر apply_migrations() لضمان تحديث
الـschema بأمان دون فقدان بيانات، بغض النظر متى أُنشئ الملف.
"""

from __future__ import annotations
import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session

from app.migrations.runner import apply_migrations

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
    """يفتح ملف عميل، يطبّق أي migrations ناقصة تلقائياً، ويرجّع جلسة جاهزة."""
    path = os.path.join(DATA_DIR, "companies", db_filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    apply_migrations(engine)
    return sessionmaker(bind=engine)()
