"""
Invoice Queries — استعلامات قراءة فقط للواجهة
==================================================
الواجهة لا تكتب SQLAlchemy مباشرة إطلاقاً (قاعدة ثابتة) — حتى لعمليات
القراءة البسيطة متل "اعرض لي كل فواتير البيع". هذا الملف هو الوسيط.
"""

from __future__ import annotations
from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceKind


def list_invoices(session: Session, kind: InvoiceKind | None = None, search: str = "") -> list[Invoice]:
    query = session.query(Invoice)
    if kind is not None:
        query = query.filter(Invoice.kind == kind)
    if search:
        query = query.filter(
            (Invoice.invoice_no.ilike(f"%{search}%")) | (Invoice.party_name.ilike(f"%{search}%"))
        )
    return query.order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).all()


def list_items(session: Session, search: str = ""):
    from app.models import Item
    query = session.query(Item).filter(Item.is_active == True)  # noqa: E712
    if search:
        query = query.filter(
            (Item.sku.ilike(f"%{search}%")) | (Item.name_ar.ilike(f"%{search}%"))
        )
    return query.order_by(Item.name_ar).limit(20).all()
